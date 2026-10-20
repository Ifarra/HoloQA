"""Provider-neutral orchestration for coding agents that use HoloQA.

This layer launches an agent; it never interprets the agent's prose as a test
result. The only release decision returned here comes from the HoloQA run.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from functools import wraps
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from holoqa import browser as browser_module
from holoqa.storage import atomic_json, file_lock
from holoqa import plan as plan_module
from holoqa import run as run_module


class AgentError(ValueError):
    """A coding-agent wrapper request could not be started safely."""


PROVIDERS = ("codex", "claude", "opencode")
MODES = ("supervised", "unattended")
MANIFEST_FILE = "agent.json"
EVENTS_FILE = "agent-events.jsonl"
PROMPT_FILE = "agent-prompt.md"
_DEFER_BROWSER_CLEANUP: ContextVar[bool] = ContextVar("defer_browser_cleanup", default=False)


#: What "unattended" actually means for each provider. These differ enough that
#: a user choosing it deserves to know which they are getting: Codex approves
#: tool calls for itself, Claude denies anything not explicitly allowed, and
#: OpenCode auto-approves. Treating them as one setting is how a denied login
#: gets recorded as an application defect.
UNATTENDED_MEANING = {
    "codex": (
        "approves its own tool calls in a workspace-write sandbox "
        "(--approve-for-me); never the unrestricted bypass flag"
    ),
    "claude": (
        "denies anything that would prompt, and is additionally granted only "
        "mcp__holoqa__* and Bash(agent-browser:*) (--allowedTools)"
    ),
    "opencode": "auto-approves tool calls (--auto)",
}


@dataclass(frozen=True)
class AgentRunRequest:
    provider: str
    cwd: Path
    mode: str = "supervised"
    model: str = ""
    retain_events: bool = False
    desktop_run: bool = False
    output_callback: Callable[[str, str], None] | None = field(
        default=None, repr=False, compare=False
    )
    cancel_event: threading.Event | None = field(
        default=None, repr=False, compare=False
    )


@dataclass
class LaunchSpec:
    command: list[str]
    env: dict[str, str]
    config_path: Path


def workflow_prompt(plan_path: Path, run_dir: Path, *, resume: bool = False) -> str:
    continuation = (
        "This is a resumed run. Read its current status and continue only pending steps."
        if resume else "Start by reading the plan and the current HoloQA run status."
    )
    return f"""You are executing an evidence-backed HoloQA release checklist.

Plan: {plan_path}
Run directory: {run_dir}

{continuation}

- The plan is read-only. Do not edit it.
- Do not modify application source, configuration, or test fixtures.
- Read the whole plan first and follow its staged user journeys in order.
- Use the actor assigned to each plan step, and establish that actor's required authentication state before its checks. Reuse that actor's page, authentication, and test data only across related checks assigned to the same actor; never carry one role's session into another role's step.
- Avoid repeating a login, navigation, or setup action when the current browser state for that actor already satisfies it.
- For each step, collect the evidence its assertions need, then judge that step once. A single DOM capture can prove several URL/text assertions; a single API capture can prove status, JSON, and header assertions. Do not capture screenshots unless the plan requires them or visual evidence is useful.
- Prefer the least expensive evidence that proves the requirement: use API captures for API behavior and DOM captures for page content. Use screenshots for visual state, not as a substitute for machine-checkable assertions.
- Do not open extra tabs or windows unless the plan requires them. Keep all actions in the assigned actor session and close a session only when that actor's journey is finished.
- Check the current page and run status before repeating work. After an uncertain action, inspect the result before retrying; for submits, deletes, or other non-idempotent actions, avoid creating duplicate side effects.
- Follow declared dependencies and captured variables exactly. Do not skip a step because a related check passed; every planned step needs its own HoloQA verdict.
- Use HoloQA tools to capture evidence and judge each step. Read status at the start and after meaningful milestones, rather than polling it after every browser action.
- A PASS or FAIL is authoritative only when returned by HoloQA.
- Use BLOCKED only for a genuine prerequisite or safety stop, with a concrete cause.
- Do not call holoqa_run_package unless the user explicitly asked for it.
- At completion, summarize work but do not claim a release decision. HoloQA status is authoritative.
"""


def provider_binary(provider: str) -> str | None:
    if provider not in PROVIDERS:
        raise AgentError(f"unknown provider {provider!r}; use one of {', '.join(PROVIDERS)}")
    return shutil.which(provider)


def provider_info(provider: str) -> dict[str, Any]:
    binary = provider_binary(provider)
    version = ""
    if binary:
        try:
            result = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=15
            )
            version = (result.stdout or result.stderr).strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            version = "installed"
    return {
        "provider": provider,
        "installed": bool(binary),
        "binary": binary or "",
        "version": version,
        "structured_output": True,
        "modes": list(MODES),
        "unattended": UNATTENDED_MEANING.get(provider, ""),
    }


def _mcp_command() -> list[str]:
    """Use this interpreter, avoiding a dependency on a globally installed script."""
    return [sys.executable, "-m", "holoqa.cli", "mcp"]


def _toml(value: str) -> str:
    return json.dumps(value)


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml(value) for value in values) + "]"


def _provider_command(binary: str, args: list[str]) -> list[str]:
    """Windows cannot execute npm's .cmd shims through CreateProcess directly."""
    if os.name == "nt" and binary.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", binary, *args]
    return [binary, *args]


