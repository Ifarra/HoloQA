"""The adjudicator. If these pass and the guardrails hold, a filled checklist means something."""

from __future__ import annotations

from holoqa import BLOCKED, FAIL, PASS
from holoqa import verdict as verdict_module
from holoqa.verdict import jsonpath, subset_matches
from tests.conftest import api_evidence, dom_evidence, screenshot_evidence


def test_all_assertions_satisfied_gives_pass_and_binds_a_variable(plan, run):
    api_evidence(
        run, "T1", "t1-create",
        method="POST", url="http://localhost:9999/things",
        status=201, body={"id": "thing_42", "status": "PENDING"},
    )
    screenshot_evidence(run, "T1", "t1-page")

    outcome, results, captures = verdict_module.evaluate(plan, run, "T1")

    assert outcome == PASS
    assert captures == {"thing_id": "thing_42"}
    assert run.vars()["thing_id"] == "thing_42"
    assert all(item["ok"] for item in results)


def test_wrong_status_is_a_failure(plan, run):
    api_evidence(
        run, "T1", "t1-create",
        method="POST", status=500, body={"id": "x", "status": "PENDING"},
    )
    screenshot_evidence(run, "T1", "t1-page")

    outcome, results, _ = verdict_module.evaluate(plan, run, "T1")

    assert outcome == FAIL
    api_result = next(item for item in results if item["kind"] == "api")
    assert api_result["ok"] is False
    assert "500" in api_result["detail"]


def test_missing_capture_blocks_rather_than_fails(plan, run):
    """Absence of evidence is not evidence of a defect."""
    screenshot_evidence(run, "T1", "t1-page")

    outcome, results, _ = verdict_module.evaluate(plan, run, "T1")

    assert outcome == BLOCKED
    assert any(item["ok"] is None for item in results)
    assert not any(item["ok"] is False for item in results)


def test_json_body_mismatch_is_a_failure(plan, run):
    api_evidence(
        run, "T1", "t1-create",
        method="POST", status=201, body={"id": "x", "status": "RUNNING"},
    )
    screenshot_evidence(run, "T1", "t1-page")

    outcome, results, _ = verdict_module.evaluate(plan, run, "T1")

    assert outcome == FAIL
    json_result = next(item for item in results if item["kind"] == "json")
    assert json_result["ok"] is False
    assert "RUNNING" in json_result["detail"]


def test_captured_variable_interpolates_into_a_later_step(plan, run):
    api_evidence(
        run, "T1", "t1-create",
        method="POST", status=201, body={"id": "thing_42", "status": "PENDING"},
    )
    screenshot_evidence(run, "T1", "t1-page")
    verdict_module.evaluate(plan, run, "T1")

    api_evidence(
        run, "T2", "t2-read",
        method="GET", url="http://localhost:9999/things/thing_42",
        status=200, body={"id": "thing_42"},
    )
    dom_evidence(run, "T2", "t2-page", url="http://localhost:9999/things/thing_42")

    outcome, results, _ = verdict_module.evaluate(plan, run, "T2")

    assert outcome == PASS, results


def test_unbound_variable_blocks_the_step(plan, run):
    """T2 references {thing_id}, which T1 never captured."""
    api_evidence(run, "T2", "t2-read", url="http://localhost:9999/things/1")
    dom_evidence(run, "T2", "t2-page")

    outcome, results, _ = verdict_module.evaluate(plan, run, "T2")

    assert outcome == BLOCKED
    assert any("thing_id" in item["detail"] for item in results)


def test_text_not_found_fails_but_truncated_text_blocks(plan, run):
    dom_evidence(run, "T2", "t2-page", text="Something else")
    api_evidence(run, "T2", "t2-read")
    run.bind("thing_id", "1")

    outcome, results, _ = verdict_module.evaluate(plan, run, "T2")
    text_result = next(item for item in results if item["kind"] == "text_contains")
    assert outcome == FAIL
    assert text_result["ok"] is False

    # A truncated page cannot prove absence.
    path = run.evidence_dir / "t2-page.json"
    payload = path.read_text(encoding="utf-8").replace('"truncated": false', '"truncated": true')
    path.write_text(payload, encoding="utf-8")
    run.attach("T2", kind="dom", path=path)

    outcome, results, _ = verdict_module.evaluate(plan, run, "T2")
    text_result = next(item for item in results if item["kind"] == "text_contains")
    assert text_result["ok"] is None
    assert outcome == BLOCKED


def test_changed_requires_two_differing_captures(plan, run):
    dom_evidence(run, "T3", "t3-a", text="progress 0%")
    outcome, results, _ = verdict_module.evaluate(plan, run, "T3")
    assert outcome == BLOCKED, "one capture cannot prove a change"

    dom_evidence(run, "T3", "t3-b", text="progress 0%")
    outcome, results, _ = verdict_module.evaluate(plan, run, "T3")
    assert outcome == FAIL, "identical captures mean nothing changed"

    dom_evidence(run, "T3", "t3-c", text="progress 47%")
    outcome, results, _ = verdict_module.evaluate(plan, run, "T3")
    assert outcome == PASS


def test_jsonpath_resolves_nested_and_indexed_paths():
    data = {"a": {"b": [{"c": 7}]}}
    assert jsonpath(data, "$.a.b[0].c") == (True, 7)
    assert jsonpath(data, "$.a.missing") == (False, None)
    assert jsonpath(data, "$.a.b[5]") == (False, None)


def test_subset_match_ignores_extra_keys_but_not_wrong_values():
    ok, _ = subset_matches({"status": "PENDING"}, {"status": "PENDING", "extra": 1})
    assert ok
    ok, detail = subset_matches({"status": "PENDING"}, {"status": "DONE"})
    assert not ok and "DONE" in detail
    ok, detail = subset_matches({"status": "x"}, {"other": 1})
    assert not ok and "absent" in detail
