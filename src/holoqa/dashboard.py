from __future__ import annotations

import html
import json
import os
from pathlib import Path


DEFAULT_STATE_DATABASE = ".holoqa/state.db"


def default_database_path() -> Path:
    return Path(os.environ.get("HOLOQA_STATE", DEFAULT_STATE_DATABASE))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from holoqa.project_store import ProjectStore
from holoqa.runs import RunStore


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_class(status: str) -> str:
    return {"PASS": "pass", "BLOCKED": "blocked", "FAIL": "fail"}.get(status, "neutral")


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
            runs = RunStore(database_path)
            return {**runs.get(run_id).model_dump(), "results": runs.results(run_id), "evidence": runs.evidence(run_id)}
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/runs/{run_id}/evidence/{evidence_id}")
    def evidence_file(run_id: str, evidence_id: str) -> FileResponse:
        matches = [item for item in RunStore(database_path).evidence(run_id) if item["evidence_id"] == evidence_id]
        if not matches:
            raise HTTPException(status_code=404, detail="evidence not found")
        path = Path(matches[0]["artifact_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact file not found")
        return FileResponse(path)

    @app.get("/api/runs")
    def runs() -> dict[str, object]:
        return {"runs": RunStore(database_path).list_runs()}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        projects_data = store.projects()
        runs_data = RunStore(database_path).list_runs()
        project_rows = "".join(
            f'''<tr class="data-row" data-search="{_esc(project["project_name"])} {_esc(project["workspace_root"])}">
                <td><span class="row-index">{index:02d}</span><strong>{_esc(project["project_name"])}</strong></td>
                <td class="mono muted">{_esc(project["workspace_root"])}</td>
                <td class="mono muted">{_esc(project["created_at"])}</td>
                <td><a class="row-link" href="#project-{_esc(project["project_id"])}" aria-label="Open project">↗</a></td>
            </tr>'''
            for index, project in enumerate(projects_data, start=1)
        ) or '<tr class="empty-row"><td colspan="4">No projects initialized yet.</td></tr>'
        run_rows = "".join(
            f'''<tr class="data-row" data-search="{_esc(run["run_id"])} {_esc(run["status"])} {_esc(run["message"])}">
                <td><span class="row-index">{index:02d}</span><strong class="mono">{_esc(run["run_id"])}</strong></td>
                <td><span class="status {_status_class(run["status"])}"><i></i>{_esc(run["status"])}</span></td>
                <td class="muted">{_esc(run["message"])}</td>
                <td><a class="row-link" href="/api/runs/{_esc(run["run_id"])}" aria-label="Inspect run">↗</a></td>
            </tr>'''
            for index, run in enumerate(runs_data, start=1)
        ) or '<tr class="empty-row"><td colspan="4">No runs executed yet.</td></tr>'
        passed = sum(run["status"] == "PASS" for run in runs_data)
        blocked = sum(run["status"] == "BLOCKED" for run in runs_data)
        return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HoloQA Control Surface</title>
<style>
:root {{ --canvas:#e7e7e4; --surface:#f3f3f0; --paper:#fbfbf8; --ink:#161616; --muted:#777773; --line:#c9c9c4; --orange:#ff6327; --green:#2d8b62; --amber:#b67920; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--canvas); font-family:"IBM Plex Sans", "Segoe UI", sans-serif; font-size:13px; background-image:linear-gradient(rgba(0,0,0,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,.035) 1px,transparent 1px); background-size:64px 64px; }}
body:before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.24; background:linear-gradient(90deg,transparent 49.8%,rgba(0,0,0,.06) 50%,transparent 50.2%); }}
.shell {{ max-width:1400px; min-height:100vh; margin:0 auto; padding:18px; }}
.frame {{ min-height:calc(100vh - 36px); border:3px solid var(--ink); border-radius:18px; background:var(--surface); overflow:hidden; box-shadow:0 14px 0 rgba(0,0,0,.07); }}
.topbar {{ min-height:64px; display:flex; align-items:center; gap:32px; padding:0 24px; border-bottom:1px solid var(--ink); }}
.brand {{ display:flex; gap:10px; align-items:center; min-width:205px; font-weight:800; letter-spacing:.08em; font-size:12px; }}
.brand-mark {{ width:22px; height:22px; border:2px solid var(--orange); display:grid; place-items:center; color:var(--orange); font-weight:900; }}
.brand-mark:after {{ content:"+"; font-size:16px; line-height:1; }}
.nav {{ display:flex; gap:24px; align-items:center; flex:1; }}
.nav a, .utility {{ color:var(--muted); text-decoration:none; font-size:11px; text-transform:uppercase; letter-spacing:.1em; }}
.nav a:hover, .row-link:hover {{ color:var(--orange); }}
.nav .back {{ color:var(--ink); }}
.apply {{ border:1px solid var(--orange); color:var(--ink); background:transparent; text-decoration:none; padding:10px 14px; font-size:11px; text-transform:uppercase; letter-spacing:.1em; }}
.apply:hover {{ color:white; background:var(--orange); }}
.main {{ padding:26px 24px 22px; }}
.eyebrow, .label {{ text-transform:uppercase; letter-spacing:.14em; font-size:10px; font-weight:700; color:var(--muted); }}
.hero {{ display:grid; grid-template-columns:1.15fr .85fr; border-bottom:1px solid var(--line); min-height:260px; }}
.hero-copy {{ padding:10px 32px 34px 0; border-right:1px solid var(--line); display:flex; flex-direction:column; justify-content:space-between; }}
h1 {{ margin:20px 0 0; font-size:clamp(58px,9vw,138px); line-height:.82; letter-spacing:-.085em; font-weight:900; max-width:680px; }}
h1 span {{ color:var(--orange); }}
.hero-note {{ display:flex; gap:14px; align-items:flex-end; max-width:310px; line-height:1.45; color:var(--muted); }}
.cross {{ color:var(--orange); font-size:26px; line-height:.8; }}
.hero-panel {{ padding:14px 0 26px 28px; display:flex; flex-direction:column; justify-content:flex-end; }}
.hero-panel .tag {{ color:var(--orange); border:1px solid var(--orange); display:inline-block; width:max-content; padding:5px 8px; text-transform:uppercase; font-size:9px; letter-spacing:.12em; }}
.hero-panel h2 {{ margin:14px 0 8px; font-size:30px; letter-spacing:-.06em; }}
.hero-panel p {{ margin:0; max-width:360px; color:var(--muted); line-height:1.5; }}
.statline {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:24px; border-top:1px solid var(--line); padding-top:14px; }}
.stat strong {{ display:block; font-size:21px; letter-spacing:-.06em; }}
.stat span {{ display:block; margin-top:4px; font-size:9px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }}
.section-head {{ display:flex; align-items:end; justify-content:space-between; gap:20px; padding:27px 0 14px; }}
.section-title {{ margin:0; font-size:23px; letter-spacing:-.05em; }}
.section-title small {{ color:var(--orange); font:700 11px ui-monospace, monospace; vertical-align:top; margin-left:6px; }}
.controls {{ display:flex; align-items:center; gap:10px; }}
.tabs {{ display:flex; border:1px solid var(--line); }}
.tab {{ border:0; border-right:1px solid var(--line); background:transparent; color:var(--muted); padding:9px 12px; font:700 10px inherit; text-transform:uppercase; letter-spacing:.08em; cursor:pointer; }}
.tab:last-child {{ border-right:0; }} .tab.active, .tab:hover {{ background:var(--ink); color:white; }}
.search {{ width:190px; border:1px solid var(--line); background:var(--paper); padding:9px 12px; font:12px inherit; outline:none; }} .search:focus {{ border-color:var(--orange); }}
.table-wrap {{ border-top:2px solid var(--ink); background:var(--paper); overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; min-width:700px; }}
th, td {{ text-align:left; padding:14px 12px; border-bottom:1px solid var(--line); }} th {{ font-size:9px; text-transform:uppercase; letter-spacing:.13em; color:var(--muted); }}
td {{ height:58px; }} td:first-child {{ width:31%; }} .row-index {{ display:inline-block; width:32px; color:var(--orange); font:10px ui-monospace,monospace; }}
.mono {{ font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:11px; }} .muted {{ color:var(--muted); }}
.row-link {{ color:var(--ink); text-decoration:none; font-size:20px; display:inline-block; transition:transform .15s ease; }} .row-link:hover {{ transform:translate(2px,-2px); }}
.status {{ display:inline-flex; align-items:center; gap:7px; font:700 10px ui-monospace,monospace; letter-spacing:.08em; }} .status i {{ width:7px; height:7px; border-radius:50%; background:var(--muted); }} .status.pass i {{ background:var(--green); }} .status.blocked i {{ background:var(--amber); }} .status.fail i {{ background:var(--orange); }}
.empty-row td {{ color:var(--muted); text-align:center; padding:28px; }}
.footer {{ display:grid; grid-template-columns:1fr auto; gap:24px; align-items:center; margin-top:28px; padding:20px 0 4px; border-top:1px solid var(--ink); }}
.footer h2 {{ margin:0 0 5px; font-size:20px; letter-spacing:-.05em; }} .footer p {{ margin:0; color:var(--muted); }} .actions {{ display:flex; gap:10px; }}
.action {{ padding:12px 15px; border:1px solid var(--orange); color:var(--ink); text-decoration:none; font-size:10px; text-transform:uppercase; letter-spacing:.08em; }} .action.primary {{ color:#fff; background:var(--orange); }}
.kicker {{ display:flex; gap:18px; margin-top:8px; }} .kicker span {{ color:var(--muted); font:10px ui-monospace,monospace; }} .kicker b {{ color:var(--ink); }}
@media (max-width:850px) {{ .shell {{ padding:0; }} .frame {{ border-width:0; border-radius:0; box-shadow:none; }} .topbar {{ padding:14px 18px; flex-wrap:wrap; gap:15px; }} .brand {{ min-width:auto; }} .nav {{ order:3; flex-basis:100%; overflow:auto; gap:18px; padding-bottom:2px; }} .main {{ padding:20px 18px; }} .hero {{ grid-template-columns:1fr; }} .hero-copy {{ border-right:0; border-bottom:1px solid var(--line); padding-right:0; min-height:240px; }} .hero-panel {{ padding:22px 0 26px; }} .section-head, .footer {{ align-items:flex-start; flex-direction:column; }} .controls {{ width:100%; flex-wrap:wrap; }} .search {{ flex:1; min-width:180px; }} .actions {{ width:100%; }} .action {{ flex:1; text-align:center; }} }}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
</style></head>
<body><div class="shell"><div class="frame">
<header class="topbar"><div class="brand"><span class="brand-mark"></span><span>HOLOQA / LABS</span></div><nav class="nav"><a class="back" href="#top">← Control surface</a><a href="#projects">Projects</a><a href="#runs">Runs</a><a href="/health">System status</a></nav><a class="apply" href="#runs">New inspection</a></header>
<main class="main" id="top">
<section class="hero"><div class="hero-copy"><div class="eyebrow">MCP-first SIT / UAT operations</div><h1>CONTROL<span>_</span><br>SURFACE</h1><div class="hero-note"><span class="cross">×</span><span>One place to inspect workspaces, approve plans, and preserve evidence from every browser run.</span></div></div><div class="hero-panel"><span class="tag">Live workspace</span><h2>Operational clarity.</h2><p>Structured quality signals for teams shipping agent-built products.</p><div class="statline"><div class="stat"><strong>{len(projects_data):02d}</strong><span>Projects</span></div><div class="stat"><strong>{len(runs_data):02d}</strong><span>Total runs</span></div><div class="stat"><strong>{passed:02d}/{blocked:02d}</strong><span>Pass / held</span></div></div></div></section>
<section id="projects"><div class="section-head"><div><div class="eyebrow">Directory / 01</div><h2 class="section-title">Projects <small>↘</small></h2></div><div class="controls"><div class="tabs" role="tablist"><button class="tab active" data-filter="projects">All</button><button class="tab" data-filter="projects">Initialized</button></div><input class="search" id="dashboard-search" type="search" placeholder="Search surface…" aria-label="Search projects and runs"></div></div><div class="table-wrap"><table><thead><tr><th>Project</th><th>Workspace</th><th>Initialized</th><th></th></tr></thead><tbody id="projects-body" aria-live="polite">{project_rows}</tbody></table></div></section>
<section id="runs"><div class="section-head"><div><div class="eyebrow">Execution ledger / 02</div><h2 class="section-title">Recent runs <small>↘</small></h2></div><div class="kicker"><span>PASS <b>{passed:02d}</b></span><span>HELD <b>{blocked:02d}</b></span></div></div><div class="table-wrap"><table><thead><tr><th>Run identifier</th><th>State</th><th>Message</th><th></th></tr></thead><tbody id="runs-body" aria-live="polite">{run_rows}</tbody></table></div></section>
<footer class="footer"><div><h2>Ready to inspect the next surface?</h2><p>Initialize a workspace or execute an approved plan through the MCP workflow.</p></div><div class="actions"><a class="action" href="#projects">Initialize project</a><a class="action primary" href="#runs">Create run plan ↗</a></div></footer>
</main></div></div>
<script>
const search = document.querySelector('#dashboard-search');
search.addEventListener('input', () => {{
  const term = search.value.toLowerCase().trim();
  document.querySelectorAll('.data-row').forEach(row => {{ row.hidden = term && !row.dataset.search.toLowerCase().includes(term); }});
}});
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {{ document.querySelectorAll('.tab').forEach(item => item.classList.remove('active')); tab.classList.add('active'); }}));
</script></body></html>'''

    return app


app = create_app(default_database_path())


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