def build_launch(
    request: AgentRunRequest,
    run: run_module.Run,
    prompt: str,
    directory: Path,
) -> LaunchSpec:
    """Create an isolated, per-run MCP configuration and launch command."""
    binary = provider_binary(request.provider)
    if not binary:
        raise AgentError(f"{request.provider} is not on PATH; run `holoqa agent doctor`")
    if request.mode not in MODES:
        raise AgentError(f"unknown mode {request.mode!r}; use one of {', '.join(MODES)}")

    command = _mcp_command()
    env = {**os.environ, "HOLOQA_RUN_DIR": str(run.dir), "HOLOQA_MANAGED_BROWSER": "1"}
    browser_module.configure(run, directory / "browser-bin", env)
    mcp_env = {key: value for key, value in env.items()
               if key.startswith("HOLOQA_") or key in {"PATH", "AGENT_BROWSER_ARGS", "AGENT_BROWSER_SESSION"}}
    config_path = directory / f"{request.provider}-mcp-config.json"

    if request.provider == "codex":
        # Codex accepts one-off config overrides. This leaves user and project
        # config untouched while making a missing HoloQA MCP server fail closed.
        args = [
            "exec", "--json", "--ignore-user-config",
            "--config", f"mcp_servers.holoqa.command={_toml(command[0])}",
            "--config", f"mcp_servers.holoqa.args={_toml_list(command[1:])}",
            "--config", f"mcp_servers.holoqa.env.HOLOQA_RUN_DIR={_toml(str(run.dir))}",
            "--config", "mcp_servers.holoqa.required=true",
            "--config", 'mcp_servers.holoqa.env.HOLOQA_MANAGED_BROWSER="1"',
            # `codex exec` has no human approval host. Approve only HoloQA's
            # own local tools; the sandbox remains workspace-write.
            "--config", "mcp_servers.holoqa.default_tools_approval_mode=\"approve\"",
        ]
        for key, value in mcp_env.items():
            args.extend(["--config", f"mcp_servers.holoqa.env.{key}={_toml(value)}"])
        if request.model:
            args.extend(["--model", request.model])
        if request.mode == "unattended":
            # This is intentionally opt-in. It is for an isolated CI/staging
            # workspace where the wrapper must let the agent invoke its local
            # browser driver without interactive approval.  The sandbox remains
            # workspace-write; this is not the unrestricted bypass flag.
            args.extend(["--approve-for-me", "--ignore-rules"])
        else:
            args.extend(["--sandbox", "workspace-write"])
        args.append(prompt)
        args = _provider_command(binary, args)
        config_path.write_text("# Codex configuration is supplied through --config.\n", encoding="utf-8")
    elif request.provider == "claude":
        config_path.write_text(json.dumps({"mcpServers": {"holoqa": {
            "command": command[0], "args": command[1:], "env": mcp_env,
        }}}, indent=2), encoding="utf-8")
        # Keep the multiline prompt last. With a Windows .cmd shim, arguments
        # after a newline-bearing prompt are otherwise eaten by `cmd /c`.
        args = ["-p", "--mcp-config", str(config_path), "--output-format", "stream-json", "--verbose"]
        if request.model:
            args.extend(["--model", request.model])
        if request.desktop_run:
            # No edit tools or unrestricted shell in the native desktop. Claude
            # may call only HoloQA's evidence tools and the browser driver.
            args.extend([
                "--permission-mode", "dontAsk", "--permission-prompts", "none",
                "--allowedTools",
                "mcp__holoqa__holoqa_browser",
                "mcp__holoqa__holoqa_observe",
                "mcp__holoqa__holoqa_judge",
                "mcp__holoqa__holoqa_block",
                "mcp__holoqa__holoqa_note",
                "mcp__holoqa__holoqa_run_status",
            ])
        elif request.mode == "unattended":
            # `--permission-prompts none` means *nobody answers*, so anything
            # that would prompt is denied automatically — it is the opposite of
            # Codex's `--approve-for-me`. A run configured for unattended work
            # was therefore denied the login it needed and recorded the refusal
            # as a BLOCKED that looked like an application problem.
            #
            # Unattended means "proceed without a human". That is expressed by
            # allowing the specific tools this workflow needs: HoloQA's own MCP
            # tools and the browser driver, which is what the agent must call to
            # do the job. The sandbox is unchanged and nothing global is written.
            args.extend([
                "--permission-mode", "acceptEdits",
                "--permission-prompts", "none",
                "--allowedTools",
                "mcp__holoqa__*",
                "Bash(agent-browser:*)",
            ])
        args.append(prompt)
        args = _provider_command(binary, args)
    else:
        config_path.write_text(json.dumps({"mcp": {"servers": {"holoqa": {
            "type": "local", "command": command, "cwd": str(request.cwd),
            "environment": mcp_env, "codemode": False,
        }}}}, indent=2), encoding="utf-8")
        env["OPENCODE_CONFIG"] = str(config_path)
        args = ["run", "--format", "json"]
        if request.model:
            args.extend(["--model", request.model])
        if request.mode == "unattended":
            args.append("--auto")
        args.append(prompt)
        args = _provider_command(binary, args)

    return LaunchSpec(command=args, env=env, config_path=config_path)


