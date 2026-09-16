from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel

from holoqa.codegraph import discover_snapshot


class ProjectInitialization(BaseModel):
    project_id: str
    snapshot_id: str
    workspace_root: str
    commit: str
    project_name: str
    codegraph_status: str = "unavailable"
    codegraph_artifact: str | None = None
    codegraph_sha256: str | None = None


class ProjectStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    workspace_root TEXT NOT NULL UNIQUE,
                    project_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(snapshots)")}
            for name, definition in (("codegraph_status", "TEXT"), ("codegraph_artifact", "TEXT"), ("codegraph_sha256", "TEXT")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE snapshots ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _commit(workspace: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            digest = hashlib.sha256(str(workspace).encode()).hexdigest()
            return f"uncommitted-{digest[:16]}"

    def initialize(self, workspace_root: Path) -> ProjectInitialization:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace does not exist: {workspace}")
        project_id = f"project_{uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex[:16]}"
        snapshot_id = f"snapshot_{uuid.uuid4().hex[:16]}"
        commit = self._commit(workspace)
        graph = discover_snapshot(workspace, commit)
        project_name = workspace.name or "HoloQA Project"
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO projects(project_id, workspace_root, project_name) VALUES (?, ?, ?)",
                (project_id, str(workspace), project_name),
            )
            connection.execute(
                "INSERT INTO snapshots(snapshot_id, project_id, commit_sha, codegraph_status, codegraph_artifact, codegraph_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, project_id, commit, graph.status, graph.artifact_path, graph.artifact_sha256),
            )
        return ProjectInitialization(
            project_id=project_id,
            snapshot_id=snapshot_id,
            workspace_root=str(workspace),
            commit=commit,
            project_name=project_name,
            codegraph_status=graph.status,
            codegraph_artifact=graph.artifact_path,
            codegraph_sha256=graph.artifact_sha256,
        )

    def projects(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_id, project_name, workspace_root, created_at FROM projects ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]
