from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from holoqa.project_inspection import inspect_workspace
from holoqa.project_store import ProjectStore
from holoqa.reports import export_html, export_json
from holoqa.runs import RunStore
from holoqa.workbook import export_results, import_workbook

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


def holoqa_initialize_project(workspace_root: str, state_database: str | None = None) -> dict[str, Any]:
    """Create or update a project and persist a snapshot for the current workspace."""
    workspace = Path(workspace_root).expanduser().resolve()
    database = Path(state_database) if state_database else workspace / ".holoqa" / "state.db"
    initialization = ProjectStore(database).initialize(workspace)
    return {
        "status": "completed",
        **initialization.model_dump(),
        "next_actions": ["holoqa_import_test_workbook", "holoqa_create_run_plan"],
    }


def holoqa_import_test_workbook(workbook_path: str, state_database: str) -> dict[str, Any]:
    """Validate and normalize a supported XLSX test workbook without modifying it."""
    cases = import_workbook(Path(workbook_path))
    return {"status": "validated", "source": str(Path(workbook_path).resolve()), "cases": cases}


def holoqa_create_run_plan(project_id: str, cases: list[dict[str, Any]], state_database: str) -> dict[str, Any]:
    """Create a reviewable test plan; this operation does not execute tests."""
    plan = RunStore(Path(state_database)).create_plan(project_id, cases)
    return {"status": "awaiting_approval", **plan.model_dump(), "requires_approval": True}


def holoqa_approve_run(plan_id: str, state_database: str) -> dict[str, Any]:
    """Approve an exact plan before execution."""
    RunStore(Path(state_database)).approve(plan_id)
    return {"status": "approved", "plan_id": plan_id}


def holoqa_execute_run(plan_id: str, state_database: str) -> dict[str, Any]:
    """Execute an approved MVP plan and return its run state."""
    run = RunStore(Path(state_database)).execute(plan_id)
    return run.model_dump()


def holoqa_get_run_status(run_id: str, state_database: str) -> dict[str, Any]:
    """Read a run status from the local MVP state store."""
    store = RunStore(Path(state_database))
    run = store.get(run_id)
    return run.model_dump()


def holoqa_export_report(
    run_id: str,
    state_database: str,
    source_workbook: str | None = None,
    output_directory: str | None = None,
) -> dict[str, Any]:
    """Generate MVP JSON, HTML, and optional annotated XLSX report artifacts."""
    database = Path(state_database)
    run = RunStore(database).get(run_id).model_dump()
    output = Path(output_directory) if output_directory else database.parent / "artifacts" / run_id
    results = [{"test_id": "MVP", "status": run["status"], "actual_result": run["message"], "evidence": ""}]
    json_path = export_json(output / "report.json", run, results)
    html_path = export_html(output / "report.html", run, results)
    artifacts = {"json": str(json_path), "html": str(html_path)}
    if source_workbook:
        workbook_path = output / "results.xlsx"
        export_results(Path(source_workbook), workbook_path, results)
        artifacts["xlsx"] = str(workbook_path)
    return {"status": "completed", "run_id": run_id, "artifacts": artifacts}


server.add_tool(holoqa_project_inspect, name="holoqa_project_inspect", description="Inspect the local workspace.", structured_output=True)
server.add_tool(holoqa_initialize_project, name="holoqa_initialize_project", description="Initialize a HoloQA project and pinned snapshot.", structured_output=True)
server.add_tool(holoqa_import_test_workbook, name="holoqa_import_test_workbook", description="Validate and normalize a supported XLSX test workbook.", structured_output=True)
server.add_tool(holoqa_create_run_plan, name="holoqa_create_run_plan", description="Create a reviewable run plan without executing it.", structured_output=True)
server.add_tool(holoqa_approve_run, name="holoqa_approve_run", description="Approve a specific run plan.", structured_output=True)
server.add_tool(holoqa_execute_run, name="holoqa_execute_run", description="Execute an approved MVP run plan.", structured_output=True)
server.add_tool(holoqa_get_run_status, name="holoqa_get_run_status", description="Read the current status of a run.", structured_output=True)
server.add_tool(holoqa_export_report, name="holoqa_export_report", description="Generate JSON, HTML, and XLSX report artifacts.", structured_output=True)


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