def _manifest_path(run: run_module.Run) -> Path:
    return run.out_dir / MANIFEST_FILE


def _write_manifest(run: run_module.Run, data: dict[str, Any]) -> None:
    atomic_json(_manifest_path(run), data)


def _read_manifest(run: run_module.Run) -> dict[str, Any]:
    path = _manifest_path(run)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _safe_command(command: list[str]) -> list[str]:
    """Commands contain no credentials, but retain only a reviewable invocation."""
    return list(command)


def _emit(line: str) -> None:
    """Write agent output even when Windows' console cannot encode Unicode."""
    try:
        print(line)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        payload = (line + "\n").encode(encoding, errors="replace")
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            buffer.flush()
        else:  # pragma: no cover - streams normally expose a binary buffer
            sys.stdout.write(payload.decode(encoding, errors="replace"))


class StreamExit(int):
    def __new__(cls, code: int, reason: str = ""):
        result = super().__new__(cls, code)
        result.reason = reason
        return result


_HELD_RUNS: ContextVar[tuple[str, ...]] = ContextVar("held_runs", default=())


def execution_guard(function):
    @wraps(function)
    def guarded(request, run, *args, **kwargs):
        key = str(run.dir)
        if key in _HELD_RUNS.get():
            return function(request, run, *args, **kwargs)
        lock = file_lock(run.dir / ".execution.lock", timeout=0)
        try:
            lock.__enter__()
        except TimeoutError as error:
            raise AgentError("This run already has an active execution") from error
        token = _HELD_RUNS.set((*_HELD_RUNS.get(), key))
        try:
            return function(request, run, *args, **kwargs)
        finally:
            _HELD_RUNS.reset(token)
            lock.__exit__(None, None, None)
    return guarded


