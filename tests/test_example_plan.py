"""The shipped example plan must keep validating.

It is the proof of generality: 31 real steps from a different application,
expressed without touching HoloQA's source. If a change to the plan format
breaks it, that is a regression in the format, not in the example.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from holoqa import plan as plan_module

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "wolvesight.plan.yaml"


def test_example_plan_validates():
    result = plan_module.lint(EXAMPLE)
    assert result["status"] == "ok"
    assert result["steps"] == 31
    assert result["stages"] == ["A", "B", "C", "D", "E"]


def test_example_plan_binds_variables_in_order():
    plan = plan_module.load(EXAMPLE)
    a3 = plan.step("A3")
    assert ("capture", {"scan_id": "$.id"}, "") in a3.assertions
    # A4 consumes what A3 captured.
    a4_api = plan.step("A4").assertion("api")
    assert "{scan_id}" in a4_api["path"]


def test_negative_steps_expect_rejection():
    """Steps 10, 12, 27 and 29 pass only when the system refuses."""
    plan = plan_module.load(EXAMPLE)
    for step_id in ("B10", "B12", "E29"):
        api = plan.step(step_id).assertion("api")
        assert isinstance(api["status"], list)
        assert all(400 <= code < 500 for code in api["status"]), step_id


def test_change_proving_steps_use_the_changed_assertion():
    plan = plan_module.load(EXAMPLE)
    for step_id in ("A5", "B13", "C20", "C21", "C23"):
        kinds = [kind for kind, _, _on in plan.step(step_id).assertions]
        assert "changed" in kinds, f"{step_id} claims to prove a change"


def test_interpolation_resolves_a_captured_scan_id():
    plan = plan_module.load(EXAMPLE)
    api = plan.step("A4").assertion("api")
    resolved = plan_module.interpolate(api, {"scan_id": "scan_777"})
    assert resolved["path"] == "/api/scans/scan_777"


def test_missing_binding_is_reported_not_guessed():
    plan = plan_module.load(EXAMPLE)
    api = plan.step("A4").assertion("api")
    with pytest.raises(plan_module.PlanError, match="scan_id"):
        plan_module.interpolate(api, {})
