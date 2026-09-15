from __future__ import annotations

import json
from pathlib import Path

from holoqa.workbook import export_results


def export_json(path: Path, run: dict, results: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run": run, "results": results}, indent=2), encoding="utf-8")
    return path


def export_html(path: Path, run: dict, results: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f"<tr><td>{r.get('test_id','')}</td><td>{r.get('status','')}</td><td>{r.get('actual_result','')}</td></tr>"
        for r in results
    )
    path.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'><title>HoloQA Report</title></head>"
        f"<body><h1>HoloQA Test Report</h1><p>Run: {run.get('run_id','')}</p>"
        f"<table border='1'><tr><th>Test ID</th><th>Status</th><th>Actual Result</th></tr>{rows}</table></body></html>",
        encoding="utf-8",
    )
    return path