def _stream_process(
    spec: LaunchSpec,
    request: AgentRunRequest,
    run: run_module.Run,
    *,
    deadline: float | None = None,
) -> int:
    if request.cancel_event and request.cancel_event.is_set():
        return StreamExit(130, "interrupted")
    if deadline is not None and time.monotonic() >= deadline:
        return StreamExit(130, "timed_out")
    process = subprocess.Popen(
        spec.command, cwd=request.cwd, env=spec.env, stdin=subprocess.DEVNULL, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1,
        # Agent CLIs emit UTF-8 JSON/events even when the Windows console uses
        # cp1252. Never let one non-console character kill the drain thread.
        encoding="utf-8", errors="replace",
        start_new_session=os.name != "nt",
        creationflags=0x00000004 if os.name == "nt" else 0,  # CREATE_SUSPENDED
    )
    job = None
    if os.name == "nt":
        from holoqa.process import WindowsJob
        try:
            job = WindowsJob(process._handle)
            job.resume(process.pid)
        except OSError:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            process.wait()
            if job is not None:
                job.close()
            raise AgentError("Cannot establish Windows process-tree ownership")
    output: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=256)

    def stop_process() -> None:
        # The agent owns MCP children too. On POSIX the isolated process group
        # lets cancellation stop that tree without touching the user's shell.
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif job is not None:
            job.terminate()
        if process.poll() is None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()

    stop_readers = threading.Event()

    def enqueue(item: tuple[str, str | None]) -> None:
        while not stop_readers.is_set():
            try:
                output.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def drain(channel: str, stream: Any) -> None:
        try:
            for line in iter(lambda: stream.readline(65537), ""):
                if len(line) > 65536:
                    enqueue(("error", "Agent output record exceeds 65536 characters"))
                    break
                enqueue((channel, line.rstrip("\n")))
                if stop_readers.is_set():
                    break
        finally:
            enqueue((channel, None))
            stream.close()

    readers = [threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
               threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True)]
    event_file = run.out_dir / EVENTS_FILE
    event_handle = None
    open_streams = 2
    cancelled = False
    timed_out = False
    try:
        for reader in readers:
            reader.start()
        if request.retain_events:
            event_handle = event_file.open("w", encoding="utf-8")
        while open_streams or process.poll() is None:
            if request.cancel_event and request.cancel_event.is_set():
                cancelled = True
                stop_process()
                break
            # A wall-clock deadline turns a hang into a failure. Without one, an
            # agent that stalls holds a CI job open forever instead of reporting
            # an incomplete run — which is the worse of the two outcomes.
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                stop_process()
                break
            try:
                channel, line = output.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and not any(reader.is_alive() for reader in readers):
                    break
                continue
            if channel == "error":
                raise AgentError(line or "Agent output reader failed")
            if line is None:
                open_streams -= 1
                continue
            prefix = "agent" if channel == "stdout" else "agent:stderr"
            if request.output_callback:
                request.output_callback(channel, line)
            # A TUI callback owns presentation; printing raw JSON into the
            # alternate terminal screen corrupts the dashboard. The CLI keeps
            # the legacy stream when no callback is attached.
            if not request.output_callback:
                _emit(f"[{prefix}] {line}")
            if event_handle:
                event_handle.write(json.dumps({"channel": channel, "line": line}) + "\n")
                event_handle.flush()
    except KeyboardInterrupt:
        stop_process()
        return StreamExit(130, "interrupted")
    except BaseException:
        stop_process()
        raise
    finally:
        stop_readers.set()
        if process.poll() is None:
            stop_process()
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=0.5)
        if event_handle:
            event_handle.close()
        if job is not None:
            job.close()
    if cancelled or timed_out:
        return StreamExit(130, "timed_out" if timed_out else "interrupted")
    return StreamExit(process.wait())


def _status(run: run_module.Run) -> dict[str, Any]:
    plan = run.pinned_plan()
    return run.status(plan)


@execution_guard
def launch(
    request: AgentRunRequest,
    run: run_module.Run,
    *,
    resume: bool = False,
    timeout_s: int = 0,
) -> dict[str, Any]:
    """Launch an agent against an existing run, then report HoloQA's state."""
    request = AgentRunRequest(
        provider=request.provider, cwd=request.cwd.resolve(), mode=request.mode,
        model=request.model, retain_events=request.retain_events, desktop_run=request.desktop_run,
        output_callback=request.output_callback,
        cancel_event=request.cancel_event,
    )
    if timeout_s < 0:
        raise AgentError("timeout_s must be zero or positive")
    if not request.cwd.is_dir():
        raise AgentError(f"working directory does not exist: {request.cwd}")
    plan_path = run.plan_pin
    prompt = workflow_prompt(plan_path, run.dir, resume=resume)
    prompt += (
        "\nBrowser sessions (actor -> AGENT_BROWSER_SESSION): "
        + json.dumps(browser_module.sessions(run), sort_keys=True)
        + "\nFor EVERY agent-browser command set AGENT_BROWSER_SESSION to the mapped "
        "session for that actor. Reuse it across steps; never create extra sessions "
        "or browsers. Keep tabs bounded and close tabs you no longer need. "
        "HoloQA owns session cleanup. After cleanup or a resumed execution, "
        "reopen the target and authenticate as needed.\n"
    )
    deadline = time.monotonic() + timeout_s if timeout_s else None
    with tempfile.TemporaryDirectory(prefix="holoqa-agent-") as temp:
        spec = build_launch(request, run, prompt, Path(temp))
        prompt_path = run.out_dir / PROMPT_FILE
        prompt_path.write_text(prompt, encoding="utf-8")
        provider = provider_info(request.provider)
        manifest = {
            "provider": request.provider, "provider_version": provider["version"],
            "mode": request.mode, "model": request.model,
            "cwd": str(request.cwd), "plan_sha256": run.read()["plan_sha256"],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "prompt_file": PROMPT_FILE,
            "command": _safe_command(spec.command), "state": "running",
            "retain_events": request.retain_events, "resumed": resume,
            "timeout_s": timeout_s,
            "config": f"ephemeral:{request.provider}", "started_at": run_module.now(),
        }
        _write_manifest(run, manifest)
        try:
            code = _stream_process(spec, request, run, deadline=deadline)
        except BaseException as error:
            manifest.update({"state": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                             "finished_at": run_module.now(), "error": type(error).__name__})
            raise
        finally:
            if not _DEFER_BROWSER_CLEANUP.get():
                manifest["browser_cleanup_errors"] = browser_module.cleanup(run)
            _write_manifest(run, manifest)

    state = "interrupted" if code == 130 else ("completed" if code == 0 else "failed")
    reason = getattr(code, "reason", "")
    if reason:
        state = reason
    elif not isinstance(code, StreamExit) and code == 130 and timeout_s:
        state = "timed_out"
    manifest.update({"state": state, "exit_code": code, "finished_at": run_module.now()})
    _write_manifest(run, manifest)
    return {"agent": manifest, "holoqa": _status(run)}


