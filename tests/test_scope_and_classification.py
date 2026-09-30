"""Tier 1: assertion scope, and known-defect classification.

Both close the same class of hole the earlier soundness pass opened: a verdict
must be decided by the thing the plan pointed at, not by whatever happened to
be captured last, and a defect the plan already knows about must not read as a
new regression.
"""

from __future__ import annotations

import json

import pytest

from holoqa import BLOCKED, FAIL, PASS
from holoqa import plan as plan_module
from holoqa import report as report_module
from holoqa import verdict as verdict_module
from holoqa.run import Run
from tests.conftest import api_evidence


TWO_CALLS = """
meta:
  app: scope
steps:
  - id: S1
    title: A step that makes two calls and pins each assertion to one
    expect:
      - api: { method: POST, path: /api/orders, status: 201 }
      - json: { status: PENDING }
      - on: create
      - api: { method: GET, path: /api/orders/88, status: 200 }
      - json: { status: CONFIRMED }
      - on: read
"""

# The plan above is deliberately not valid YAML-shape for our format; build the
# real one programmatically so the intent stays readable.
SCOPED_PLAN = """
meta:
  app: scope
steps:
  - id: S1
    title: two calls, each assertion pinned to its own capture
    expect:
      - api: { method: POST, path: /api/orders, status: 201 }
      - { json: { status: PENDING }, scope: create }
      - { api: { method: GET, path: "/api/orders/88", status: 200 }, scope: read }
      - { json: { status: CONFIRMED }, scope: read }
"""


def _plan(tmp_path, body: str) -> plan_module.Plan:
    path = tmp_path / "scope.plan.yaml"
    path.write_text(body, encoding="utf-8")
    return plan_module.load(path)


def test_on_pins_an_assertion_to_a_named_capture(tmp_path):
    """Without a scope, json reads the last api capture — the wrong one here."""
    plan = _plan(tmp_path, SCOPED_PLAN)
    run = Run.create(tmp_path / "run", plan)

    api_evidence(
        run, "S1", "step-s1-create",
        method="POST", url="http://x/api/orders",
        status=201, body={"id": "88", "status": "PENDING"},
    )
    api_evidence(
        run, "S1", "step-s1-read",
        method="GET", url="http://x/api/orders/88",
        status=200, body={"id": "88", "status": "CONFIRMED"},
    )

    outcome, results, _ = verdict_module.evaluate(plan, run, "S1")

    assert outcome == PASS, results
    # The PENDING assertion is satisfied by `create`, not by the newest capture.
    pinned = next(item for item in results if item["expected"] == {"status": "PENDING"})
    assert pinned["ok"] is True


def test_without_a_scope_the_same_plan_would_read_the_wrong_capture(tmp_path):
    """Proves the scope is load-bearing: the unpinned version gets it wrong."""
    unpinned = SCOPED_PLAN.replace(", scope: read", "").replace(", scope: create", "")
    plan = _plan(tmp_path, unpinned)
    run = Run.create(tmp_path / "run", plan)

    api_evidence(
        run, "S1", "step-s1-create",
        method="POST", url="http://x/api/orders",
        status=201, body={"id": "88", "status": "PENDING"},
    )
    api_evidence(
        run, "S1", "step-s1-read",
        method="GET", url="http://x/api/orders/88",
        status=200, body={"id": "88", "status": "CONFIRMED"},
    )

    outcome, results, _ = verdict_module.evaluate(plan, run, "S1")

    # `json: {status: PENDING}` now reads the read-back capture and fails.
    assert outcome == FAIL
    assert any(item["ok"] is False for item in results)


def test_a_scope_naming_a_missing_capture_blocks_rather_than_guesses(tmp_path):
    plan = _plan(tmp_path, SCOPED_PLAN)
    run = Run.create(tmp_path / "run", plan)
    api_evidence(
        run, "S1", "step-s1-create",
        method="POST", url="http://x/api/orders",
        status=201, body={"id": "88", "status": "PENDING"},
    )

    outcome, results, _ = verdict_module.evaluate(plan, run, "S1")

    assert outcome == BLOCKED
    blocked = [item for item in results if item["ok"] is None]
    assert any("read" in item["detail"] for item in blocked)


def test_scope_is_accepted_by_the_loader(tmp_path):
    plan = _plan(tmp_path, SCOPED_PLAN)
    scoped = [scope for _k, _v, scope in plan.step("S1").assertions if scope]
    assert scoped == ["create", "read", "read"]


def test_a_bare_on_key_is_normalised_because_yaml_parses_it_as_true(tmp_path):
    """`on:` is a YAML 1.1 boolean; the author should not have to know that."""
    plan = _plan(tmp_path, """
meta:
  app: scope
steps:
  - id: S1
    title: written with a bare boolean key rather than scope
    expect:
      - { json: { status: CONFIRMED }, on: read }
""")
    assert plan.step("S1").assertions[0][2] == "read"


def test_scope_for_a_page_assertion_scopes_the_dom_capture(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: scope
steps:
  - id: P1
    title: two page captures, one pinned
    expect:
      - { text_contains: Second, scope: after }
""")
    run = Run.create(tmp_path / "run", plan)
    for name, text in (("step-p1-before", "First"), ("step-p1-after", "Second")):
        path = run.evidence_dir / f"{name}.json"
        path.write_text(json.dumps({
            "url": "http://x/", "title": "t", "text": text,
            "text_length": len(text), "truncated": False,
        }), encoding="utf-8")
        run.attach("P1", kind="dom", path=path)

    outcome, results, _ = verdict_module.evaluate(plan, run, "P1")

    assert outcome == PASS, results


# ------------------------------------------------- known-defect classification

KNOWN_DEFECT_PLAN = """
meta:
  app: kd
known_behaviors:
  - id: KB-008
    title: the guard redirects client-side, a known defect
    applies_to: [D1]
    verdict_hint: known_defect
steps:
  - id: D1
    title: a step that hits the known defect
    expect:
      - screenshot: required
  - id: D2
    title: an unrelated step
    expect:
      - screenshot: required
"""


def test_known_defect_is_separated_from_a_new_regression(tmp_path):
    plan = _plan(tmp_path, KNOWN_DEFECT_PLAN)
    run = Run.create(tmp_path / "run", plan)
    for step in ("D1", "D2"):
        shot = run.evidence_dir / f"{step}.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + step.encode())
        run.attach(step, kind="screenshot", path=shot)
    run.set_verdict("D1", FAIL, note="redirected to /login", by="holoqa")
    run.set_verdict("D2", FAIL, note="unexpected 500 on save", by="holoqa")

    text = report_module.markdown(run).read_text(encoding="utf-8")

    assert "Known defects" in text
    assert "New failures" in text
    new_section = text.split("## New failures")[1].split("##")[0]
    known_section = text.split("## Known defects")[1]
    assert "`D1`" in known_section
    assert "`D1`" not in new_section
    assert "`D2`" in new_section
