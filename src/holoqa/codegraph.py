from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel


class CodeGraphSnapshot(BaseModel):
    status: str
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    commit: str


def discover_snapshot(workspace: Path, commit: str) -> CodeGraphSnapshot:
    artifact = workspace / ".codebase-memory" / "graph.db.zst"
    if not artifact.is_file():
        return CodeGraphSnapshot(status="unavailable", commit=commit)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return CodeGraphSnapshot(status="available", artifact_path=str(artifact), artifact_sha256=digest, commit=commit)
