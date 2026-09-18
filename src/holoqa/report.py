"""Reports and the packaged deliverable.

The output is a ZIP holding a report, the run record, and every evidence file,
because that is what a release reviewer actually opens. The prior tool's
deliverable had the same shape and it was the right call.

Packaging refuses to run on an incomplete run unless explicitly forced, and a
forced package still states in the report which steps were never judged.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from holoqa import BLOCKED, FAIL, PASS
from holoqa.run import Run, decision

_STATUS_MARK = {PASS: "PASS", FAIL: "FAIL", BLOCKED: "BLOCKED", None: "—"}


class PackageError(ValueError):
    pass


def validate(run: Run, *, strict: bool = True) -> dict[str, Any]:
    """Check a run is fit to package."""
    data = run.read()
    unjudged, missing_evidence, missing_cause = [], [], []

    for step_id, step in data["steps"].items():
        verdict = step.get("verdict")
        if verdict is None:
            unjudged.append(step_id)
            continue
        if verdict == PASS and not step["observations"]:
            missing_evidence.append(step_id)
        if verdict != PASS and not (step.get("note") or "").strip():
            missing_cause.append(step_id)

    problems = []
    if missing_evidence:
        problems.append(f"PASS without evidence: {', '.join(missing_evidence)}")
    if missing_cause:
        problems.append(f"{FAIL}/{BLOCKED} without a cause: {', '.join(missing_cause)}")
    if strict and unjudged:
        problems.append(f"not yet judged: {', '.join(unjudged)}")

    return {
        "ok": not problems,
        "problems": problems,
        "unjudged": unjudged,
        "counts": _counts(data),
    }


def _counts(data: dict[str, Any]) -> dict[str, int]:
    counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
    for step in data["steps"].values():
        verdict = step.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def markdown(run: Run) -> Path:
    data = run.read()
    counts = _counts(data)
    meta = data.get("meta", {})
    lines = [
        f"# Release checklist — {data.get('app', 'application')}",
        "",
        f"- Run: `{data['run_id']}`",
        f"- Plan: `{Path(data.get('plan_path', '')).name}` (sha256 `{data.get('plan_sha256','')[:12]}`)",
    ]
    for label in ("tag", "commit", "tester", "base_url", "started_at"):
        if meta.get(label):
            lines.append(f"- {label.replace('_', ' ').title()}: `{meta[label]}`")
    lines += [
        "",
        f"**{counts[PASS]} passed · {counts[FAIL]} failed · {counts[BLOCKED]} blocked — "
        f"decision: {decision(counts)}**",
        "",
        "One failed or blocked step holds the release.",
        "",
        "| Step | Title | Verdict | Decided by | Evidence | Note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for step_id, step in data["steps"].items():
        evidence = ", ".join(f"`{item['file']}`" for item in step["observations"]) or "—"
        note = (step.get("note") or "").replace("|", "\\|") or "—"
        lines.append(
            f"| {step_id} | {step['title']} | {_STATUS_MARK.get(step.get('verdict'), '—')} "
            f"| {step.get('decided_by', '—')} | {evidence} | {note} |"
        )

    revised = [
        (step_id, step["revisions"])
        for step_id, step in data["steps"].items()
        if step.get("revisions")
    ]
    if revised:
        lines += ["", "## Revised verdicts", ""]
        for step_id, revisions in revised:
            for revision in revisions:
                lines.append(
                    f"- `{step_id}` {revision['from']} → {revision['to']} at "
                    f"{revision['at']} (was: {revision['previous_note'] or '—'})"
                )

    cited = sorted({ref for step in data["steps"].values() for ref in step.get("kb_refs", [])})
    if cited:
        lines += ["", "## Known behaviours cited", "", ", ".join(f"`{ref}`" for ref in cited)]

    path = run.out_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def package(
    run: Run,
    *,
    strict: bool = True,
    workbook_source: str | Path | None = None,
) -> dict[str, Any]:
    """validate → report → optional xlsx → zip."""
    checked = validate(run, strict=strict)
    if not checked["ok"]:
        raise PackageError(
            "run is not fit to package: " + "; ".join(checked["problems"])
        )

    data = run.read()
    artifacts = [markdown(run)]

    run_copy = run.out_dir / "run.json"
    run_copy.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    artifacts.append(run_copy)

    if workbook_source:
        from holoqa.workbook import annotate

        steps = list(data["steps"].values())
        artifacts.append(
            annotate(Path(workbook_source), run.out_dir / "checklist.xlsx", steps)
        )

    archive = run.out_dir / f"{data['run_id']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for artifact in artifacts:
            bundle.write(artifact, artifact.name)
        for evidence in sorted(run.evidence_dir.glob("*")):
            if evidence.is_file():
                bundle.write(evidence, f"evidence/{evidence.name}")

    counts = checked["counts"]
    return {
        "status": "packaged",
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "counts": counts,
        "decision": decision(counts),
        "unjudged": checked["unjudged"],
        "artifacts": [item.name for item in artifacts],
    }
