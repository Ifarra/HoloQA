"""Regression tests for the soundness fixes.

Each test here reproduces a bug that made a step report the wrong verdict —
a PASS for something never proven, or a FAIL for something that never ran.
They are written against the *pre-fix* behaviour, so if one of them ever goes
green again while the fix is reverted, the hole is back.
"""

from __future__ import annotations

import json

import pytest

from holoqa import BLOCKED, FAIL, PASS
from holoqa import plan as plan_module
from holoqa import verdict as verdict_module
from holoqa.run import Run
from tests.conftest import api_evidence, dom_evidence


def _plan(tmp_path, body: str) -> plan_module.Plan:
    path = tmp_path / "soundness.plan.yaml"
    path.write_text(body, encoding="utf-8")
    return plan_module.load(path)


# --------------------------------------------------------------- blocked_if

BLOCKED_IF_PLAN = """
meta:
  app: soundness
steps:
  - id: K1
    title: An assertion that is violated, but the plan declared an environment cause
    blocked_if: the orders service is disabled in this environment
    expect:
      - api: { method: GET, path: /api/orders, status: 200 }
  - id: K2
    title: The same violation with no blocked_if
    expect:
      - api: { method: GET, path: /api/orders, status: 200 }
"""


def test_blocked_if_downgrades_a_violation_to_blocked(tmp_path):
    """Before the fix `blocked_if` was declared, documented, and never read."""
    plan = _plan(tmp_path, BLOCKED_IF_PLAN)
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "K1", "k1", method="GET", url="http://x/api/orders", status=503)

    outcome, results, _ = verdict_module.evaluate(plan, run, "K1")

    assert outcome == BLOCKED, "a declared environment cause must not read as a defect"
    assert any(item["ok"] is None for item in results)
    # The violation is not hidden: the plan's cause AND the real detail survive.
    detail = results[0]["detail"]
    assert "blocked_if" in detail
    assert "disabled in this environment" in detail
    assert "503" in detail


def test_without_blocked_if_the_same_violation_is_still_fail(tmp_path):
    """The downgrade must be opt-in per step, not a blanket softening."""
    plan = _plan(tmp_path, BLOCKED_IF_PLAN)
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "K2", "k2", method="GET", url="http://x/api/orders", status=503)

    outcome, _, _ = verdict_module.evaluate(plan, run, "K2")

    assert outcome == FAIL


def test_blocked_if_does_not_rescue_a_passing_step(tmp_path):
    """A satisfied step with a blocked_if still PASSes; the field only downgrades."""
    plan = _plan(tmp_path, BLOCKED_IF_PLAN)
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "K1", "k1", method="GET", url="http://x/api/orders", status=200)

    outcome, _, _ = verdict_module.evaluate(plan, run, "K1")

    assert outcome == PASS


# ----------------------------------------------------------- api path scope

def test_api_path_does_not_match_an_unrelated_endpoint(tmp_path):
    """`/api/orders` used to match `/api/orders-archive`."""
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: orders returns 200
    expect:
      - api: { method: GET, path: /api/orders, status: 200 }
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "A1", "a1", method="GET", url="http://x/api/orders-archive", status=200)

    outcome, results, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == BLOCKED, "an unrelated endpoint must not satisfy the assertion"
    assert "no capture of" in results[0]["detail"]


def test_a_healthy_lookalike_capture_cannot_mask_a_broken_endpoint(tmp_path):
    """The order-of-capture hole: broken real endpoint, healthy lookalike last."""
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: orders returns 200
    expect:
      - api: { method: GET, path: /api/orders, status: 200 }
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "A1", "real", method="GET", url="http://x/api/orders", status=500)
    api_evidence(run, "A1", "lookalike", method="GET", url="http://x/api/orders-archive", status=200)

    outcome, _, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == FAIL, "the real endpoint returned 500; that must decide the verdict"


def test_api_path_exact_ignores_the_query_string(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: exact path
    expect:
      - api: { method: GET, path: /api/orders, path_exact: true, status: 200 }
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "A1", "a1", method="GET", url="http://x/api/orders?page=2", status=200)

    outcome, _, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == PASS


def test_api_path_regex_is_available(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: regex path
    expect:
      - api: { method: GET, path_regex: "^/api/orders/[0-9]+$", status: 200 }
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "A1", "a1", method="GET", url="http://x/api/orders/88", status=200)

    outcome, _, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == PASS


def test_unknown_api_key_is_refused_at_load(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: typo
    expect:
      - api: { method: GET, path: /x, statsu: 200 }
""")
    assert "unknown key" in str(caught.value)


# ------------------------------------------------- text_not_contains truncation

def test_text_not_contains_on_a_truncated_page_blocks(tmp_path):
    """The mirror of the text_contains hole: absence cannot be proven past 20k."""
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: T1
    title: forbidden text must be absent
    expect:
      - text_not_contains: FORBIDDEN_STRING
""")
    run = Run.create(tmp_path / "run", plan)
    path = run.evidence_dir / "t1.json"
    path.write_text(json.dumps({
        "url": "http://x/", "title": "t", "text": "short visible text",
        "text_length": 90000, "truncated": True,
    }), encoding="utf-8")
    run.attach("T1", kind="dom", path=path)

    outcome, results, _ = verdict_module.evaluate(plan, run, "T1")

    assert outcome == BLOCKED
    assert results[0]["ok"] is None
    assert "truncated" in results[0]["detail"]


def test_text_not_contains_still_passes_on_a_complete_page(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: T1
    title: forbidden text must be absent
    expect:
      - text_not_contains: FORBIDDEN_STRING
""")
    run = Run.create(tmp_path / "run", plan)
    dom_evidence(run, "T1", "t1", text="a complete page with no forbidden string")

    outcome, _, _ = verdict_module.evaluate(plan, run, "T1")

    assert outcome == PASS


def test_text_not_contains_still_fails_when_present(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: T1
    title: forbidden text must be absent
    expect:
      - text_not_contains: FORBIDDEN_STRING
""")
    run = Run.create(tmp_path / "run", plan)
    dom_evidence(run, "T1", "t1", text="here is FORBIDDEN_STRING on the page")

    outcome, _, _ = verdict_module.evaluate(plan, run, "T1")

    assert outcome == FAIL


# ----------------------------------------------------------- verdict_hint

def test_unknown_verdict_hint_is_refused_at_load(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
known_behaviors:
  - id: KB-001
    title: a quirk
    verdict_hint: known_defekt
steps:
  - id: T1
    title: x
    expect:
      - screenshot: required
""")
    assert "verdict_hint" in str(caught.value)


def test_known_defect_hint_is_accepted(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: soundness
known_behaviors:
  - id: KB-008
    title: a defect the plan already knows about
    applies_to: [T1]
    verdict_hint: known_defect
steps:
  - id: T1
    title: x
    expect:
      - screenshot: required
""")
    assert plan.known_behaviors[0].verdict_hint == "known_defect"
