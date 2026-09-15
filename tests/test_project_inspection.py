from pathlib import Path

import pytest

from holoqa.project_inspection import inspect_workspace


def test_inspect_workspace_returns_workspace_and_git_information(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    result = inspect_workspace(tmp_path)

    assert result.workspace_root == tmp_path.resolve()
    assert result.has_git is True
    assert result.detected_files["package.json"] is True


def test_inspect_workspace_rejects_missing_workspace(tmp_path: Path):
    missing = tmp_path / "missing"

    with pytest.raises(ValueError, match="workspace does not exist"):
        inspect_workspace(missing)
