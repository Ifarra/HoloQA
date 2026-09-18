from pathlib import Path

import pytest

from holoqa.runs import RunStore


def test_agent_session_records_recovery_and_verdict(tmp_path: Path):
    store = RunStore(tmp_path / "state.db")
    plan = store.create_plan("project-1", [{"test_id": "TC-001", "title": "Checkout"}])
    store.approve(plan.plan_id)

    run, token = store.open_agent_session(plan.plan_id, "cursor")
    assert run.status == "AGENT_CONNECTED"
    store.record_agent_event(run.run_id, token, sequence=1, event_type="agent_observation", test_id="TC-001", message="Cookie modal visible")
    store.record_agent_event(run.run_id, token, sequence=2, event_type="recovery_finished", test_id="TC-001", message="Cookie modal dismissed")
    store.record_agent_verdict(run.run_id, token, test_id="TC-001", status="PASS", message="Checkout completed")
    completed = store.complete(run.run_id, "PASS", "Agent completed run", store.results(run.run_id), execution_mode="agent")

    assert store.get(run.run_id).status == "PASS"
    assert store.get(run.run_id).execution_mode == "agent"
    assert [event["event_type"] for event in store.events(run.run_id)] == ["agent_connected", "agent_observation", "recovery_finished", "case_verdict", "run_finished"]


def test_agent_session_rejects_event_sequence_gaps(tmp_path: Path):
    store = RunStore(tmp_path / "state.db")
    plan = store.create_plan("project-1", [{"test_id": "TC-001"}])
    store.approve(plan.plan_id)
    run, token = store.open_agent_session(plan.plan_id, "claude")

    with pytest.raises(ValueError, match="sequence gap"):
        store.record_agent_event(run.run_id, token, sequence=2, event_type="agent_observation", message="out of order")


def test_agent_session_requires_approval(tmp_path: Path):
    store = RunStore(tmp_path / "state.db")
    plan = store.create_plan("project-1", [{"test_id": "TC-001"}])

    with pytest.raises(ValueError, match="approval"):
        store.open_agent_session(plan.plan_id, "cursor")
