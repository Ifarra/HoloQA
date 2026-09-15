from pathlib import Path

from holoqa.mcp_server import holoqa_project_inspect


def test_project_inspect_tool_returns_structured_result(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    result = holoqa_project_inspect(str(tmp_path))

    assert result["status"] == "ok"
    assert result["workspace_root"] == str(tmp_path.resolve())
    assert result["has_git"] is True
    assert "missing_configuration" in result
