from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from holoqa.config import state_database
from holoqa.project_inspection import inspect_workspace
from holoqa.project_store import ProjectStore
from holoqa.object_storage import artifact_exists, download, prepare_upload, upload_file
from holoqa.reports import export_html, export_json
from holoqa.runs import RunStore
from holoqa.workbook import export_results, import_workbook

server = MCPServer(
    name="holoqa",
    version="0.1.0",
    description="Remote Streamable HTTP MCP tools for HoloQA project initialization and testing.",
)


def holoqa_project_inspect(workspace_root: str) -> dict[str, Any]:
    """Inspect a local workspace without changing it."""
    workspace = Path(workspace_root).expanduser().resolve()
    if os.environ.get("HOLOQA_REMOTE_ONLY") == "1" and not str(workspace).replace("\\", "/").startswith("/workspace"):
        raise ValueError("Remote HoloQA can only inspect workspaces mounted under /workspace on the MCP server")
    inspection = inspect_workspace(workspace)
    missing = [name for name, present in inspection.detected_files.items() if not present]
    return {
        "status": "ok",
        "workspace_root": str(inspection.workspace_root),
        "has_git": inspection.has_git,
        "detected_files": inspection.detected_files,
        "missing_configuration": missing,
    }


def holoqa_initialize_project(workspace_root: str, legacy_state_database: str | None = None) -> dict[str, Any]:
    """Create or update a project and persist a snapshot for the current workspace."""
    workspace = Path(workspace_root).expanduser().resolve()
    initialization = ProjectStore(Path(legacy_state_database) if legacy_state_database else state_database()).initialize(workspace)
    return {
        "status": "completed",
        **initialization.model_dump(),
        "next_actions": ["holoqa_import_test_workbook", "holoqa_get_requirements", "holoqa_create_run_plan"],
    }


def holoqa_import_test_workbook(workbook_path: str, project_id: str | None = None) -> dict[str, Any]:
    """Validate and persist a supported XLSX test workbook for a project."""
    cases = import_workbook(Path(workbook_path))
    result: dict[str, Any] = {"status": "validated", "source": str(Path(workbook_path).resolve()), "cases": cases}
    legacy_database = project_id if project_id and project_id.lower().endswith(".db") else None
    actual_project_id = None if legacy_database else project_id
    if actual_project_id:
        result.update(ProjectStore(state_database()).import_cases(actual_project_id, cases, Path(workbook_path)))
        result["status"] = "imported"
    return result


def holoqa_get_requirements(project_id: str) -> dict[str, Any]:
    """Read testcase and requirement context stored in the HoloQA control plane."""
    store = ProjectStore(state_database())
    return {"status": "ok", "project_id": project_id, "requirements": store.requirements(project_id), "testcases": store.testcases(project_id)}


def holoqa_create_run_plan(project_id: str, cases: list[dict[str, Any]] | None = None, environment: str = "staging", snapshot_id: str | None = None, testcase_set_id: str | None = None) -> dict[str, Any]:
    """Create a reviewable test plan; this operation does not execute tests."""
    store = ProjectStore(state_database())
    selected_cases = cases or store.testcases(project_id)
    if not selected_cases:
        raise ValueError("No persisted testcases found for project; import a workbook first")
    plan = RunStore(state_database()).create_plan(project_id, selected_cases, environment=environment, snapshot_id=snapshot_id, testcase_set_id=testcase_set_id)
    return {"status": "awaiting_approval", **plan.model_dump(), "requires_approval": True}


def holoqa_approve_run(plan_id: str) -> dict[str, Any]:
    """Approve an exact plan before execution."""
    RunStore(state_database()).approve(plan_id)
    return {"status": "approved", "plan_id": plan_id}


def holoqa_execute_run(plan_id: str, base_url: str | None = None) -> dict[str, Any]:
    raise RuntimeError("Server-side browser execution was removed; open an agent session and use agent-browser")




def holoqa_get_run_status(run_id: str, legacy_state_database: str | None = None) -> dict[str, Any]:
    """Read a run status from the local MVP state store."""
    store = RunStore(Path(legacy_state_database) if legacy_state_database else state_database())
    run = store.get(run_id)
    return {**run.model_dump(), "results": store.results(run_id), "evidence": store.evidence(run_id)}


def holoqa_get_evidence(run_id: str) -> dict[str, Any]:
    """Return evidence records for a completed run."""
    return {"run_id": run_id, "evidence": RunStore(state_database()).evidence(run_id)}


def holoqa_start_run(plan_id: str, base_url: str) -> dict[str, Any]:
    raise RuntimeError("Server-side browser workers were removed; use holoqa_open_run_session")


