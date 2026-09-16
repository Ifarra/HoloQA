from pathlib import Path

from fastapi.testclient import TestClient

from holoqa.dashboard import create_app
from holoqa.project_store import ProjectStore
from holoqa.runs import RunStore


def test_dashboard_shows_initialized_project_and_run(tmp_path: Path):
    state = tmp_path / "state.db"
    project = ProjectStore(state).initialize(tmp_path)
    plan = RunStore(state).create_plan(project.project_id, [{"test_id": "TC-001"}])
    RunStore(state).approve(plan.plan_id)
    run = RunStore(state).execute(plan.plan_id)

    client = TestClient(create_app(state))
    page = client.get("/")
    assert project.project_name in page.text
    assert run.run_id in page.text
    assert "HoloQA Control Surface" in page.text
    assert 'data-filter="projects"' in page.text
    assert 'id="dashboard-search"' in page.text
    assert client.get("/api/runs/" + run.run_id).json()["status"] == "PASS"


def test_dashboard_has_operational_empty_states(tmp_path: Path):
    client = TestClient(create_app(tmp_path / "state.db"))
    page = client.get("/")
    assert "No projects initialized yet." in page.text
    assert "No runs executed yet." in page.text
    assert 'aria-live="polite"' in page.text


def test_dashboard_exposes_live_events_and_cancel_control(tmp_path: Path):
    state = tmp_path / "state.db"
    project = ProjectStore(state).initialize(tmp_path)
    plan = RunStore(state).create_plan(project.project_id, [{"test_id": "TC-LIVE"}])
    RunStore(state).approve(plan.plan_id)
    run = RunStore(state).queue(plan.plan_id)
    RunStore(state).mark_running(run.run_id)
    RunStore(state).progress(run.run_id, event_type="case_started", test_id="TC-LIVE", step="Open /", message="Executing browser step")

    client = TestClient(create_app(state))
    events = client.get(f"/api/runs/{run.run_id}/events").json()
    assert [event["event_type"] for event in events["events"]] == ["run_queued", "run_started", "case_started"]
    assert client.post(f"/api/runs/{run.run_id}/cancel").json()["message"] == "Cancellation requested"


def test_dashboard_plan_controls_require_approval_and_expose_retry(tmp_path: Path):
    state = tmp_path / "state.db"
    project = ProjectStore(state).initialize(tmp_path)
    plan = RunStore(state).create_plan(project.project_id, [{"test_id": "TC-APPROVAL"}])
    client = TestClient(create_app(state))

    assert client.get("/api/plans").json()["plans"][0]["approved"] is False
    blocked = client.post(f"/api/plans/{plan.plan_id}/start", json={"base_url": "http://localhost:3000"}).json()
    assert blocked["status"] == "BLOCKED"
    retried = client.post(f"/api/runs/{blocked['run_id']}/retry", json={}).json()
    assert retried["status"] == "BLOCKED"
