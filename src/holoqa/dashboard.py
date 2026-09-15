from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from holoqa.project_store import ProjectStore
from holoqa.runs import RunStore


def create_app(database_path: Path) -> FastAPI:
    store = ProjectStore(database_path)
    app = FastAPI(title="HoloQA Dashboard", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/projects")
    def projects() -> dict[str, object]:
        return {"projects": store.projects()}

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, object]:
        try:
            return RunStore(database_path).get(run_id).model_dump()
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/runs")
    def runs() -> dict[str, object]:
        return {"runs": RunStore(database_path).list_runs()}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        project_rows = "".join(
            f"<tr><td>{project['project_name']}</td><td>{project['workspace_root']}</td>"
            f"<td>{project['created_at']}</td></tr>"
            for project in store.projects()
        )
        if not project_rows:
            project_rows = '<tr><td colspan="3">No projects initialized yet.</td></tr>'
        run_rows = "".join(
            f"<tr><td>{run['run_id']}</td><td>{run['status']}</td><td>{run['message']}</td></tr>"
            for run in RunStore(database_path).list_runs()
        )
        if not run_rows:
            run_rows = '<tr><td colspan="3">No runs executed yet.</td></tr>'
        return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HoloQA Dashboard</title>
<style>
body {{ margin:0; font-family:system-ui,sans-serif; background:#0f172a; color:#e2e8f0; }}
main {{ max-width:1100px; margin:0 auto; padding:40px 24px; }}
header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:32px; }}
h1 {{ margin:0; font-size:32px; }}
.badge {{ color:#67e8f9; border:1px solid #155e75; padding:6px 10px; border-radius:999px; font-size:13px; }}
.card {{ background:#1e293b; border:1px solid #334155; border-radius:12px; padding:24px; }}
table {{ width:100%; border-collapse:collapse; margin-top:16px; }}
th,td {{ text-align:left; padding:14px 10px; border-bottom:1px solid #334155; }}
th {{ color:#94a3b8; font-size:13px; text-transform:uppercase; }}
.empty {{ color:#94a3b8; }}
</style></head>
<body><main>
<header><div><h1>HoloQA</h1><p>Agentic SIT/UAT control center</p></div><span class="badge">MVP</span></header>
<section class="card"><h2>Projects</h2><p class="empty">Projects initialized through the MCP server appear here.</p>
<table><thead><tr><th>Name</th><th>Workspace</th><th>Created</th></tr></thead><tbody>{project_rows}</tbody></table></section>
<section class="card" style="margin-top:24px"><h2>Recent Runs</h2>
<table><thead><tr><th>Run</th><th>Status</th><th>Message</th></tr></thead><tbody>{run_rows}</tbody></table></section>
</main></body></html>"""

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(Path(".holoqa/state.db")), host="127.0.0.1", port=8000)
