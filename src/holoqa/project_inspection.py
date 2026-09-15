from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class WorkspaceInspection(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace_root: Path
    has_git: bool
    detected_files: dict[str, bool]


def inspect_workspace(workspace_root: Path) -> WorkspaceInspection:
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"workspace does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")

    names = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod")
    return WorkspaceInspection(
        workspace_root=root,
        has_git=(root / ".git").exists(),
        detected_files={name: (root / name).is_file() for name in names},
    )
