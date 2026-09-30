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
from holoqa import run as run_module
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
        if verdict != PASS and not _cause(step):
            missing_cause.append(step_id)

    problems = []
    if missing_evidence:
        problems.append(f"PASS without evidence: {', '.join(missing_evidence)}")
    if missing_cause:
        problems.append(f"{FAIL}/{BLOCKED} without a cause: {', '.join(missing_cause)}")
    if strict and unjudged:
        problems.append(f"not yet judged: {', '.join(unjudged)}")

    # The hashes were recorded and never checked, so a file edited after capture
    # was packaged as if it were the capture. Now it is a problem, not a
    # decoration — and it is the one check that works even on a run this process
    # did not create, because it compares the records to the bytes.
    tampered = run.verify_evidence()
    if tampered:
        problems.append(
            f"evidence does not match its recorded hash: {'; '.join(tampered)}"
        )

    integrity = run.integrity()
    return {
        "ok": not problems,
        "problems": problems,
        "unjudged": unjudged,
        "counts": _counts(data),
        "integrity": integrity["status"],
        "integrity_reason": integrity.get("reason", ""),
    }


def _cause(step: dict[str, Any]) -> str:
    """The reason a step did not pass: derived first, then the human note."""
    return (step.get("verdict_note") or step.get("note") or "").strip()


def _plan_for(run: Run, data: dict[str, Any]) -> Any:
    """The plan a run was started from, or ``None`` if it cannot be read.

    Prefers the pinned copy, so the report describes the contract the run was
    judged against even if the author's file has since moved or changed.
    """
    from holoqa import plan as plan_module

    for candidate in (run.plan_pin, Path(data.get("plan_path", ""))):
        try:
            if candidate and Path(candidate).is_file():
                return plan_module.load(candidate)
        except Exception:
            continue
    return None


def _known_defect_steps(plan: Any, data: dict[str, Any]) -> dict[str, str]:
    """Steps that failed, where the plan already knew the cause.

    A ``known_defect`` in ``known_behaviors`` says a failure here is expected.
    Reporting it beside a genuine regression is how a team learns to ignore the
    report — and it is exactly the wrong signal for a release decision, where
    the question is "did anything get *worse*". Returns step id -> KB id.
    """
    if plan is None:
        return {}
    out: dict[str, str] = {}
    for entry in plan.known_behaviors:
        if entry.verdict_hint != "known_defect":
            continue
        for step_id in entry.applies_to:
            verdict = data["steps"].get(step_id, {}).get("verdict")
            if verdict in {FAIL, BLOCKED}:
                out[step_id] = entry.id
    return out


def _weak_steps(plan: Any, data: dict[str, Any]) -> list[tuple[str, str]]:
    """Steps the plan itself marked as proving little, with the reason.

    A PASS on a weak step is honest but not strong evidence, and a reader of the
    report cannot tell the two apart from the verdict alone. Saying "12 of 31
    passes are weak" is the difference between a checklist that looks green and
    one that is green.
    """
    if plan is None:
        return []
    out: list[tuple[str, str]] = []
    for step in plan.steps:
        if step.strength == "weak":
            out.append((step.id, step.weak_reason or "no reason given"))
    return out


