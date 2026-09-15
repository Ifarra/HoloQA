from pathlib import Path

from holoqa.dashboard import create_app
from holoqa.project_store import ProjectStore


def test_initialize_project_is_idempotent(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    store = ProjectStore(tmp_path / "state.db")

    first = store.initialize(tmp_path)
    second = store.initialize(tmp_path)

    assert first.project_id == second.project_id
    assert first.snapshot_id != second.snapshot_id
    assert second.commit


def test_dashboard_health_and_project_page(tmp_path: Path):
    app = create_app(tmp_path / "state.db")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/")
    assert response.status_code == 200
    assert "HoloQA" in response.text
    assert "Projects" in response.text
