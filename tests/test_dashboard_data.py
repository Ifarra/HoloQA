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
    assert client.get("/api/runs/" + run.run_id).json()["status"] == "PASS"
