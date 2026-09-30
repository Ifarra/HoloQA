"""Run directory, run.json, evidence index, and the guardrails.

Plain files, no database. A run is inspectable with ``cat``, diffable in git,
and already the shape of the ZIP that ships.

The guardrails here are ported from the prior tool's ``record.mjs``, which put
it plainly: *sengaja keras, jangan dilonggarkan* — deliberately strict, do not
loosen them. They are the reason a filled checklist means anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from holoqa import AGENT_ASSERTABLE, BLOCKED, PASS, VERDICTS
from holoqa import plan as plan_module

RUN_FILE = "run.json"
VARS_FILE = "vars.json"
EVIDENCE_DIR = "evidence"
OUT_DIR = "out"

_SLUG = re.compile(r"[^a-z0-9]+")


class GuardrailError(ValueError):
    """A guardrail refused the write. The message says which one and why."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    return _SLUG.sub("-", str(value).lower()).strip("-") or "item"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class Run:
    """One checklist execution, backed by a directory on disk."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory).expanduser().resolve()
        self.evidence_dir = self.dir / EVIDENCE_DIR
        self.out_dir = self.dir / OUT_DIR

    # ------------------------------------------------------------------ setup

    @classmethod
    def create(
        cls,
        directory: str | Path,
        plan: plan_module.Plan,
        meta: dict[str, Any] | None = None,
    ) -> "Run":
        run = cls(directory)
        if (run.dir / RUN_FILE).exists():
            raise GuardrailError(
                f"run already exists at {run.dir}; pick a new directory or resume it"
            )
        run.evidence_dir.mkdir(parents=True, exist_ok=True)
        run.out_dir.mkdir(parents=True, exist_ok=True)

        steps = {
            step.id: {
                "id": step.id,
                "stage": step.stage,
                "title": step.title,
                "verdict": None,
                "note": "",
                "verdict_note": "",
                "blocked_cause": "",
                "observations": [],
                "assertions": [],
                "kb_refs": [],
                "revisions": [],
                "started_at": None,
                "finished_at": None,
            }
            for step in plan.steps
        }
        run._write(
            {
                "run_id": run.dir.name,
                "plan_path": plan.source_path,
                "plan_sha256": plan.source_sha256,
                "app": plan.meta.app,
                "language": plan.meta.language,
                "meta": {**(meta or {}), "started_at": now()},
                "steps": steps,
            }
        )
        run._write_vars({})
        return run

    # ------------------------------------------------------------------- data

    def _write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / RUN_FILE).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def read(self) -> dict[str, Any]:
        path = self.dir / RUN_FILE
        if not path.is_file():
            raise GuardrailError(f"no run.json in {self.dir}; start a run first")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_vars(self, variables: dict[str, Any]) -> None:
        (self.dir / VARS_FILE).write_text(
            json.dumps(variables, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def vars(self) -> dict[str, Any]:
        path = self.dir / VARS_FILE
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def bind(self, name: str, value: Any) -> None:
        variables = self.vars()
        variables[name] = value
        self._write_vars(variables)

    def step(self, data: dict[str, Any], step_id: str) -> dict[str, Any]:
        if step_id not in data["steps"]:
            raise GuardrailError(f"step {step_id} is not in this run")
        return data["steps"][step_id]

    # ------------------------------------------------------------- evidence

    def attach(
        self,
        step_id: str,
        *,
        kind: str,
        path: Path,
        target: str = "",
        summary: dict[str, Any] | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        """Register a captured file as evidence for a step.

        Guardrail 4: a reference to a missing file is refused. Guardrail 1
        depends on this — an empty file never counts as evidence.

        ``actor`` records which browser session produced the bytes. With more
        than one identity in a run, "which session was this captured in" is part
        of what makes the evidence mean something: a step that checks ownership
        is only a test if it ran as the right actor.
        """
        path = Path(path)
        if not path.is_file():
            raise GuardrailError(f"evidence file does not exist: {path}")
        size = path.stat().st_size
        if size == 0:
            raise GuardrailError(f"evidence file is empty: {path.name}")

        data = self.read()
        step = self.step(data, step_id)
        record = {
            "kind": kind,
            "file": path.name,
            "target": target,
            "actor": actor,
            "bytes": size,
            "sha256": sha256_file(path),
            "captured_at": now(),
            "summary": summary or {},
        }
        step["observations"].append(record)
        if not step["started_at"]:
            step["started_at"] = now()
        self._write(data)
        return record

    def observations(self, step_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        step = self.step(self.read(), step_id)
        items = step["observations"]
        return [item for item in items if kind is None or item["kind"] == kind]

    # -------------------------------------------------------------- verdicts

    def set_verdict(
        self,
        step_id: str,
        verdict: str,
        *,
        note: str = "",
        assertions: list[dict[str, Any]] | None = None,
        by: str = "holoqa",
    ) -> dict[str, Any]:
        """Write a verdict, enforcing guardrails 1, 2, 3 and 5.

        ``by`` records provenance. Only :data:`holoqa.AGENT_ASSERTABLE`
        verdicts may arrive with ``by="agent"``; everything else must be
        derived by :mod:`holoqa.verdict` from HoloQA's own captures.
        """
        if verdict not in VERDICTS:
            raise GuardrailError(
                f"verdict {verdict!r} is not one of {', '.join(VERDICTS)}"
            )
        if by == "agent" and verdict not in AGENT_ASSERTABLE:
            raise GuardrailError(
                f"an agent may only assert {', '.join(AGENT_ASSERTABLE)}; "
                f"{verdict} is derived by HoloQA from captured evidence"
            )

        data = self.read()
        step = self.step(data, step_id)

        note = (note or "").strip()

        # A derived verdict explains itself in `verdict_note`; a human's own
        # observation lives in `note` and survives re-judging. Keeping them
        # apart stops a stale failure message from trailing a later PASS.
        derived = by != "agent"
        cause = note if derived else (note or step.get("note", ""))

        # Guardrail 1: no evidence, no pass.
        if verdict == PASS and not step["observations"]:
            raise GuardrailError(
                f"step {step_id}: PASS refused, no evidence captured. "
                "Capture an observation first, or record BLOCKED with a cause."
            )
        # Guardrail 2: a non-pass must say why.
        if verdict != PASS and not (cause or step.get("note", "")):
            raise GuardrailError(
                f"step {step_id}: {verdict} requires a note giving the concrete cause"
            )

        # Guardrail 5: a changed verdict leaves the superseded one behind.
        previous = step.get("verdict")
        if previous and previous != verdict:
            step["revisions"].append(
                {
                    "from": previous,
                    "to": verdict,
                    "at": now(),
                    "previous_note": step.get("verdict_note") or step.get("note", ""),
                }
            )

        step["verdict"] = verdict
        if derived:
            step["verdict_note"] = cause
        else:
            step["note"] = cause
            step["verdict_note"] = ""
        step["decided_by"] = by
        # A machine-readable cause, so a report can group N blocked steps by root
        # cause instead of printing N agent sentences. "requires api not
        # satisfied: ..." is the same string for every step that shares a
        # precondition, which is what makes the grouping meaningful.
        step["blocked_cause"] = (
            _blocked_cause(assertions) if verdict == BLOCKED and assertions else ""
        )
        if assertions is not None:
            step["assertions"] = assertions
        step["finished_at"] = now()
        if not step["started_at"]:
            step["started_at"] = now()
        self._write(data)
        return step

    def note(self, step_id: str, note: str, kb_refs: list[str] | None = None) -> dict[str, Any]:
        data = self.read()
        step = self.step(data, step_id)
        if note:
            step["note"] = note.strip()
        for ref in kb_refs or []:
            if ref not in step["kb_refs"]:
                step["kb_refs"].append(ref)
        if not step["started_at"]:
            step["started_at"] = now()
        self._write(data)
        return step

    # --------------------------------------------------------------- status

    def blocked_dependencies(self, plan: plan_module.Plan, step_id: str) -> list[str]:
        """Guardrail 7: a step cannot be judged until its prerequisites passed."""
        data = self.read()
        unmet = []
        for dependency in plan.step(step_id).depends_on:
            if data["steps"].get(dependency, {}).get("verdict") != PASS:
                unmet.append(dependency)
        return unmet

    def status(self, plan: plan_module.Plan) -> dict[str, Any]:
        data = self.read()
        steps = data["steps"]
        counts = {verdict: 0 for verdict in VERDICTS}
        pending: list[str] = []
        for step_id in plan.step_ids:
            verdict = steps.get(step_id, {}).get("verdict")
            if verdict in counts:
                counts[verdict] += 1
            else:
                pending.append(step_id)
        next_step = pending[0] if pending else None
        return {
            "run_id": data["run_id"],
            "run_dir": str(self.dir),
            "app": data.get("app", ""),
            "meta": data.get("meta", {}),
            "total": len(plan.step_ids),
            "counts": counts,
            "pending": pending,
            "next_step": next_step,
            "vars": self.vars(),
            # An incomplete checklist is not releasable. This matters to the
            # agent wrapper: an agent that exits before judging a step must
            # never make an all-zero run look green.
            "decision": "HOLD" if pending else decision(counts),
        }


def _blocked_cause(assertions: list[dict[str, Any]]) -> str:
    """The shared reason a step blocked: a prerequisite that the plan declared.

    Only a ``requires`` failure produces a cause here. An agent-asserted BLOCKED
    has free-text prose, which cannot be grouped — and grouping is the point:
    "64 blocked" is not a finding, "41 blocked on the same missing fixture" is.
    """
    for item in assertions:
        if item.get("kind") == "requires":
            return str(item.get("detail", ""))
    return ""


def decision(counts: dict[str, int]) -> str:
    """One failure holds the release.

    Carried over from the prior tool's release-decision block: *"Satu baris
    Gagal berarti keputusan Tahan."*
    """
    if counts.get("FAIL"):
        return "HOLD"
    if counts.get(BLOCKED):
        return "HOLD"
    return "RELEASE"


def find(directory: str | Path | None = None) -> Run:
    """Resolve the active run: an explicit path, ``$HOLOQA_RUN_DIR``, or latest."""
    import os

    if directory:
        return Run(directory)
    env = os.environ.get("HOLOQA_RUN_DIR")
    if env:
        return Run(env)
    root = Path(".holoqa/runs").resolve()
    if root.is_dir():
        candidates = sorted(
            (item for item in root.iterdir() if (item / RUN_FILE).is_file()),
            key=lambda item: item.name,
        )
        if candidates:
            return Run(candidates[-1])
    raise GuardrailError(
        "no active run; pass run_dir, set HOLOQA_RUN_DIR, or start a run first"
    )
