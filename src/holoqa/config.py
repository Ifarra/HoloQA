from __future__ import annotations

import os
from pathlib import Path


DEFAULT_STATE_DATABASE = "/data/.holoqa/state.db"


def state_database() -> Path:
    return Path(os.environ.get("HOLOQA_STATE", DEFAULT_STATE_DATABASE))


def artifact_root() -> Path:
    return state_database().expanduser().resolve().parent / "artifacts"


def auth_root() -> Path:
    path = state_database().expanduser().resolve().parent / "auth"
    path.mkdir(parents=True, exist_ok=True)
    return path