def holoqa_open_run_session(plan_id: str, agent_id: str = "ai-coder") -> dict[str, Any]:
    """Open an agent-owned browser execution session; HoloQA never launches the browser."""
    run, token = RunStore(state_database()).open_agent_session(plan_id, agent_id)
    dashboard = os.environ.get("HOLOQA_PUBLIC_DASHBOARD_URL", "http://localhost:8000").rstrip("/")
    return {**run.model_dump(), "session_token": token, "executor": "connected_ai_coder", "browser_owner": "client", "live_endpoint": f"{dashboard}/api/runs/{run.run_id}/live", "live_connection": "WebSocket"}


def holoqa_agent_heartbeat(run_id: str, session_token: str) -> dict[str, Any]:
    return RunStore(state_database()).heartbeat_agent(run_id, session_token).model_dump()


def holoqa_record_agent_event(run_id: str, session_token: str, sequence: int, event_type: str, test_id: str | None = None, step: str | None = None, status: str | None = None, message: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist an ordered observation/action/recovery event from the local AI coder."""
    return RunStore(state_database()).record_agent_event(run_id, session_token, sequence=sequence, event_type=event_type, test_id=test_id, step=step, status=status, message=message, payload=payload).model_dump()


def holoqa_prepare_artifact_upload(run_id: str, session_token: str, test_id: str, filename: str, content_type: str = "application/octet-stream") -> dict[str, Any]:
    """Return a short-lived Garage URL; artifact bytes stay outside the MCP payload."""
    RunStore(state_database())._authorize_agent(run_id, session_token)
    safe_name = Path(filename).name
    key = f"runs/{run_id}/{test_id}/{safe_name}"
    return {"run_id": run_id, "test_id": test_id, "filename": safe_name, **prepare_upload(key, content_type)}


def holoqa_commit_artifact(run_id: str, session_token: str, test_id: str, artifact_uri: str, kind: str = "artifact") -> dict[str, Any]:
    """Attach an already-uploaded Garage object to a run."""
    if not artifact_exists(artifact_uri):
        raise ValueError("artifact was not found in Garage; upload it before committing")
    return RunStore(state_database()).record_agent_artifact(run_id, session_token, test_id, artifact_uri, kind).model_dump()


def holoqa_update_case_verdict(run_id: str, session_token: str, test_id: str, status: str, message: str, result: dict[str, Any] | None = None, artifacts: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Record a case verdict supplied by the AI coder after local browser execution."""
    store = RunStore(state_database())
    for artifact in artifacts or []:
        uri = str(artifact.get("artifact_uri") or artifact.get("path") or "")
        if not uri or not artifact_exists(uri):
            raise ValueError(f"artifact was not found in Garage: {uri}")
        store.record_agent_artifact(run_id, session_token, test_id, uri, str(artifact.get("kind") or "artifact"))
    merged = dict(result or {})
    if artifacts:
        merged["evidence_items"] = [{"path": str(item.get("artifact_uri") or item.get("path")), "kind": str(item.get("kind") or "artifact")} for item in artifacts]
    return store.record_agent_verdict(run_id, session_token, test_id=test_id, status=status, message=message, result=merged).model_dump()


def holoqa_complete_run(run_id: str, session_token: str, status: str, message: str) -> dict[str, Any]:
    """Close an agent session after every testcase has a final verdict."""
    store = RunStore(state_database())
    store._authorize_agent(run_id, session_token)
    results = store.results(run_id)
    if status == "PASS" and (not results or any(item.get("status") != "PASS" for item in results)):
        raise ValueError("a run can be PASS only when every recorded testcase passed")
    store.complete(run_id, status, message, results, execution_mode="agent")
    report = holoqa_export_report(run_id)
    return {**store.get(run_id).model_dump(), "results": results, "evidence": store.evidence(run_id), "report": report}


def holoqa_get_run_events(run_id: str, after_id: int = 0) -> dict[str, Any]:
    """Read durable execution events after an event cursor."""
    return {"run_id": run_id, "events": RunStore(state_database()).events(run_id, after_id=after_id)}


def holoqa_cancel_run(run_id: str) -> dict[str, Any]:
    """Request cancellation of a queued or running browser job."""
    store = RunStore(state_database())
    store.request_cancel(run_id)
    return store.get(run_id).model_dump()


def _remote_project_inspect(workspace_root: str) -> dict[str, Any]:
    return holoqa_project_inspect(workspace_root)


def _remote_initialize_project(workspace_root: str) -> dict[str, Any]:
    return holoqa_initialize_project(workspace_root)


def _remote_import_test_workbook(workbook_path: str, project_id: str) -> dict[str, Any]:
    return holoqa_import_test_workbook(workbook_path, project_id)


def _remote_get_run_status(run_id: str) -> dict[str, Any]:
    return holoqa_get_run_status(run_id)


def _remote_export_report(run_id: str, source_workbook: str | None = None, output_directory: str | None = None) -> dict[str, Any]:
    return holoqa_export_report(run_id, None, source_workbook, output_directory)


def holoqa_export_report(
    run_id: str,
    legacy_state_database: str | None = None,
    source_workbook: str | None = None,
    output_directory: str | None = None,
) -> dict[str, Any]:
    """Generate MVP JSON, HTML, and optional annotated XLSX report artifacts."""
    database = Path(legacy_state_database) if legacy_state_database else state_database()
    store = RunStore(database)
    run = store.get(run_id).model_dump()
    output = Path(output_directory) if output_directory else database.parent / "artifacts" / run_id
    results = store.results(run_id)
    if not results:
        results = [{"test_id": "MVP", "status": run["status"], "actual_result": run["message"], "evidence": ""}]
    evidence = store.evidence(run_id)
    json_path = export_json(output / "report.json", run, results, evidence)
    html_path = export_html(output / "report.html", run, results)
    artifacts = {"json": upload_file(json_path, f"runs/{run_id}/report.json"), "html": upload_file(html_path, f"runs/{run_id}/report.html")}
    if source_workbook:
        workbook_path = output / "results.xlsx"
        export_results(Path(source_workbook), workbook_path, results)
        artifacts["xlsx"] = upload_file(workbook_path, f"runs/{run_id}/results.xlsx")
    for kind, artifact_uri in artifacts.items():
        store.add_evidence(run_id, "__run__", artifact_uri, f"report_{kind}")
    return {"status": "completed", "run_id": run_id, "artifacts": artifacts}


server.add_tool(_remote_project_inspect, name="holoqa_project_inspect", description="Inspect a workspace already mounted on the HoloQA server. Do not pass a local Windows path.", structured_output=True)
server.add_tool(_remote_initialize_project, name="holoqa_initialize_project", description="Initialize a server-side workspace project and pinned snapshot. Uses the HoloQA server state store.", structured_output=True)
server.add_tool(_remote_import_test_workbook, name="holoqa_import_test_workbook", description="Validate and persist a supported XLSX test workbook for an initialized project.", structured_output=True)
server.add_tool(holoqa_get_requirements, name="holoqa_get_requirements", description="Retrieve testcase and requirement context from the control plane.", structured_output=True)
server.add_tool(holoqa_create_run_plan, name="holoqa_create_run_plan", description="Create a reviewable run plan from persisted project testcases. Does not execute.", structured_output=True)
server.add_tool(holoqa_approve_run, name="holoqa_approve_run", description="Approve a specific run plan.", structured_output=True)
# Browser execution is intentionally not exposed: the connected AI coder owns the client browser.
server.add_tool(_remote_get_run_status, name="holoqa_get_run_status", description="Read the current status of a run with results and evidence from the shared server store.", structured_output=True)
server.add_tool(holoqa_get_evidence, name="holoqa_get_evidence", description="List evidence artifacts for a run.", structured_output=True)
server.add_tool(holoqa_open_run_session, name="holoqa_open_run_session", description="Open an agent-owned browser session. HoloQA does not launch a browser.", structured_output=True)
server.add_tool(holoqa_agent_heartbeat, name="holoqa_agent_heartbeat", description="Keep a connected AI coder session alive.", structured_output=True)
server.add_tool(holoqa_record_agent_event, name="holoqa_record_agent_event", description="Record ordered browser observations, actions, and recovery events.", structured_output=True)
server.add_tool(holoqa_prepare_artifact_upload, name="holoqa_prepare_artifact_upload", description="Prepare a short-lived Garage upload URL for client-side evidence.", structured_output=True)
server.add_tool(holoqa_commit_artifact, name="holoqa_commit_artifact", description="Attach a verified Garage artifact to a testcase.", structured_output=True)
server.add_tool(holoqa_update_case_verdict, name="holoqa_update_case_verdict", description="Record a testcase verdict from the AI coder.", structured_output=True)
server.add_tool(holoqa_complete_run, name="holoqa_complete_run", description="Complete a run after client-side AI execution.", structured_output=True)
server.add_tool(holoqa_get_run_events, name="holoqa_get_run_events", description="Read live execution events for a run.", structured_output=True)
server.add_tool(holoqa_cancel_run, name="holoqa_cancel_run", description="Request cancellation of a running job.", structured_output=True)
server.add_tool(_remote_export_report, name="holoqa_export_report", description="Generate JSON, HTML, and XLSX report artifacts.", structured_output=True)


def main() -> None:
    transport = os.environ.get("HOLOQA_MCP_TRANSPORT", "streamable-http")
    if transport != "streamable-http":
        raise RuntimeError("HoloQA supports remote Streamable HTTP only; configure the MCP URL instead of starting a local process")
    server.run(
        transport="streamable-http",
        host=os.environ.get("HOLOQA_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("HOLOQA_MCP_PORT", "8100")),
        streamable_http_path=os.environ.get("HOLOQA_MCP_PATH", "/mcp"),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
