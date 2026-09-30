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


# ------------------------------------------- api without a status key (F-8)

def test_an_api_assertion_without_a_status_is_refused_at_load(tmp_path):
    """It used to PASS on any response, including a 500 on a broken endpoint."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: no status, so nothing is asserted
    expect:
      - api: { method: GET, path: /api/broken }
""")
    assert "status" in str(caught.value)


def test_a_requires_precondition_without_a_status_is_also_refused(tmp_path):
    """The same hole through `requires:`, which the assertion check missed."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: a precondition that asserts nothing
    requires:
      - api: { method: GET, path: /api/fixtures }
    expect:
      - screenshot: required
""")
    assert "status" in str(caught.value)


# ----------------------------------- unknown keys in file/header (F-7)

def test_an_unknown_file_key_is_refused_at_load(tmp_path):
    """`size_gtt` was accepted, ignored, and an 8-byte file passed for a 1 MB one."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: typo in a file assertion
    expect:
      - file: { size_gtt: 1000000 }
""")
    assert "unknown key" in str(caught.value)
    assert "size_gtt" in str(caught.value)


def test_an_unknown_header_key_is_refused_at_load(tmp_path):
    """`contain` is not `contains`: it degraded the check to "header exists"."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: typo in a header assertion
    expect:
      - header: { name: cache-control, contain: no-store }
""")
    assert "unknown key" in str(caught.value)


def test_a_header_assertion_needs_a_name(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: a header with nothing to look for
    expect:
      - header: { contains: whatever }
""")
    assert "needs a name" in str(caught.value)


def test_a_header_assertion_cannot_set_two_matchers(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: contradictory header matchers
    expect:
      - header: { name: etag, contains: abc, equals: def }
""")
    assert "pick one" in str(caught.value)


# ------------------------------------------------- invalid regex (F-10)

def test_an_invalid_regex_is_refused_at_load_not_at_judge_time(tmp_path):
    """It used to load, then raise an uncaught `re.error` mid-judgement."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: an unclosed character set
    expect:
      - url_matches: "([unclosed"
""")
    assert "invalid regular expression" in str(caught.value)


def test_an_invalid_regex_inside_an_api_path_regex_is_refused(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: an unclosed group in path_regex
    expect:
      - api: { method: GET, path_regex: "^(/api", status: 200 }
""")
    assert "invalid regular expression" in str(caught.value)


# ------------------------------------------------------ changed kind (F-12)

def test_a_misspelled_changed_kind_is_refused_at_load(tmp_path):
    """It used to report BLOCKED as if a capture were missing."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: domm is not a capture kind
    expect:
      - changed: domm
""")
    assert "changed must compare" in str(caught.value)


def test_changed_honours_a_scope(tmp_path):
    """`scope` used to be accepted on `changed` and silently dropped.

    Two captures of two *different* subjects used to count as a change. The
    scope pins the comparison to the subject the assertion is about, so a plan
    testing `/api/orders` cannot be satisfied by a second call elsewhere.
    """
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: only the pinned subject is compared
    expect:
      - { changed: api, scope: /api/orders }
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "A1", "orders-1", url="http://x/api/orders", status=200)
    # A second capture of a DIFFERENT endpoint. Unpinned, this was the "change".
    api_evidence(run, "A1", "unrelated", url="http://x/api/other", status=200)

    outcome, results, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == BLOCKED, (
        "one capture of the pinned subject proves nothing; the other endpoint "
        "must not stand in for a change"
    )
    assert "for scope" in results[0]["detail"]


def test_changed_with_a_scope_passes_when_the_pinned_subject_moved(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: the pinned subject must change
    expect:
      - { changed: api, scope: /api/orders }
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(
        run, "A1", "orders-before", url="http://x/api/orders",
        status=200, body={"state": "PENDING"},
    )
    api_evidence(
        run, "A1", "orders-after", url="http://x/api/orders",
        status=200, body={"state": "DONE"},
    )

    outcome, _, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == PASS


# --------------------------------------------------- empty plan (F-9)

def test_a_plan_with_no_steps_is_refused(tmp_path):
    """It used to validate, package, and decide RELEASE."""
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: soundness
steps: []
""")
    assert "at least one step" in str(caught.value)


# --------------------------------- blocked_if scoped to one assertion (F-6)

CAUSE_PLAN = """
meta:
  app: soundness
steps:
  - id: C1
    title: one expected failure and one unrelated one
    expect:
      - { api: { method: GET, path: /api/orders, status: 200 },
          blocked_if: the orders service is disabled in this environment }
      - text_contains: Checkout
"""


