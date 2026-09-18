"""Command line: run the MCP server, validate a plan, or self-test.

``holoqa selftest`` is the descendant of the prior tool's ``dry-run.mjs``. It
proves every guardrail still bites, using only temporary files — no staging, no
browser, no network. If it ever goes green while a guardrail is broken, the
checklist this tool produces has stopped meaning anything.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from holoqa import BLOCKED, FAIL, PASS, __version__
from holoqa import plan as plan_module
from holoqa import run as run_module
from holoqa import verdict as verdict_module
from holoqa.observe import REDACT_KEYS, redact
from holoqa.run import GuardrailError, Run

SELFTEST_PLAN = """
meta:
  app: selftest
  base_url: http://localhost:0
known_behaviors:
  - id: KB-001
    title: known quirk
    applies_to: [S1]
stages:
  - id: S
    title: Selftest
steps:
  - id: S1
    stage: S
    title: Step with a screenshot requirement
    expect:
      - screenshot: required
  - id: S2
    stage: S
    title: Step that depends on S1
    depends_on: [S1]
    expect:
      - api: { method: GET, path: /thing, status: 200 }
  - id: S3
    stage: S
    title: Step requiring change
    expect:
      - changed: dom
"""


class SelftestFailure(AssertionError):
    pass


def _expect_refusal(label: str, action, fragment: str = "") -> str:
    try:
        action()
    except GuardrailError as error:
        if fragment and fragment.lower() not in str(error).lower():
            raise SelftestFailure(
                f"{label}: refused, but not for the expected reason: {error}"
            ) from None
        return f"ok   {label}"
    raise SelftestFailure(f"{label}: NOT refused — this guardrail is broken")


def selftest() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="holoqa-selftest-"))
    lines: list[str] = []
    try:
        plan_path = workspace / "plan.yaml"
        plan_path.write_text(SELFTEST_PLAN, encoding="utf-8")
        plan = plan_module.load(plan_path)
        run = Run.create(workspace / "run", plan, meta={"tester": "selftest"})

        # 3 — verdict vocabulary is closed
        lines.append(_expect_refusal(
            "guardrail 3  unknown verdict rejected",
            lambda: run.set_verdict("S1", "Lulus"), "not one of"))

        # 1 — no evidence, no pass
        lines.append(_expect_refusal(
            "guardrail 1  PASS without evidence rejected",
            lambda: run.set_verdict("S1", PASS), "no evidence"))

        # 2 — a non-pass must say why
        lines.append(_expect_refusal(
            "guardrail 2  FAIL without a cause rejected",
            lambda: run.set_verdict("S1", FAIL), "requires a note"))

        # 4 — evidence must exist on disk
        lines.append(_expect_refusal(
            "guardrail 4  missing evidence file rejected",
            lambda: run.attach("S1", kind="screenshot", path=workspace / "nope.png"),
            "does not exist"))

        empty = run.evidence_dir / "empty.png"
        empty.write_bytes(b"")
        lines.append(_expect_refusal(
            "guardrail 4  empty evidence file rejected",
            lambda: run.attach("S1", kind="screenshot", path=empty), "empty"))

        # The agent may never assert a pass.
        lines.append(_expect_refusal(
            "trust        agent cannot assert PASS",
            lambda: run.set_verdict("S1", PASS, by="agent"), "may only assert"))
        lines.append(_expect_refusal(
            "trust        agent cannot assert FAIL",
            lambda: run.set_verdict("S1", FAIL, note="x" * 20, by="agent"),
            "may only assert"))

        # A real capture, then a real pass.
        shot = run.evidence_dir / "step-s1-page.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pretend-image-bytes")
        run.attach("S1", kind="screenshot", path=shot, target="<page>")
        outcome, results, _ = verdict_module.evaluate(plan, run, "S1")
        if outcome != PASS:
            raise SelftestFailure(f"S1 should pass with a screenshot, got {outcome}: {results}")
        run.set_verdict("S1", outcome, assertions=results, by="holoqa")
        lines.append("ok   verdict      screenshot requirement satisfied -> PASS")

        # 9 — a missing capture is BLOCKED, never FAIL.
        outcome, results, _ = verdict_module.evaluate(plan, run, "S2")
        if outcome != BLOCKED:
            raise SelftestFailure(
                f"guardrail 9: uncaptured api assertion should BLOCK, got {outcome}")
        lines.append("ok   guardrail 9  uncaptured assertion -> BLOCKED, not FAIL")

        # changed: identical captures must not satisfy it.
        for name in ("a", "b"):
            same = run.evidence_dir / f"step-s3-{name}.json"
            same.write_text(json.dumps({"url": "u", "text": "identical"}), encoding="utf-8")
            run.attach("S3", kind="dom", path=same)
        outcome, results, _ = verdict_module.evaluate(plan, run, "S3")
        if outcome != FAIL:
            raise SelftestFailure(
                f"changed: two identical captures must FAIL, got {outcome}: {results}")
        run.set_verdict("S3", outcome, note="captures identical", assertions=results)
        lines.append("ok   verdict      identical captures do not prove a change")

        # 5 — a revised verdict keeps the superseded one.
        run.set_verdict("S3", BLOCKED, note="environment limit, rechecking later")
        step = run.read()["steps"]["S3"]
        if not step["revisions"] or step["revisions"][0]["from"] != FAIL:
            raise SelftestFailure("guardrail 5: verdict revision was not recorded")
        lines.append("ok   guardrail 5  revised verdict keeps an audit trail")

        # 7 — dependencies gate judgement.
        run.set_verdict("S1", BLOCKED, note="forced open to test the dependency gate")
        if run.blocked_dependencies(plan, "S2") != ["S1"]:
            raise SelftestFailure("guardrail 7: unmet dependency was not detected")
        lines.append("ok   guardrail 7  a step with an unmet dependency cannot be judged")

        # 8 — KB citations must exist.
        if plan.has_kb("KB-999"):
            raise SelftestFailure("guardrail 8: unknown KB id was accepted")
        lines.append("ok   guardrail 8  unknown known-behaviour id is rejected")

        # 6 — credential-bearing headers never reach disk.
        cleaned = redact({"Cookie": "secret", "X-Api-Key": "k", "content-type": "application/json"})
        if cleaned["Cookie"] != "<redacted>" or cleaned["content-type"] != "application/json":
            raise SelftestFailure("guardrail 6: header redaction is wrong")
        if not REDACT_KEYS.search("authorization"):
            raise SelftestFailure("guardrail 6: redaction pattern lost a key")
        lines.append("ok   guardrail 6  credential headers redacted in evidence")

        # Plan-level static checks, before anything can run.
        lines.append(_plan_refusal(
            workspace, "plan check   forward reference to an uncaptured variable",
            SELFTEST_PLAN + """
  - id: S4
    title: uses an unbound variable
    expect:
      - api: { method: GET, path: "/a/{nope}", status: 200 }
