from pathlib import Path

from holoqa.mcp_server import (
    holoqa_approve_run,
    holoqa_create_run_plan,
    holoqa_execute_run,
    holoqa_get_run_status,
    holoqa_import_test_workbook,
)


def test_mcp_workflow_import_plan_approve_execute(tmp_path: Path):
    source = tmp_path / "cases.xlsx"
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Test ID", "Title", "Steps", "Expected Result"])
    sheet.append(["TC-001", "Create user", "Open form; submit", "User appears"])
    workbook.save(source)
    state = tmp_path / "state.db"

    imported = holoqa_import_test_workbook(str(source), str(state))
    assert imported["status"] == "validated"
    plan = holoqa_create_run_plan("project-demo", imported["cases"], str(state))
    assert plan["status"] == "awaiting_approval"

    blocked = holoqa_execute_run(plan["plan_id"], str(state))
    assert blocked["status"] == "BLOCKED"

    approved = holoqa_approve_run(plan["plan_id"], str(state))
    assert approved["status"] == "approved"
    run = holoqa_execute_run(plan["plan_id"], str(state))
    assert run["status"] == "PASS"
    assert holoqa_get_run_status(run["run_id"], str(state))["status"] == "PASS"
