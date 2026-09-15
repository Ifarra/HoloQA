from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from holoqa.project_inspection import inspect_workspace
from holoqa.project_store import ProjectStore

server = MCPServer(
    name="holoqa",
    version="0.1.0",
    description="Local MCP tools for HoloQA project initialization and testing.",
)


def holoqa_project_inspect(workspace_root: str) -> dict[str, Any]:
    """Inspect a local workspace without changing it."""
    inspection = inspect_workspace(Path(workspace_root))
    missing = [name for name, present in inspection.detected_files.items() if not present]
    return {
        "status": "ok",
        "workspace_root": str(inspection.workspace_root),
        "has_git": inspection.has_git,
        "detected_files": inspection.detected_files,
        "missing_configuration": missing,
    }


server.add_tool(
    holoqa_project_inspect,
    name="holoqa_project_inspect",
    description="Inspect the current local workspace and report project configuration signals.",
    structured_output=True,
)


def holoqa_initialize_project(workspace_root: str, state_database: str | None = None) -> dict[str, Any]:
    """Create or update a project and persist a snapshot for the current workspace."""
    workspace = Path(workspace_root).expanduser().resolve()
    database = Path(state_database) if state_database else workspace / ".holoqa" / "state.db"
    initialization = ProjectStore(database).initialize(workspace)
    return {
        "status": "completed",
        **initialization.model_dump(),
        "next_actions": ["query_codegraph", "create_run_plan"],
    }


server.add_tool(
    holoqa_initialize_project,
    name="holoqa_initialize_project",
    description="Initialize a HoloQA project for a local workspace and create a pinned snapshot.",
    structured_output=True,
)


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