""", "no earlier step captures it"))

        lines.append(_plan_refusal(
            workspace, "plan check   unknown assertion kind rejected",
            SELFTEST_PLAN + """
  - id: S5
    title: invented assertion
    expect:
      - eventually_works: yes
""", "unknown assertion"))

        lines.append(_plan_refusal(
            workspace, "plan check   dependency on a later step rejected",
            """
meta: { app: selftest }
steps:
  - id: A
    title: first
    depends_on: [B]
    expect:
      - screenshot: required
  - id: B
    title: second
    expect:
      - screenshot: required
""", "does not come earlier"))

        lines.append(_plan_refusal(
            workspace, "plan check   unquoted {braces} explained, not just rejected",
            """
meta: { app: selftest }
steps:
  - id: A
    title: unquoted brace
    expect:
      - api: { method: GET, path: /a/{x}, status: 200 }
""", "must be quoted inside a flow mapping"))

        print("\n".join(lines))
        print(f"\n{len(lines)} guardrails verified. No staging, no network, no browser.")
        return 0
    except SelftestFailure as failure:
        print("\n".join(lines))
        print(f"\nFAILED: {failure}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _plan_refusal(workspace: Path, label: str, source: str, fragment: str) -> str:
    path = workspace / "bad-plan.yaml"
    path.write_text(source, encoding="utf-8")
    try:
        plan_module.load(path)
    except plan_module.PlanError as error:
        if fragment.lower() not in str(error).lower():
            raise SelftestFailure(f"{label}: wrong reason: {error}") from None
        return f"ok   {label}"
    raise SelftestFailure(f"{label}: NOT refused")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="holoqa", description=__doc__)
    parser.add_argument("--version", action="version", version=f"holoqa {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("mcp", help="run the stdio MCP server (default)")
    sub.add_parser("selftest", help="verify every guardrail, offline")
    validate = sub.add_parser("validate", help="validate a plan file")
    validate.add_argument("plan")
    status = sub.add_parser("status", help="show the active run")
    status.add_argument("--run-dir", default="")

    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()
    # A malformed plan is ordinary user input, not a crash. Print the reason
    # and the hint, never a traceback.
    if args.command == "validate":
        try:
            result = plan_module.lint(args.plan)
        except plan_module.PlanError as error:
            print(f"invalid plan: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    if args.command == "status":
        try:
            run = run_module.find(args.run_dir or None)
            plan = plan_module.load(run.read()["plan_path"])
        except (run_module.GuardrailError, plan_module.PlanError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(run.status(plan), indent=2))
        return 0

    from holoqa.mcp import main as serve

    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