def _counts(data: dict[str, Any]) -> dict[str, int]:
    counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
    for step in data["steps"].values():
        verdict = step.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def markdown(run: Run, integrity: dict[str, Any] | None = None) -> Path:
    data = run.read()
    counts = _counts(data)
    meta = data.get("meta", {})
    plan = _plan_for(run, data)
    known = _known_defect_steps(plan, data)
    weak = _weak_steps(plan, data)
    integrity = integrity or run.integrity()
    status = integrity.get("status", "unverified")
    lines = [
        f"# Release checklist — {data.get('app', 'application')}",
        "",
        f"- Run: `{data['run_id']}`",
        f"- Plan: `{Path(data.get('plan_path', '')).name}` (sha256 `{data.get('plan_sha256','')[:12]}`)",
    ]
    for label in ("tag", "commit", "tester", "base_url", "started_at"):
        if meta.get(label):
            lines.append(f"- {label.replace('_', ' ').title()}: `{meta[label]}`")
    # The first thing a reviewer needs to know is whether the records can be
    # trusted, because every line below is only as good as that answer.
    lines.append(f"- Evidence integrity: **{status}** — {integrity.get('reason', '')}")
    lines += [
        "",
        f"**{counts[PASS]} passed · {counts[FAIL]} failed · {counts[BLOCKED]} blocked — "
        f"decision: {decision(counts) if status != 'unverified' else 'HOLD (unverified)'}**",
    ]
    if status == "unverified":
        lines.append(
            "> This run's evidence could not be verified by the process reporting it, "
            "so it cannot decide a release. A human must read it, then record the "
            "decision with `holoqa attest`."
        )
    elif status == "tampered":
        lines.append(
            "> The run's records contradict its bytes. Do not act on this report "
            "without establishing what changed."
        )
    if weak:
        passing_weak = [
            step_id for step_id, _ in weak
            if data["steps"].get(step_id, {}).get("verdict") == PASS
        ]
        lines.append(
            f"**Evidence strength: {len(passing_weak)} of {counts[PASS]} passes are "
            f"marked weak** — they need a human to read the evidence."
        )
    lines += [
        "",
        "One failed or blocked step holds the release.",
        "",
        "| Step | Title | Actor | Verdict | Decided by | Evidence | Note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for step_id, step in data["steps"].items():
        evidence = ", ".join(f"`{item['file']}`" for item in step["observations"]) or "—"
        # A derived failure reason first, otherwise the tester's own note. A
        # passing step never inherits the message from a verdict it replaced.
        note = _cause(step).replace("|", "\\|") or "—"
        # Which identity produced the evidence, when the run used more than one.
        actors = sorted({item["actor"] for item in step["observations"] if item.get("actor")})
        actor = ", ".join(actors) or "—"
        lines.append(
            f"| {step_id} | {step['title']} | {actor} | {_STATUS_MARK.get(step.get('verdict'), '—')} "
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

    # Separate an expected failure from a new one. A release reviewer is asking
    # "did anything get worse", so a defect the plan already documents must not
    # sit in the same list as a fresh regression.
    failed = [
        (step_id, step) for step_id, step in data["steps"].items()
        if step.get("verdict") in {FAIL, BLOCKED}
    ]

    # Group blocked steps by their declared root cause. "64 blocked" is not
    # actionable; "41 blocked on the same missing fixture" is. Only a
    # `requires:` failure has a machine-readable cause — an agent's prose
    # BLOCKED stays in the per-step table where it belongs.
    causes: dict[str, list[str]] = {}
    for step_id, step in failed:
        cause = step.get("blocked_cause") or ""
        if cause and step_id not in known:
            causes.setdefault(cause, []).append(step_id)
    if causes:
        lines += ["", "## Blocked by root cause", ""]
        for cause, step_ids in sorted(causes.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- **{len(step_ids)} step(s)** — {cause}")
            lines.append(f"  - {', '.join(f'`{sid}`' for sid in step_ids)}")

    if known or failed:
        lines += ["", "## New failures", ""]
        new = [(sid, step) for sid, step in failed if sid not in known]
        if new:
            for step_id, step in new:
                lines.append(
                    f"- `{step_id}` {step['title']} — {step.get('verdict')}: "
                    f"{_cause(step) or '—'}"
                )
        else:
            lines.append("- none")

        if known:
            lines += ["", "## Known defects", ""]
            for step_id, kb_id in sorted(known.items()):
                step = data["steps"][step_id]
                lines.append(
                    f"- `{step_id}` {step['title']} — {step.get('verdict')} "
                    f"(expected, `{kb_id}`): {_cause(step) or '—'}"
                )

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
    """validate → report → optional xlsx → zip.

    A run whose integrity is ``unverified`` still packages, but it does not get
    to *decide* anything: the archive is produced for a human to review and the
    decision is HOLD. Refusing outright would strand real runs whose server has
    restarted; deciding RELEASE would be a green result nobody can stand behind.
    The archive carries the integrity verdict and the reason.
    """
    checked = validate(run, strict=strict)
    if not checked["ok"]:
        raise PackageError(
            "run is not fit to package: " + "; ".join(checked["problems"])
        )

    integrity = checked["integrity"]
    data = run.read()
    artifacts = [markdown(run, integrity=checked)]

    run_copy = run.out_dir / "run.json"
    run_copy.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    artifacts.append(run_copy)

    integrity_copy = run.out_dir / "integrity.json"
    integrity_copy.write_text(
        json.dumps(
            {
                "status": integrity,
                "reason": checked.get("integrity_reason", ""),
                "evidence_files": sorted(
                    item.name for item in run.evidence_dir.glob("*") if item.is_file()
                ),
                "at": run_module.now(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    artifacts.append(integrity_copy)

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
    # An unverifiable run packages for review, but it does not decide: a RELEASE
    # nobody can stand behind is the exact output this tool exists to prevent.
    decision_out = decision(counts)
    if integrity == "unverified":
        decision_out = "HOLD"
    return {
        "status": "packaged",
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "counts": counts,
        "decision": decision_out,
        "integrity": integrity,
        "integrity_reason": checked.get("integrity_reason", ""),
        "unjudged": checked["unjudged"],
        "artifacts": [item.name for item in artifacts],
    }
