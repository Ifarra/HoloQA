from __future__ import annotations

from pathlib import Path
from typing import Any

from .runs import RunStore


def cancel_run(run_id: str, state_database: str) -> dict[str, Any]:
    store = RunStore(Path(state_database))
    store.request_cancel(run_id)
    return store.get(run_id).model_dump()


def set_control_mode(run_id: str, state_database: str, mode: str) -> dict[str, Any]:
    store = RunStore(Path(state_database))
    store.set_control_mode(run_id, mode)
    return store.get(run_id).model_dump()
