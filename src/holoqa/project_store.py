from __future__ import annotations

import hashlib
import json
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
                CREATE TABLE IF NOT EXISTS environments (
                    environment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
                    base_url TEXT NOT NULL, kind TEXT DEFAULT 'staging', browser TEXT DEFAULT 'chromium',
                    viewport TEXT DEFAULT '1440x900', locale TEXT DEFAULT 'en-US', protected INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE TABLE IF NOT EXISTS requirements (
                    requirement_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, req_key TEXT NOT NULL,
                    title TEXT NOT NULL, description TEXT DEFAULT '', priority TEXT DEFAULT 'medium',
                    status TEXT DEFAULT 'uncovered', test_ids TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE TABLE IF NOT EXISTS testcases (
                    testcase_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, testcase_set_id TEXT NOT NULL,
                    test_id TEXT NOT NULL, title TEXT NOT NULL, steps TEXT NOT NULL, expected_result TEXT NOT NULL,
                    source TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

    def environments(self, project_id: str | None = None) -> list[dict[str, object]]:
        with self._connect() as connection:
            query = "SELECT environment_id, project_id, name, base_url, kind, browser, viewport, locale, protected, created_at FROM environments"
            args: tuple[object, ...] = ()
            if project_id:
                query += " WHERE project_id=?"; args = (project_id,)
            rows = connection.execute(query + " ORDER BY created_at DESC", args).fetchall()
        return [{**dict(row), "protected": bool(row[8])} for row in rows]

    def create_environment(self, payload: dict[str, object]) -> dict[str, object]:
        required = [str(payload.get(key) or "") for key in ("project_id", "name", "base_url")]
        if not all(required):
            raise ValueError("project_id, name, and base_url are required")
        environment_id = f"env_{uuid.uuid4().hex[:12]}"
        values = (environment_id, required[0], required[1], required[2], str(payload.get("kind") or "staging"), str(payload.get("browser") or "chromium"), str(payload.get("viewport") or "1440x900"), str(payload.get("locale") or "en-US"), int(bool(payload.get("protected"))))
        with self._connect() as connection:
            connection.execute("INSERT INTO environments(environment_id, project_id, name, base_url, kind, browser, viewport, locale, protected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        return self.environments(required[0])[0]

    def delete_environment(self, environment_id: str) -> None:
        with self._connect() as connection:
            if connection.execute("DELETE FROM environments WHERE environment_id=?", (environment_id,)).rowcount == 0:
                raise ValueError(f"environment not found: {environment_id}")

    def requirements(self, project_id: str | None = None) -> list[dict[str, object]]:
        with self._connect() as connection:
            query = "SELECT requirement_id, project_id, req_key, title, description, priority, status, test_ids, created_at FROM requirements"
            args: tuple[object, ...] = ()
            if project_id:
                query += " WHERE project_id=?"; args = (project_id,)
            rows = connection.execute(query + " ORDER BY created_at DESC", args).fetchall()
        return [{**dict(row), "key": row[2], "test_ids": json.loads(row[7] or "[]")} for row in rows]

    def import_cases(self, project_id: str, cases: list[dict], source: Path) -> dict[str, object]:
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone():
                raise ValueError(f"project not found: {project_id}")
            testcase_set_id = f"set_{uuid.uuid4().hex[:12]}"
            for case in cases:
                connection.execute(
                    "INSERT INTO testcases(testcase_id, project_id, testcase_set_id, test_id, title, steps, expected_result, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"tc_{uuid.uuid4().hex[:12]}", project_id, testcase_set_id, str(case.get("test_id") or ""), str(case.get("title") or ""), json.dumps(case.get("steps") or []), str(case.get("expected_result") or ""), str(source.resolve())),
                )
        return {"testcase_set_id": testcase_set_id, "project_id": project_id, "case_count": len(cases)}

    def testcases(self, project_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT testcase_id, testcase_set_id, test_id, title, steps, expected_result, source, created_at FROM testcases WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return [{"testcase_id": r[0], "testcase_set_id": r[1], "test_id": r[2], "title": r[3], "steps": json.loads(r[4] or "[]"), "expected_result": r[5], "source": r[6], "created_at": r[7]} for r in rows]

    def create_requirement(self, payload: dict[str, object]) -> dict[str, object]:
        project_id, title = str(payload.get("project_id") or ""), str(payload.get("title") or "")
        if not project_id or not title: raise ValueError("project_id and title are required")
        requirement_id = f"req_{uuid.uuid4().hex[:12]}"
        key = str(payload.get("key") or f"REQ-{uuid.uuid4().hex[:4].upper()}")
        with self._connect() as connection:
            connection.execute("INSERT INTO requirements(requirement_id, project_id, req_key, title, description, priority, status, test_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (requirement_id, project_id, key, title, str(payload.get("description") or ""), str(payload.get("priority") or "medium"), str(payload.get("status") or "uncovered"), json.dumps(payload.get("test_ids") or [])))
        return self.requirements(project_id)[0]
