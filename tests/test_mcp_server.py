from pathlib import Path

from holoqa.mcp_server import holoqa_initialize_project, holoqa_project_inspect


def test_project_inspect_tool_returns_structured_result(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    result = holoqa_project_inspect(str(tmp_path))

    assert result["status"] == "ok"
    assert result["workspace_root"] == str(tmp_path.resolve())
    assert result["has_git"] is True
    assert "missing_configuration" in result


def test_initialize_project_tool_is_idempotent(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    first = holoqa_initialize_project(str(tmp_path), str(tmp_path / "state.db"))
    second = holoqa_initialize_project(str(tmp_path), str(tmp_path / "state.db"))

    assert first["project_id"] == second["project_id"]
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["commit"]
