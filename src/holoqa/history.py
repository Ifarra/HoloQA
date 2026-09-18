"""Cross-run memory — the capability the prior tool lacked entirely.

Each completed run appends one line to ``history.jsonl``. That is enough to
answer the question a release reviewer actually asks: *what regressed since the
last time this was green?*

A standalone run can only say whether today is broken. A history says whether
today is broken *in a new way*, which is what decides whether a release moves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from holoqa import BLOCKED, FAIL, PASS
from holoqa.run import Run, decision

HISTORY_FILE = "history.jsonl"


def _history_path(run: Run) -> Path:
    return run.dir.parent.parent / HISTORY_FILE


def record(run: Run) -> Path:
    """Append this run's outcome. Idempotent per run id."""
    data = run.read()
    counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
    verdicts = {}
    for step_id, step in data["steps"].items():
        verdict = step.get("verdict")
        verdicts[step_id] = verdict
        if verdict in counts:
            counts[verdict] += 1

    entry = {
        "run_id": data["run_id"],
        "app": data.get("app", ""),
        "meta": data.get("meta", {}),
        "counts": counts,
        "decision": decision(counts),
        "verdicts": verdicts,
    }

    path = _history_path(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = [
        line
        for line in _read(path)
        if line.get("run_id") != entry["run_id"]
    ]
    existing.append(entry)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in existing) + "\n",
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def compare(run: Run, against: str | None = None) -> dict[str, Any]:
    """Diff this run against a named run, or the most recent green one."""
    data = run.read()
    entries = [
        entry for entry in _read(_history_path(run))
        if entry["run_id"] != data["run_id"] and entry.get("app") == data.get("app", "")
    ]
    if not entries:
        return {
            "status": "no_baseline",
            "detail": "no earlier run of this app recorded; this run becomes the baseline",
        }

    if against:
        baseline = next((entry for entry in entries if entry["run_id"] == against), None)
        if baseline is None:
            return {"status": "not_found", "detail": f"no run {against} in history"}
    else:
        green = [entry for entry in entries if entry.get("decision") == "RELEASE"]
        baseline = (green or entries)[-1]

    current = {
        step_id: step.get("verdict") for step_id, step in data["steps"].items()
    }
    previous = baseline.get("verdicts", {})

    regressions, fixes, changes = [], [], []
    for step_id in sorted(set(current) | set(previous)):
        before, after = previous.get(step_id), current.get(step_id)
        if before == after:
            continue
        change = {"step": step_id, "before": before or "—", "after": after or "—"}
        changes.append(change)
        if before == PASS and after in {FAIL, BLOCKED}:
            regressions.append(change)
        elif before in {FAIL, BLOCKED} and after == PASS:
            fixes.append(change)

    return {
        "status": "ok",
        "baseline": baseline["run_id"],
        "baseline_decision": baseline.get("decision"),
        "regressions": regressions,
        "fixes": fixes,
        "changes": changes,
        "flaky": flaky(run),
    }


def flaky(run: Run, minimum_runs: int = 3) -> list[dict[str, Any]]:
    """Steps whose verdict has flipped across recent runs of the same app."""
    data = run.read()
    entries = [
        entry for entry in _read(_history_path(run))
        if entry.get("app") == data.get("app", "")
    ]
    if len(entries) < minimum_runs:
        return []

    unstable = []
    recent = entries[-10:]
    for step_id in data["steps"]:
        seen = [entry["verdicts"].get(step_id) for entry in recent]
        seen = [item for item in seen if item]
        if len(set(seen)) > 1 and PASS in seen:
            unstable.append(
                {
                    "step": step_id,
                    "verdicts": seen,
                    "passes": seen.count(PASS),
                    "runs": len(seen),
                }
            )
    return unstable
