"""Tier 2: machine-checked preconditions.

The reviewer's Proctor run had 64 BLOCKED whose only justification was a
sentence the agent wrote. That inverts the tool's whole premise: the agent
decides what to look at, HoloQA decides the verdict. `requires:` moves the
decision back to the plan, and gives the report a root cause it can group on.
"""

from __future__ import annotations

import json

import pytest

from holoqa import BLOCKED, PASS
from holoqa import plan as plan_module
from holoqa import report as report_module
from holoqa import verdict as verdict_module
from holoqa.run import Run
from tests.conftest import api_evidence, dom_evidence


REQUIRES_PLAN = """
meta:
  app: requires
steps:
  - id: R0
    title: the fixture is captured here
    expect:
      - screenshot: required
  - id: R1
    title: needs a fixture that is present
    requires:
      - api: { method: GET, path: /api/availability/windows, status: 200 }
    expect:
      - text_contains: Booking
  - id: R2
    title: needs a fixture that is absent
    requires:
      - api: { method: GET, path: /api/availability/windows, status: 200 }
    expect:
      - text_contains: Booking
  - id: R3
    title: needs something never captured at all
    requires:
      - api: { method: GET, path: /api/never-called, status: 200 }
    expect:
      - text_contains: Booking
"""


def _plan(tmp_path, body: str = REQUIRES_PLAN) -> plan_module.Plan:
    path = tmp_path / "requires.plan.yaml"
    path.write_text(body, encoding="utf-8")
    return plan_module.load(path)


def test_a_met_requirement_lets_the_step_be_judged(tmp_path):
    """The fixture is captured in its own step, then consumed by later ones."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    # R0 is the probe step that captures the shared fixture.
    api_evidence(
        run, "R0", "fixture",
        method="GET", url="http://x/api/availability/windows", status=200,
    )
    dom_evidence(run, "R1", "page", text="Booking opens at 9")

    outcome, results, _ = verdict_module.evaluate(plan, run, "R1")

    assert outcome == PASS, results


def test_a_precondition_reads_a_fixture_captured_by_an_earlier_step(tmp_path):
    """This is the normal shape, and searching only the step's own captures
    would report every precondition as unmet."""
    plan = _plan(tmp_path, """
meta:
  app: requires
steps:
  - id: P0
    title: probe the environment once
    expect:
      - api: { method: GET, path: /api/health, status: 200 }
  - id: P1
    title: a later step depends on that probe
    requires:
      - api: { method: GET, path: /api/health, status: 200 }
    expect:
      - screenshot: required
""")
    run = Run.create(tmp_path / "run", plan)
    api_evidence(run, "P0", "probe", method="GET", url="http://x/api/health", status=200)
    shot = run.evidence_dir / "p1.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x")
    run.attach("P1", kind="screenshot", path=shot)

    outcome, results, _ = verdict_module.evaluate(plan, run, "P1")

    assert outcome == PASS, results


def test_an_unmet_requirement_blocks_with_the_plans_own_cause(tmp_path):
    """The fixture returns 200 in R1 but the assertion is violated here."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    # The fixture call itself came back 404 — the precondition is not satisfied.
    api_evidence(
        run, "R2", "fixture",
        method="GET", url="http://x/api/availability/windows", status=404,
    )
    dom_evidence(run, "R2", "page", text="Booking")

    outcome, results, _ = verdict_module.evaluate(plan, run, "R2")

    assert outcome == BLOCKED
    assert results[0]["kind"] == "requires"
    assert "availability/windows" in results[0]["detail"]


def test_a_requirement_with_no_capture_at_all_blocks(tmp_path):
    """An unverifiable precondition is unmet, not ignored."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    dom_evidence(run, "R3", "page", text="Booking")

    outcome, results, _ = verdict_module.evaluate(plan, run, "R3")

    assert outcome == BLOCKED
    assert "never-called" in results[0]["detail"]


def test_a_requirement_beats_a_satisfied_assertion(tmp_path):
    """If the step could not run, a passing assertion is meaningless."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    api_evidence(
        run, "R2", "fixture",
        method="GET", url="http://x/api/availability/windows", status=503,
    )
    dom_evidence(run, "R2", "page", text="Booking")

    outcome, _, _ = verdict_module.evaluate(plan, run, "R2")

    assert outcome == BLOCKED


def test_requirements_are_parsed_and_interpolated(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: requires
steps:
  - id: X1
    title: capture an id
    expect:
      - capture: { session_id: $.id }
  - id: X2
    title: needs the session from X1
    requires:
      - api: { method: GET, path: "/api/sessions/{session_id}", status: 200 }
    expect:
      - screenshot: required
""")
    assert plan.step("X2").requirements[0][0] == "api"
    assert "{session_id}" in plan.step("X2").requirements[0][1]["path"]


# ----------------------------------------------------- report grouping

def test_the_report_groups_blocked_steps_by_root_cause(tmp_path):
    """Three steps sharing one missing fixture collapse into one line."""
    plan = _plan(tmp_path, """
meta:
  app: requires
steps:
  - id: R1
    title: first consumer of the fixture
    requires:
      - api: { method: GET, path: /api/availability/windows, status: 200 }
    expect:
      - screenshot: required
  - id: R2
    title: second consumer of the same fixture
    requires:
      - api: { method: GET, path: /api/availability/windows, status: 200 }
    expect:
      - screenshot: required
  - id: R3
    title: third consumer of the same fixture
    requires:
      - api: { method: GET, path: /api/availability/windows, status: 200 }
    expect:
      - screenshot: required
""")
    run = Run.create(tmp_path / "run", plan)
    for step_id in ("R1", "R2", "R3"):
        # The shared fixture is down, so all three block for the same reason.
        api_evidence(
            run, step_id, f"{step_id}-fixture",
            method="GET", url="http://x/api/availability/windows", status=503,
        )
        outcome, results, _ = verdict_module.evaluate(plan, run, step_id)
        run.set_verdict(
            step_id, outcome,
            note=results[0]["detail"], assertions=results, by="holoqa",
        )

    text = report_module.markdown(run).read_text(encoding="utf-8")

    assert "## Blocked by root cause" in text
    section = text.split("## Blocked by root cause")[1].split("##")[0]
    # One line, not three: that is the whole point of grouping.
    assert "3 step(s)" in section
    assert section.count("requires api not satisfied") == 1
    assert "`R1`" in section and "`R2`" in section and "`R3`" in section


def test_blocked_cause_is_recorded_on_the_step(tmp_path):
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    dom_evidence(run, "R3", "page", text="Booking")
    outcome, results, _ = verdict_module.evaluate(plan, run, "R3")
    run.set_verdict("R3", outcome, note=results[0]["detail"], assertions=results)

    assert "requires" in run.read()["steps"]["R3"]["blocked_cause"]


def test_an_agent_prose_block_has_no_groupable_cause(tmp_path):
    """Free text cannot be grouped, and must not pretend to be."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    run.set_verdict("R1", BLOCKED, note="I could not find the booking button", by="agent")

    assert run.read()["steps"]["R1"]["blocked_cause"] == ""
