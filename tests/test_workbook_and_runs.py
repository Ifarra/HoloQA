from pathlib import Path

from holoqa.workbook import import_workbook, export_results
from holoqa.runs import RunStore


def test_workbook_import_and_export(tmp_path: Path):
    source = tmp_path / "cases.xlsx"
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Test ID", "Title", "Steps", "Expected Result"])
    sheet.append(["TC-001", "Create user", "Open form; submit valid user", "User appears"])
    workbook.save(source)

    spec = import_workbook(source)
    assert spec[0]["test_id"] == "TC-001"
    assert spec[0]["title"] == "Create user"

    output = tmp_path / "results.xlsx"
    export_results(source, output, [{"test_id": "TC-001", "status": "PASS", "actual_result": "User appears"}])
    assert output.exists()
    assert source.read_bytes() != output.read_bytes()


def test_run_store_requires_approval(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    plan = store.create_plan("project-1", [{"test_id": "TC-001"}])
    run = store.execute(plan.plan_id)
    assert run.status == "BLOCKED"
    assert "approval" in run.message.lower()

    store.approve(plan.plan_id)
    run = store.execute(plan.plan_id)
    assert run.status == "AGENT_READY"
    assert "ai coder" in run.message.lower()