def _run_until_done(
    request: AgentRunRequest,
    run: run_module.Run,
    *,
    max_rounds: int = 5,
    timeout_s: int = 0,
    resume: bool = False,
) -> dict[str, Any]:
    """Keep resuming the agent while the run still has pending steps.

    An agent that stops after one stage leaves a HOLD with 96 steps pending and
    nobody to continue them; the wrapper reported that faithfully and did
    nothing, which is a report rather than a tool. This loops, and stops when
    the run is complete, when a round makes no progress, or at ``max_rounds``.

    Progress is measured in judged steps, so a round that judges nothing is a
    stall and looping again would only burn time and money.
    """
    history: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    judged = run.status(run.pinned_plan())["total"] - len(run.status(run.pinned_plan())["pending"])
    for round_number in range(1, max(1, max_rounds) + 1):
        result = launch(request, run, resume=resume or round_number > 1, timeout_s=timeout_s)
        status = result["holoqa"]
        pending = status.get("pending", [])
        now_judged = status.get("total", 0) - len(pending)
        history.append({
            "round": round_number,
            "agent_state": result["agent"].get("state"),
            "judged": now_judged,
            "pending": len(pending),
            "decision": status.get("decision"),
        })
        if not pending or result["agent"].get("state") in {"failed", "interrupted", "timed_out"}:
            break
        if now_judged <= judged:
            # No forward progress: another identical round cannot help.
            break
        judged = now_judged
    result["rounds"] = history
    result["until_done"] = not result.get("holoqa", {}).get("pending")
    return result


@execution_guard
def run_until_done(
    request: AgentRunRequest,
    run: run_module.Run,
    *,
    max_rounds: int = 5,
    timeout_s: int = 0,
    resume: bool = False,
) -> dict[str, Any]:
    """Retain browser state between rounds, release sessions on every exit."""
    token = _DEFER_BROWSER_CLEANUP.set(True)
    result: dict[str, Any] = {}
    try:
        result = _run_until_done(request, run, max_rounds=max_rounds,
                                 timeout_s=timeout_s, resume=resume)
        return result
    finally:
        _DEFER_BROWSER_CLEANUP.reset(token)
        errors = browser_module.cleanup(run)
        if result.get("agent") is not None:
            result["agent"]["browser_cleanup_errors"] = errors
            _write_manifest(run, result["agent"])


def new_run_directory(plan, request) -> Path:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return request.cwd.resolve() / ".holoqa" / "runs" / f"{stamp}-{plan.meta.app}-{uuid.uuid4().hex[:8]}"


def create_and_launch(
    plan_path: str | Path,
    request: AgentRunRequest,
    *,
    tag: str = "", commit: str = "", tester: str = "", base_url: str = "", run_dir: str = "",
    timeout_s: int = 0, until_done: bool = False, max_rounds: int = 5,
) -> dict[str, Any]:
    plan = plan_module.load(plan_path)
    directory = Path(run_dir) if run_dir else new_run_directory(plan, request)
    run = run_module.Run.create(directory, plan, meta={
        "tag": tag, "commit": commit, "tester": tester,
        "base_url": base_url or plan.meta.base_url,
    })
    if until_done:
        return run_until_done(request, run, timeout_s=timeout_s, max_rounds=max_rounds)
    return launch(request, run, timeout_s=timeout_s)


def status(directory: str | Path) -> dict[str, Any]:
    run = run_module.find(directory)
    return {"agent": _read_manifest(run), "holoqa": _status(run)}
