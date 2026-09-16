from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel


class Plan(BaseModel):
    plan_id: str
    project_id: str
    cases: list[dict]
    name: str = "Untitled plan"
    approved: bool = False
    plan_version: str = ""
    environment: str = "local"
    snapshot_id: str | None = None


class Run(BaseModel):
    run_id: str
    plan_id: str
    status: str
    message: str = ""
    execution_mode: str = "real"
    current_test_id: str | None = None
    current_step: str | None = None
    completed_cases: int = 0
    total_cases: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    last_heartbeat: str | None = None


class RunStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY, project_id TEXT, cases TEXT, approved INTEGER
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, plan_id TEXT, status TEXT, message TEXT, results TEXT DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    test_id TEXT,
                    step TEXT,
                    status TEXT,
                    message TEXT,
                    payload TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY, run_id TEXT, test_id TEXT, artifact_path TEXT, kind TEXT
                );
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, test_id TEXT, title TEXT NOT NULL,
                    status TEXT NOT NULL, severity TEXT DEFAULT 'medium', state TEXT DEFAULT 'open',
                    expected TEXT DEFAULT '', actual TEXT DEFAULT '', evidence TEXT DEFAULT '[]', created_at TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(plans)")}
            for name, definition in (("name", "TEXT DEFAULT 'Untitled plan'"), ("plan_version", "TEXT DEFAULT ''"), ("environment", "TEXT DEFAULT 'local'"), ("snapshot_id", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE plans ADD COLUMN {name} {definition}")
            run_columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "execution_mode" not in run_columns:
                db.execute("ALTER TABLE runs ADD COLUMN execution_mode TEXT DEFAULT 'real'")
            for name, definition in (
                ("current_test_id", "TEXT"),
                ("current_step", "TEXT"),
                ("completed_cases", "INTEGER DEFAULT 0"),
                ("total_cases", "INTEGER DEFAULT 0"),
                ("started_at", "TEXT"),
                ("finished_at", "TEXT"),
                ("last_heartbeat", "TEXT"),
                ("cancel_requested", "INTEGER DEFAULT 0"),
            ):
                if name not in run_columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _event(self, run_id: str, event_type: str, *, test_id: str | None = None, step: str | None = None, status: str | None = None, message: str = "", payload: dict | None = None) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO run_events(run_id, event_type, test_id, step, status, message, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, event_type, test_id, step, status, message, json.dumps(payload or {}), self._now()),
            )

    def queue(self, plan_id: str) -> Run:
        plan = self.get_plan(plan_id)
        approved = plan.approved
        run = Run(run_id=f"run_{uuid.uuid4().hex[:12]}", plan_id=plan_id, status="QUEUED" if approved else "BLOCKED", message="Run queued" if approved else "Run requires approval", total_cases=len(plan.cases))
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO runs(run_id, plan_id, status, message, results, total_cases, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, plan_id, run.status, run.message, "[]", run.total_cases, self._now()),
            )
        self._event(run.run_id, "run_queued" if approved else "run_blocked", status=run.status, message=run.message, payload={"total_cases": run.total_cases})
        return run

    def list_plans(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT plan_id, project_id, name, approved, environment, snapshot_id, cases FROM plans ORDER BY rowid DESC").fetchall()
        return [{"plan_id": row[0], "project_id": row[1], "name": row[2] or "Untitled plan", "approved": bool(row[3]), "environment": row[4], "snapshot_id": row[5], "case_count": len(json.loads(row[6] or "[]"))} for row in rows]

    def mark_running(self, run_id: str) -> None:
        now = self._now()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET status='RUNNING', message=?, started_at=COALESCE(started_at, ?), last_heartbeat=? WHERE run_id=?", ("Browser execution started", now, now, run_id))
        self._event(run_id, "run_started", status="RUNNING", message="Browser execution started")

    def progress(self, run_id: str, *, event_type: str, test_id: str | None = None, step: str | None = None, status: str | None = None, message: str = "", completed_cases: int | None = None, payload: dict | None = None) -> None:
        now = self._now()
        with sqlite3.connect(self.path) as db:
            if completed_cases is None:
                db.execute("UPDATE runs SET current_test_id=?, current_step=?, last_heartbeat=? WHERE run_id=?", (test_id, step, now, run_id))
            else:
                db.execute("UPDATE runs SET current_test_id=?, current_step=?, completed_cases=?, last_heartbeat=? WHERE run_id=?", (test_id, step, completed_cases, now, run_id))
        self._event(run_id, event_type, test_id=test_id, step=step, status=status, message=message, payload=payload)

    def request_cancel(self, run_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError(f"run not found: {run_id}")
            if row[0] in {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE", "CANCELLED"}:
                return
            db.execute("UPDATE runs SET cancel_requested=1, message=?, last_heartbeat=? WHERE run_id=?", ("Cancellation requested", self._now(), run_id))
        self._event(run_id, "cancel_requested", message="Cancellation requested")

    def cancellation_requested(self, run_id: str) -> bool:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row[0])

    def events(self, run_id: str, after_id: int = 0) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT event_id, run_id, event_type, test_id, step, status, message, payload, created_at FROM run_events WHERE run_id=? AND event_id>? ORDER BY event_id", (run_id, after_id)).fetchall()
        return [{"event_id": r[0], "run_id": r[1], "event_type": r[2], "test_id": r[3], "step": r[4], "status": r[5], "message": r[6], "payload": json.loads(r[7] or "{}"), "created_at": r[8]} for r in rows]

    def create_plan(self, project_id: str, cases: list[dict], environment: str = "local", snapshot_id: str | None = None, name: str = "Untitled plan") -> Plan:
        fingerprint = hashlib.sha256(json.dumps({"project_id": project_id, "cases": cases, "environment": environment, "snapshot_id": snapshot_id}, sort_keys=True).encode()).hexdigest()
        plan = Plan(plan_id=f"plan_{uuid.uuid4().hex[:12]}", project_id=project_id, cases=cases, name=name, environment=environment, snapshot_id=snapshot_id, plan_version=fingerprint)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO plans(plan_id, project_id, cases, name, approved, plan_version, environment, snapshot_id) VALUES (?, ?, ?, ?, 0, ?, ?, ?)", (plan.plan_id, project_id, json.dumps(cases), name, fingerprint, environment, snapshot_id))
        return plan

    def approve(self, plan_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            updated = db.execute("UPDATE plans SET approved=1 WHERE plan_id=?", (plan_id,)).rowcount
        if not updated:
            raise ValueError(f"plan not found: {plan_id}")

    def execute(self, plan_id: str) -> Run:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT approved FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError(f"plan not found: {plan_id}")
        if not row[0]:
            run = Run(run_id=f"run_{uuid.uuid4().hex[:12]}", plan_id=plan_id, status="BLOCKED", message="Run requires approval", execution_mode="real")
        else:
            run = Run(run_id=f"run_{uuid.uuid4().hex[:12]}", plan_id=plan_id, status="PASS", message="MVP execution completed", execution_mode="real")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO runs(run_id, plan_id, status, message, results, execution_mode) VALUES (?, ?, ?, ?, ?, ?)", (run.run_id, run.plan_id, run.status, run.message, "[]", run.execution_mode))
        return run

    def get_plan(self, plan_id: str) -> Plan:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT plan_id, project_id, cases, name, approved, plan_version, environment, snapshot_id FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError(f"plan not found: {plan_id}")
        return Plan(plan_id=row[0], project_id=row[1], cases=json.loads(row[2]), name=row[3] or "Untitled plan", approved=bool(row[4]), plan_version=row[5] or "", environment=row[6] or "local", snapshot_id=row[7])

    def complete(self, run_id: str, status: str, message: str, results: list[dict], execution_mode: str = "real") -> None:
        if status not in {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}:
            raise ValueError(f"unsupported run status: {status}")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET status=?, message=?, results=?, execution_mode=?, finished_at=?, last_heartbeat=? WHERE run_id=?", (status, message, json.dumps(results), execution_mode, self._now(), self._now(), run_id))
            for result in results:
                items = result.get("evidence_items") or [{"path": path, "kind": "screenshot"} for path in str(result.get("evidence", "")).split(";") if path]
                for item in items:
                    artifact_path = str(item.get("path", ""))
                    if artifact_path:
                        db.execute(
                            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                            (f"evidence_{uuid.uuid4().hex[:12]}", run_id, result.get("test_id", ""), artifact_path, str(item.get("kind", "artifact"))),
                        )
        self._event(run_id, "run_finished", status=status, message=message, payload={"result_count": len(results)})

    def results(self, run_id: str) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT results FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"run not found: {run_id}")
        return json.loads(row[0] or "[]")

    def evidence(self, run_id: str) -> list[dict[str, str]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT evidence_id, run_id, test_id, artifact_path, kind FROM evidence WHERE run_id=?", (run_id,)).fetchall()
        return [{"evidence_id": row[0], "run_id": row[1], "test_id": row[2], "artifact_path": row[3], "kind": row[4]} for row in rows]

    def create_finding(self, run_id: str, test_id: str, title: str, status: str, expected: str = "", actual: str = "", severity: str = "medium") -> dict[str, object]:
        finding_id = f"finding_{uuid.uuid4().hex[:12]}"
        now = self._now()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO findings(finding_id, run_id, test_id, title, status, severity, state, expected, actual, evidence, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)", (finding_id, run_id, test_id, title, status, severity, expected, actual, "[]", now))
        return self.finding(finding_id)

    def finding(self, finding_id: str) -> dict[str, object]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT finding_id, run_id, test_id, title, status, severity, state, expected, actual, evidence, created_at FROM findings WHERE finding_id=?", (finding_id,)).fetchone()
        if not row:
            raise ValueError(f"finding not found: {finding_id}")
        return {"finding_id": row[0], "run_id": row[1], "test_id": row[2], "title": row[3], "status": row[4], "severity": row[5], "state": row[6], "expected": row[7], "actual": row[8], "evidence": json.loads(row[9] or "[]"), "created_at": row[10]}

    def list_findings(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.path) as db:
            ids = [row[0] for row in db.execute("SELECT finding_id FROM findings ORDER BY created_at DESC").fetchall()]
        return [self.finding(finding_id) for finding_id in ids]

    def update_finding(self, finding_id: str, *, state: str | None = None, severity: str | None = None) -> dict[str, object]:
        updates, values = [], []
        if state is not None:
            updates.append("state=?"); values.append(state)
        if severity is not None:
            updates.append("severity=?"); values.append(severity)
        if updates:
            values.append(finding_id)
            with sqlite3.connect(self.path) as db:
                if db.execute(f"UPDATE findings SET {', '.join(updates)} WHERE finding_id=?", values).rowcount == 0:
                    raise ValueError(f"finding not found: {finding_id}")
        return self.finding(finding_id)

    def list_runs(self) -> list[dict[str, str]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT run_id, plan_id, status, message, current_test_id, current_step, completed_cases, total_cases, started_at, finished_at, last_heartbeat FROM runs ORDER BY rowid DESC").fetchall()
        return [{"run_id": row[0], "plan_id": row[1], "status": row[2], "message": row[3], "current_test_id": row[4], "current_step": row[5], "completed_cases": row[6] or 0, "total_cases": row[7] or 0, "started_at": row[8], "finished_at": row[9], "last_heartbeat": row[10]} for row in rows]

    def get(self, run_id: str) -> Run:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT run_id, plan_id, status, message, execution_mode, current_test_id, current_step, completed_cases, total_cases, started_at, finished_at, last_heartbeat FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"run not found: {run_id}")
        return Run(run_id=row[0], plan_id=row[1], status=row[2], message=row[3], execution_mode=row[4] or "real", current_test_id=row[5], current_step=row[6], completed_cases=row[7] or 0, total_cases=row[8] or 0, started_at=row[9], finished_at=row[10], last_heartbeat=row[11])