def test_a_blocked_if_on_one_assertion_does_not_excuse_another(tmp_path):
    """A session that expired is a defect, not the feature flag the plan named."""
    plan = _plan(tmp_path, CAUSE_PLAN)
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "C1", "c1", method="GET", url="http://x/api/orders", status=503)
    page = run.evidence_dir / "c1-page.json"
    page.write_text(json.dumps({
        "url": "http://x/login", "title": "Sign in",
        "text": "Session expired. Please sign in again.",
        "text_length": 36, "truncated": False,
    }), encoding="utf-8")
    run.attach("C1", kind="dom", path=page)

    outcome, results, _ = verdict_module.evaluate(plan, run, "C1")

    assert outcome == FAIL, "the unrelated UI failure must still be a defect"
    excused = next(item for item in results if item["kind"] == "api")
    unexcused = next(item for item in results if item["kind"] == "text_contains")
    assert excused["ok"] is None, "the declared cause still downgrades its own assertion"
    assert unexcused["ok"] is False, "the assertion with no cause still fails"
    assert "blocked_if" not in unexcused, "a FAIL must not carry an excuse it did not make"


def test_a_step_level_blocked_if_still_covers_its_assertions(tmp_path):
    """The per-assertion form is a narrowing, not a replacement."""
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: C1
    title: the whole step is allowed to be disabled here
    blocked_if: the orders service is disabled in this environment
    expect:
      - api: { method: GET, path: /api/orders, status: 200 }
      - screenshot: required
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "C1", "c1", method="GET", url="http://x/api/orders", status=503)

    outcome, results, _ = verdict_module.evaluate(plan, run, "C1")

    assert outcome == BLOCKED, results
    assert all(item["ok"] is None for item in results if item["kind"] == "api")


def test_a_step_level_cause_can_be_narrowed_to_the_assertion_that_needs_it(tmp_path):
    """The supported narrowing: move the cause off the step, onto the assertion.

    There is deliberately no way to opt one assertion *out* of a step-level
    cause — an empty `blocked_if` falls back to the step's. A step with mixed
    expectations should declare the cause where it applies.
    """
    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: C1
    title: only the orders assertion may be excused
    expect:
      - { api: { method: GET, path: /api/orders, status: 200 },
          blocked_if: the orders service is disabled in this environment }
      - text_contains: Checkout
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "C1", "c1", method="GET", url="http://x/api/orders", status=503)
    page = run.evidence_dir / "c1-page.json"
    page.write_text(json.dumps({
        "url": "http://x/login", "title": "Sign in", "text": "Session expired.",
        "text_length": 16, "truncated": False,
    }), encoding="utf-8")
    run.attach("C1", kind="dom", path=page)

    outcome, results, _ = verdict_module.evaluate(plan, run, "C1")

    assert outcome == FAIL, results
    page_result = next(item for item in results if item["kind"] == "text_contains")
    assert page_result["ok"] is False


# --------------------------- a second capture does not overwrite the first (F-2)

def test_a_second_capture_of_the_same_kind_is_kept(tmp_path, monkeypatch):
    """Two captures used to share one file name, so the first was overwritten.

    The index then held two records pointing at the same bytes, and `changed` —
    the assertion that exists to compare two captures taken apart — could only
    ever see the survivor. The capture is stubbed here so the test stays
    offline; the real driver is exercised by tests/test_e2e_browser.py.
    """
    from holoqa import mcp as mcp_module
    from holoqa import observe as observe_module

    plan = _plan(tmp_path, """
meta:
  app: soundness
steps:
  - id: A1
    title: two page captures of the same step
    expect:
      - changed: dom
""")
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))

    counter = {"n": 0}

    def fake_dom(destination, *, session=""):
        counter["n"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "url": "http://x/", "title": "t", "text": f"capture {counter['n']}",
            "text_length": 10, "truncated": False,
        }), encoding="utf-8")
        return {"url": "http://x/", "title": "t", "text_length": 10, "truncated": False}

    monkeypatch.setattr(observe_module, "dom", fake_dom)

    first = mcp_module.holoqa_observe("A1", "dom")
    second = mcp_module.holoqa_observe("A1", "dom")

    assert first["file"] != second["file"], (
        "the second capture overwrote the first, so `changed` has nothing to compare"
    )
    assert first["sha256"] != second["sha256"]
    on_disk = sorted(item.name for item in run.evidence_dir.iterdir())
    assert len(on_disk) == 2, on_disk

    # And the two captures are what `changed` compares.
    outcome, results, _ = verdict_module.evaluate(plan, run, "A1")
    assert outcome == PASS, results
