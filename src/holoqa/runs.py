from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from pydantic import BaseModel


class Plan(BaseModel):
    plan_id: str
    project_id: str
    cases: list[dict]
    approved: bool = False


class Run(BaseModel):
    run_id: str
    plan_id: str
    status: str
    message: str = ""


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
                    run_id TEXT PRIMARY KEY, plan_id TEXT, status TEXT, message TEXT
                );
                """
            )

    def create_plan(self, project_id: str, cases: list[dict]) -> Plan:
        plan = Plan(plan_id=f"plan_{uuid.uuid4().hex[:12]}", project_id=project_id, cases=cases)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO plans VALUES (?, ?, ?, 0)", (plan.plan_id, project_id, json.dumps(cases)))
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
            run = Run(run_id=f"run_{uuid.uuid4().hex[:12]}", plan_id=plan_id, status="BLOCKED", message="Run requires approval")
        else:
            run = Run(run_id=f"run_{uuid.uuid4().hex[:12]}", plan_id=plan_id, status="PASS", message="MVP execution completed")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?)", (run.run_id, run.plan_id, run.status, run.message))
        return run

    def list_runs(self) -> list[dict[str, str]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT run_id, plan_id, status, message FROM runs ORDER BY rowid DESC").fetchall()
        return [{"run_id": row[0], "plan_id": row[1], "status": row[2], "message": row[3]} for row in rows]

    def get(self, run_id: str) -> Run:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT run_id, plan_id, status, message FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"run not found: {run_id}")
        return Run(run_id=row[0], plan_id=row[1], status=row[2], message=row[3])
