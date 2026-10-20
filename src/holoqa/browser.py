"""Run-owned browser sessions for the managed agent wrapper.

Logical actor names remain evidence identities; physical sessions are unique
per run. Standalone MCP users retain their existing session names.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def session_name(run_dir: Path, actor: str) -> str:
    scope = hashlib.sha256(str(run_dir.resolve()).encode()).hexdigest()[:16]
    identity = hashlib.sha256(actor.encode()).hexdigest()[:12]
    return f"holoqa-{scope}-{identity}"


def sessions(run) -> dict[str, str]:
    plan = run.pinned_plan()
    return {actor: session_name(run.dir, actor)
            for actor in sorted({plan.actor_for(step) for step in plan.step_ids})}


def configure(run, directory: Path, env: dict[str, str]) -> None:
    """Install a per-execution PATH shim; never rewrite the user's installation."""
    binary = shutil.which("agent-browser", path=env.get("PATH"))
    if not binary:
        return
    env["HOLOQA_BROWSER_BINARY"] = binary
    names = sessions(run)
    env["HOLOQA_BROWSER_SESSIONS"] = json.dumps(names)
    env["HOLOQA_MANAGED_BROWSER"] = "1"
    plan = run.pinned_plan()
    env["AGENT_BROWSER_SESSION"] = names.get(plan.default_actor, next(iter(names.values())))
    flags = list(plan.meta.browser.args)
    if plan.meta.browser.ignore_https_errors:
        flags.append("--ignore-https-errors")
    env["AGENT_BROWSER_ARGS"] = " ".join(flags)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        script = directory / "agent-browser.cmd"
        script.write_text(f'@"{sys.executable}" -m holoqa.browser_driver %*\r\n')
    else:
        script = directory / "agent-browser"
        script.write_text(f'#!{sys.executable}\nfrom holoqa.browser_driver import main\nmain()\n')
        script.chmod(0o700)
    env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")


def cleanup(run) -> list[str]:
    """Close only sessions actually used by this run; leave failures retryable."""
    from holoqa.browser_driver import raw, session_lock_path
    from holoqa.storage import atomic_json, file_lock
    registry = run.out_dir / "browser-sessions.json"
    errors = []
    try:
        # The OS lock is the cleanup owner; unlike a PID marker, it cannot be
        # confused by PID reuse and is released automatically after a crash.
        with file_lock(run.dir / ".browser-cleanup.lock", timeout=0):
            with file_lock(run.dir / ".browser.lock"):
                if not registry.is_file():
                    return []
                state = json.loads(registry.read_text())
                pending = list(state["active"])
                binary = os.environ.get("HOLOQA_BROWSER_BINARY") or shutil.which("agent-browser")
                if not binary:
                    return ["agent-browser is unavailable; owned sessions still need cleanup"]
                state["closing"] = True
                atomic_json(registry, state)
            try:
                for session in pending:
                    prefix = session_name(run.dir, "").rsplit("-", 1)[0] + "-"
                    if not session.startswith(prefix):
                        errors.append("Refused cleanup of an unowned session")
                        continue
                    try:
                        with file_lock(session_lock_path(run.dir, session)):
                            raw(["close"], {**os.environ, "HOLOQA_BROWSER_BINARY": binary,
                                            "AGENT_BROWSER_SESSION": session}, timeout=10)
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        errors.append(f"{session}: {error}")
                        continue
                    with file_lock(run.dir / ".browser.lock"):
                        if registry.is_file():
                            state = json.loads(registry.read_text())
                            if session in state["active"]:
                                state["active"].remove(session)
                                atomic_json(registry, state)
            finally:
                with file_lock(run.dir / ".browser.lock"):
                    if registry.is_file():
                        state = json.loads(registry.read_text())
                        if state.pop("closing", None) is not None:
                            atomic_json(registry, state)
    except TimeoutError:
        return ["Browser cleanup is already in progress"]
    return errors
