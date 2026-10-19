"""Run-owned browser sessions for the managed agent wrapper.

Logical actor names remain evidence identities; physical sessions are unique
per run. Standalone MCP users retain their existing session names.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def session_name(run_dir: Path, actor: str) -> str:
    scope = hashlib.sha256(str(run_dir.resolve()).encode()).hexdigest()[:16]
    identity = hashlib.sha256(actor.encode()).hexdigest()[:12]
    return f"holoqa-{scope}-{identity}"


def sessions(run) -> dict[str, str]:
    plan = run.pinned_plan()
    return {actor: session_name(run.dir, actor)
            for actor in sorted({plan.actor_for(step) for step in plan.step_ids})}


def cleanup(run) -> list[str]:
    """Close only this run's sessions, reporting failures without hiding results."""
    from holoqa import observe
    errors = []
    for session in sessions(run).values():
        try:
            observe.run_cli(["close"], session=session, timeout=10)
        except (observe.CaptureError, OSError) as error:
            errors.append(f"{session}: {error}")
    return errors
