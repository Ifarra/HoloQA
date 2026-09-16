import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from openpyxl import Workbook


def _make_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Test ID", "Title", "Steps", "Expected Result"])
    sheet.append(["MCP-001", "Create user", "Open /demo; Create user mcp@example.com", "User created successfully"])
    workbook.save(path)


def test_stdio_mcp_client_completes_approved_workflow(tmp_path: Path):
    asyncio.run(_run_stdio_workflow(tmp_path))


async def _run_stdio_workflow(tmp_path: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment["HOLOQA_DEMO_PORT"] = str(port)
    demo = subprocess.Popen([sys.executable, "-m", "holoqa.demo_app"], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("demo app did not start")

        workbook = tmp_path / "mcp_cases.xlsx"
        _make_workbook(workbook)
        state = tmp_path / "state.db"
        server = StdioServerParameters(command="uv", args=["run", "holoqa-mcp"], cwd=str(Path.cwd()))
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"holoqa_project_inspect", "holoqa_execute_run", "holoqa_export_report"} <= names

                inspected = await session.call_tool("holoqa_project_inspect", {"workspace_root": str(tmp_path)})
                assert inspected.structured_content["status"] == "ok"
                initialized = await session.call_tool("holoqa_initialize_project", {"workspace_root": str(tmp_path), "state_database": str(state)})
                assert initialized.structured_content["status"] == "completed"
                imported = await session.call_tool("holoqa_import_test_workbook", {"workbook_path": str(workbook), "state_database": str(state)})
                plan = await session.call_tool("holoqa_create_run_plan", {"project_id": initialized.structured_content["project_id"], "cases": imported.structured_content["cases"], "state_database": str(state)})
                blocked = await session.call_tool("holoqa_execute_run", {"plan_id": plan.structured_content["plan_id"], "state_database": str(state), "base_url": f"http://127.0.0.1:{port}"})
                assert blocked.structured_content["status"] == "BLOCKED"
                await session.call_tool("holoqa_approve_run", {"plan_id": plan.structured_content["plan_id"], "state_database": str(state)})
                executed = await session.call_tool("holoqa_execute_run", {"plan_id": plan.structured_content["plan_id"], "state_database": str(state), "base_url": f"http://127.0.0.1:{port}"})
                assert executed.structured_content["status"] == "PASS", executed.structured_content
                assert executed.structured_content["evidence"]
                assert {item["kind"] for item in executed.structured_content["evidence"]} >= {"screenshot", "network", "console", "dom", "navigation"}
                report = await session.call_tool("holoqa_export_report", {"run_id": executed.structured_content["run_id"], "state_database": str(state), "source_workbook": str(workbook)})
                assert set(report.structured_content["artifacts"]) == {"json", "html", "xlsx"}
    finally:
        demo.terminate()
        demo.wait(timeout=10)
