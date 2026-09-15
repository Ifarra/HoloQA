from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook


_REQUIRED = {"test id", "title", "steps", "expected result"}


def import_workbook(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise ValueError("workbook is empty")
    headers = [str(value or "").strip().lower() for value in rows[0]]
    missing = _REQUIRED - set(headers)
    if missing:
        raise ValueError(f"workbook missing columns: {sorted(missing)}")
    indexes = {header: headers.index(header) for header in _REQUIRED}
    cases = []
    for row in rows[1:]:
        if not any(value is not None and str(value).strip() for value in row):
            continue
        cases.append(
            {
                "test_id": str(row[indexes["test id"]] or "").strip(),
                "title": str(row[indexes["title"]] or "").strip(),
                "steps": [step.strip() for step in str(row[indexes["steps"]] or "").split(";") if step.strip()],
                "expected_result": str(row[indexes["expected result"]] or "").strip(),
            }
        )
    return cases


def export_results(source: Path, output: Path, results: list[dict[str, Any]]) -> None:
    workbook = load_workbook(source)
    sheet = workbook.active
    headers = [str(value or "").strip().lower() for value in next(sheet.iter_rows(values_only=True))]
    additions = ["Status", "Actual Result", "Evidence"]
    for header in additions:
        if header.lower() not in headers:
            sheet.cell(row=1, column=sheet.max_column + 1, value=header)
            headers.append(header.lower())
    by_id = {str(result["test_id"]): result for result in results}
    for row in range(2, sheet.max_row + 1):
        test_id = str(sheet.cell(row=row, column=headers.index("test id") + 1).value or "")
        result = by_id.get(test_id)
        if not result:
            continue
        for header, key in (("status", "status"), ("actual result", "actual_result"), ("evidence", "evidence")):
            sheet.cell(row=row, column=headers.index(header) + 1, value=result.get(key, ""))
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
