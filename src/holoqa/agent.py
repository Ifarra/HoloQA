"""Provider-neutral orchestration for coding agents that use HoloQA.

This layer launches an agent; it never interprets the agent's prose as a test
result. The only release decision returned here comes from the HoloQA run.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from holoqa import plan as plan_module
from holoqa import run as run_module


class AgentError(ValueError):
    """A coding-agent wrapper request could not be started safely."""


PROVIDERS = ("codex", "claude", "opencode")
MODES = ("supervised", "unattended")
MANIFEST_FILE = "agent.json"
EVENTS_FILE = "agent-events.jsonl"
PROMPT_FILE = "agent-prompt.md"


@dataclass(frozen=True)
class AgentRunRequest:
    provider: str
    cwd: Path
    mode: str = "supervised"
    model: str = ""
    retain_events: bool = False
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
- Drive the target application only as needed to exercise the plan.
- Use HoloQA tools to capture evidence and judge each step.
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
    env = {**os.environ, "HOLOQA_RUN_DIR": str(run.dir)}
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
            # `codex exec` has no human approval host. Approve only HoloQA's
            # own local tools; the sandbox remains workspace-write.
            "--config", "mcp_servers.holoqa.default_tools_approval_mode=\"approve\"",
        ]
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
            "command": command[0], "args": command[1:], "env": {"HOLOQA_RUN_DIR": str(run.dir)},
        }}}, indent=2), encoding="utf-8")
        # Keep the multiline prompt last. With a Windows .cmd shim, arguments
        # after a newline-bearing prompt are otherwise eaten by `cmd /c`.
        args = ["-p", "--mcp-config", str(config_path), "--output-format", "stream-json", "--verbose"]
        if request.model:
            args.extend(["--model", request.model])
        if request.mode == "unattended":
            args.extend(["--permission-prompts", "none"])
        args.append(prompt)
        args = _provider_command(binary, args)
    else:
        config_path.write_text(json.dumps({"mcp": {"servers": {"holoqa": {
            "type": "local", "command": command, "cwd": str(request.cwd),
            "environment": {"HOLOQA_RUN_DIR": str(run.dir)}, "codemode": False,
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
    _manifest_path(run).write_text(json.dumps(data, indent=2), encoding="utf-8")


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


def _stream_process(
    spec: LaunchSpec,
    request: AgentRunRequest,
    run: run_module.Run,
) -> int:
    process = subprocess.Popen(
        spec.command, cwd=request.cwd, env=spec.env, stdin=subprocess.DEVNULL, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1,
        # Agent CLIs emit UTF-8 JSON/events even when the Windows console uses
        # cp1252. Never let one non-console character kill the drain thread.
        encoding="utf-8", errors="replace",
    )
    output: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def drain(channel: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                output.put((channel, line.rstrip("\n")))
        finally:
            output.put((channel, None))

    readers = [threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
               threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True)]
    for reader in readers:
        reader.start()

    event_file = run.out_dir / EVENTS_FILE
    event_handle = event_file.open("w", encoding="utf-8") if request.retain_events else None
    open_streams = 2
    cancelled = False
    try:
        while open_streams:
            if request.cancel_event and request.cancel_event.is_set() and process.poll() is None:
                process.terminate()
                cancelled = True
            try:
                channel, line = output.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and not any(reader.is_alive() for reader in readers):
                    break
                continue
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
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return 130
    finally:
        if event_handle:
            event_handle.close()
    if cancelled:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return 130
    return process.wait()


def _status(run: run_module.Run) -> dict[str, Any]:
    plan = plan_module.load(run.read()["plan_path"])
    return run.status(plan)


def launch(request: AgentRunRequest, run: run_module.Run, *, resume: bool = False) -> dict[str, Any]:
    """Launch an agent against an existing run, then report HoloQA's state."""
    request = AgentRunRequest(
        provider=request.provider, cwd=request.cwd.resolve(), mode=request.mode,
        model=request.model, retain_events=request.retain_events,
        output_callback=request.output_callback,
        cancel_event=request.cancel_event,
    )
    if not request.cwd.is_dir():
        raise AgentError(f"working directory does not exist: {request.cwd}")
    plan_path = Path(run.read()["plan_path"])
    prompt = workflow_prompt(plan_path, run.dir, resume=resume)
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
            "config": f"ephemeral:{request.provider}", "started_at": run_module.now(),
        }
        _write_manifest(run, manifest)
        code = _stream_process(spec, request, run)

    manifest.update({"state": "interrupted" if code == 130 else ("completed" if code == 0 else "failed"),
                     "exit_code": code, "finished_at": run_module.now()})
    _write_manifest(run, manifest)
    return {"agent": manifest, "holoqa": _status(run)}


def create_and_launch(
    plan_path: str | Path,
    request: AgentRunRequest,
    *,
    tag: str = "", commit: str = "", tester: str = "", base_url: str = "", run_dir: str = "",
) -> dict[str, Any]:
    plan = plan_module.load(plan_path)
    directory = Path(run_dir) if run_dir else request.cwd / ".holoqa" / "runs" / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}-{plan.meta.app}"
    )
    run = run_module.Run.create(directory, plan, meta={
        "tag": tag, "commit": commit, "tester": tester,
        "base_url": base_url or plan.meta.base_url,
    })
    return launch(request, run)


def status(directory: str | Path) -> dict[str, Any]:
    run = run_module.find(directory)
    return {"agent": _read_manifest(run), "holoqa": _status(run)}
