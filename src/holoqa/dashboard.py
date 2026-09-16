from __future__ import annotations

import html
import json
import os
import time
from pathlib import Path


DEFAULT_STATE_DATABASE = ".holoqa/state.db"


def default_database_path() -> Path:
    return Path(os.environ.get("HOLOQA_STATE", DEFAULT_STATE_DATABASE))

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from holoqa.execution_service import start_background_run
from holoqa.project_store import ProjectStore
from holoqa.runs import RunStore


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_class(status: str) -> str:
    return {"PASS": "pass", "BLOCKED": "blocked", "FAIL": "fail", "INCONCLUSIVE": "neutral"}.get(status, "neutral")


def create_app(database_path: Path) -> FastAPI:
    store = ProjectStore(database_path)
    app = FastAPI(title="HoloQA Dashboard", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/projects")
    def projects() -> dict[str, object]:
        return {"projects": store.projects()}

    @app.get("/api/plans")
    def plans() -> dict[str, object]:
        return {"plans": RunStore(database_path).list_plans()}

    @app.post("/api/plans")
    def create_plan(payload: dict[str, object] = Body(default_factory=dict)) -> dict[str, object]:
        project_id = str(payload.get("project_id") or "")
        cases = payload.get("cases")
        if not project_id or not isinstance(cases, list) or not cases:
            raise HTTPException(status_code=400, detail="project_id and at least one test case are required")
        name = str(payload.get("name") or "Untitled plan")
        environment = str(payload.get("environment") or "local")
        plan = RunStore(database_path).create_plan(project_id, cases, environment=environment, name=name)
        return plan.model_dump()

    @app.get("/api/plans/{plan_id}")
    def plan_status(plan_id: str) -> dict[str, object]:
        try:
            return RunStore(database_path).get_plan(plan_id).model_dump()
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/plans/{plan_id}/approve")
    def approve_plan(plan_id: str) -> dict[str, object]:
        try:
            runs = RunStore(database_path)
            runs.approve(plan_id)
            return runs.get_plan(plan_id).model_dump()
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/plans/{plan_id}/start")
    def start_plan(plan_id: str, payload: dict[str, object] = Body(default_factory=dict)) -> dict[str, object]:
        base_url = str(payload.get("base_url") or "http://localhost:3000")
        try:
            return start_background_run(plan_id, str(database_path), base_url)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

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
        try:
            path = path.resolve()
            artifact_root = database_path.expanduser().resolve().parent
            path.relative_to(artifact_root)
        except ValueError:
            raise HTTPException(status_code=403, detail="evidence outside artifact root") from None
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact file not found")
        return FileResponse(path)

    @app.get("/api/runs")
    def runs() -> dict[str, object]:
        return {"runs": RunStore(database_path).list_runs()}

    @app.get("/api/findings")
    def findings() -> dict[str, object]:
        run_store = RunStore(database_path)
        items = []
        for run in run_store.list_runs():
            for result in run_store.results(run["run_id"]):
                if result.get("status") != "PASS":
                    items.append({"finding_id": f"finding_{run['run_id']}_{result.get('test_id', 'unknown')}", "run_id": run["run_id"], "test_id": result.get("test_id"), "status": result.get("status"), "title": result.get("title") or result.get("test_id"), "expected": result.get("expected_result", ""), "actual": result.get("actual_result", ""), "evidence": result.get("evidence_items", [])})
        return {"findings": items}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, after_id: int = 0) -> dict[str, object]:
        try:
            return {"run_id": run_id, "events": RunStore(database_path).events(run_id, after_id=after_id)}
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/runs/{run_id}/stream")
    def run_stream(run_id: str) -> StreamingResponse:
        def stream():
            cursor = 0
            while True:
                store = RunStore(database_path)
                try:
                    run = store.get(run_id)
                except ValueError:
                    yield f"event: error\ndata: {json.dumps({'detail': 'run not found'})}\n\n"
                    return
                events = store.events(run_id, after_id=cursor)
                for event in events:
                    cursor = event["event_id"]
                    yield f"data: {json.dumps(event)}\n\n"
                if run.status in {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE", "CANCELLED"}:
                    yield f"event: complete\ndata: {json.dumps(run.model_dump())}\n\n"
                    return
                time.sleep(0.75)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, object]:
        try:
            store = RunStore(database_path)
            store.request_cancel(run_id)
            return store.get(run_id).model_dump()
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/runs/{run_id}/retry")
    def retry(run_id: str, payload: dict[str, object] = Body(default_factory=dict)) -> dict[str, object]:
        base_url = str(payload.get("base_url") or "http://localhost:3000")
        try:
            run = RunStore(database_path).get(run_id)
            return start_background_run(run.plan_id, str(database_path), base_url)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        projects_data = store.projects()
        run_store = RunStore(database_path)
        runs_data = run_store.list_runs()
        plans_data = run_store.list_plans()
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
                <td><a class="row-link run-link" href="#monitor" data-run-id="{_esc(run["run_id"])}" aria-label="Monitor run">↗</a></td>
            </tr>'''
            for index, run in enumerate(runs_data, start=1)
        ) or '<tr class="empty-row"><td colspan="4">No runs executed yet.</td></tr>'
        plan_rows = "".join(
            f'''<tr class="data-row" data-search="{_esc(plan["name"])} {_esc(plan["plan_id"])} {_esc(plan["environment"])}">
                <td><span class="row-index">{index:02d}</span><strong>{_esc(plan["name"])}</strong><div class="mono muted">{_esc(plan["plan_id"])}</div></td>
                <td class="mono">{_esc(plan["environment"])}</td><td class="mono">{plan["case_count"]:02d} cases</td>
                <td><span class="status {_status_class("PASS" if plan["approved"] else "BLOCKED")}"><i></i>{"APPROVED" if plan["approved"] else "DRAFT"}</span></td>
                <td><button class="row-action plan-approve" data-plan-id="{_esc(plan["plan_id"])}" {"disabled" if plan["approved"] else ""}>{"Approved" if plan["approved"] else "Approve"}</button><button class="row-action plan-start" data-plan-id="{_esc(plan["plan_id"])}">Start ↗</button></td>
            </tr>'''
            for index, plan in enumerate(plans_data, start=1)
        ) or '<tr class="empty-row"><td colspan="5">No plans drafted yet. Use Test Studio to create the first plan.</td></tr>'
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
.studio-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }} .form-panel {{ background:var(--paper); border-top:2px solid var(--ink); padding:18px; }} .form-row {{ display:grid; gap:6px; margin-bottom:14px; }} .form-row label {{ text-transform:uppercase; letter-spacing:.12em; font-size:9px; font-weight:700; color:var(--muted); }} .form-row input, .form-row select, .form-row textarea {{ width:100%; border:1px solid var(--line); background:var(--surface); color:var(--ink); padding:10px 11px; font:12px inherit; outline:none; }} .form-row textarea {{ min-height:190px; resize:vertical; font-family:ui-monospace,monospace; font-size:11px; }} .form-row input:focus, .form-row select:focus, .form-row textarea:focus {{ border-color:var(--orange); }} .form-message {{ min-height:18px; margin-top:10px; color:var(--muted); }} .row-action {{ border:1px solid var(--line); background:var(--surface); color:var(--ink); padding:8px 10px; margin-right:6px; font:700 9px inherit; text-transform:uppercase; letter-spacing:.07em; cursor:pointer; }} .row-action:hover {{ border-color:var(--orange); }} .row-action:disabled {{ opacity:.5; cursor:not-allowed; }}
.kicker {{ display:flex; gap:18px; margin-top:8px; }} .kicker span {{ color:var(--muted); font:10px ui-monospace,monospace; }} .kicker b {{ color:var(--ink); }}
@media (max-width:850px) {{ .shell {{ padding:0; }} .frame {{ border-width:0; border-radius:0; box-shadow:none; }} .topbar {{ padding:14px 18px; flex-wrap:wrap; gap:15px; }} .brand {{ min-width:auto; }} .nav {{ order:3; flex-basis:100%; overflow:auto; gap:18px; padding-bottom:2px; }} .main {{ padding:20px 18px; }} .hero {{ grid-template-columns:1fr; }} .hero-copy {{ border-right:0; border-bottom:1px solid var(--line); padding-right:0; min-height:240px; }} .hero-panel {{ padding:22px 0 26px; }} .section-head, .footer {{ align-items:flex-start; flex-direction:column; }} .controls {{ width:100%; flex-wrap:wrap; }} .search {{ flex:1; min-width:180px; }} .actions {{ width:100%; }} .action {{ flex:1; text-align:center; }} .studio-grid {{ grid-template-columns:1fr; }} }}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
</style></head>
<body><div class="shell"><div class="frame">
<header class="topbar"><div class="brand"><span class="brand-mark"></span><span>HOLOQA / LABS</span></div><nav class="nav"><a class="back" href="#top">← Control surface</a><a href="#projects">Projects</a><a href="#runs">Runs</a><a href="/health">System status</a></nav><a class="apply" href="#runs">New inspection</a></header>
<main class="main" id="top">
<section id="studio"><div class="section-head"><div><div class="eyebrow">Authoring / 02</div><h2 class="section-title">Test studio <small>↘</small></h2></div><div class="kicker"><span>CASES <b>STRUCTURED</b></span><span>MODE <b>MCP-READY</b></span></div></div><div class="studio-grid"><form class="form-panel" id="plan-form"><div class="form-row"><label for="plan-name">Plan name</label><input id="plan-name" value="Smoke inspection" required></div><div class="form-row"><label for="plan-project">Project</label><select id="plan-project" required>{''.join(f'<option value="{_esc(project["project_id"])}">{_esc(project["project_name"])}</option>' for project in projects_data) or '<option value="">Initialize a project first</option>'}</select></div><div class="form-row"><label for="plan-environment">Environment</label><input id="plan-environment" value="local"></div><div class="form-row"><label for="plan-cases">Test cases / JSON</label><textarea id="plan-cases" spellcheck="false">{'[]'}</textarea></div><button class="action primary" type="submit">Draft plan ↗</button><div class="form-message" id="plan-message" aria-live="polite"></div></form><div class="form-panel"><div class="eyebrow">Authoring contract</div><h3 style="font-size:24px;letter-spacing:-.05em;margin:12px 0 8px">From intent to evidence.</h3><p class="muted" style="line-height:1.55;max-width:420px">Write browser actions and explicit assertions. Every approved plan becomes a versioned execution record with screenshots, DOM, network, and console evidence.</p><div class="kicker" style="margin-top:28px"><span>SUPPORTED <b>OPEN / · TEXT · URL · STATUS</b></span></div></div></div></section>
<section id="plans"><div class="section-head"><div><div class="eyebrow">Governance / 03</div><h2 class="section-title">Plan approval <small>↘</small></h2></div><div class="kicker"><span>DRAFTS <b>{len(plans_data):02d}</b></span></div></div><div class="table-wrap"><table><thead><tr><th>Plan</th><th>Environment</th><th>Scope</th><th>State</th><th>Action</th></tr></thead><tbody id="plans-body" aria-live="polite">{plan_rows}</tbody></table></div></section>
<section class="hero"><div class="hero-copy"><div class="eyebrow">MCP-first SIT / UAT operations</div><h1>CONTROL<span>_</span><br>SURFACE</h1><div class="hero-note"><span class="cross">×</span><span>One place to inspect workspaces, approve plans, and preserve evidence from every browser run.</span></div></div><div class="hero-panel"><span class="tag">Live workspace</span><h2>Operational clarity.</h2><p>Structured quality signals for teams shipping agent-built products.</p><div class="statline"><div class="stat"><strong>{len(projects_data):02d}</strong><span>Projects</span></div><div class="stat"><strong>{len(runs_data):02d}</strong><span>Total runs</span></div><div class="stat"><strong>{passed:02d}/{blocked:02d}</strong><span>Pass / held</span></div></div></div></section>
<section id="projects"><div class="section-head"><div><div class="eyebrow">Directory / 01</div><h2 class="section-title">Projects <small>↘</small></h2></div><div class="controls"><div class="tabs" role="tablist"><button class="tab active" data-filter="projects">All</button><button class="tab" data-filter="projects">Initialized</button></div><input class="search" id="dashboard-search" type="search" placeholder="Search surface…" aria-label="Search projects and runs"></div></div><div class="table-wrap"><table><thead><tr><th>Project</th><th>Workspace</th><th>Initialized</th><th></th></tr></thead><tbody id="projects-body" aria-live="polite">{project_rows}</tbody></table></div></section>
<section id="runs"><div class="section-head"><div><div class="eyebrow">Execution ledger / 02</div><h2 class="section-title">Recent runs <small>↘</small></h2></div><div class="kicker"><span>PASS <b>{passed:02d}</b></span><span>HELD <b>{blocked:02d}</b></span></div></div><div class="table-wrap"><table><thead><tr><th>Run identifier</th><th>State</th><th>Message</th><th></th></tr></thead><tbody id="runs-body" aria-live="polite">{run_rows}</tbody></table></div></section>
<section id="monitor"><div class="section-head"><div><div class="eyebrow">Live execution / 03</div><h2 class="section-title">Run monitor <small>↘</small></h2></div><button class="action" id="cancel-run" type="button">Cancel run</button></div><div class="table-wrap" style="padding:18px"><div id="monitor-status" class="mono" aria-live="polite">Select a run to monitor progress and evidence.</div><pre id="monitor-events" class="mono muted" style="white-space:pre-wrap;max-height:360px;overflow:auto"></pre></div></section>
<footer class="footer"><div><h2>Ready to inspect the next surface?</h2><p>Initialize a workspace or execute an approved plan through the MCP workflow.</p></div><div class="actions"><a class="action" href="#projects">Initialize project</a><a class="action primary" href="#runs">Create run plan ↗</a></div></footer>
</main></div></div>
<script>
const planForm = document.querySelector('#plan-form');
const planMessage = document.querySelector('#plan-message');
if (planForm) planForm.addEventListener('submit', async (event) => {{
  event.preventDefault();
  planMessage.textContent = 'Validating cases and drafting plan…';
  try {{
    const cases = JSON.parse(document.querySelector('#plan-cases').value);
    const response = await fetch('/api/plans', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{name:document.querySelector('#plan-name').value, project_id:document.querySelector('#plan-project').value, environment:document.querySelector('#plan-environment').value, cases}})}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not create plan');
    planMessage.textContent = `${{data.plan_id}} drafted. Review and approve it below.`;
    window.location.hash = 'plans';
    setTimeout(() => window.location.reload(), 500);
  }} catch (error) {{ planMessage.textContent = error.message; }}
}});
document.querySelectorAll('.plan-approve').forEach(button => button.addEventListener('click', async () => {{
  const response = await fetch(`/api/plans/${{button.dataset.planId}}/approve`, {{method:'POST'}});
  if (response.ok) window.location.reload();
}}));
document.querySelectorAll('.plan-start').forEach(button => button.addEventListener('click', async () => {{
  const baseUrl = window.prompt('Target base URL', 'http://localhost:3000');
  if (!baseUrl) return;
  const response = await fetch(`/api/plans/${{button.dataset.planId}}/start`, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{base_url:baseUrl}})}});
  const run = await response.json();
  if (!response.ok) {{ window.alert(run.detail || 'Could not start run'); return; }}
  window.location.hash = 'monitor';
  monitorRun(run.run_id);
}}));
const search = document.querySelector('#dashboard-search');
search.addEventListener('input', () => {{
  const term = search.value.toLowerCase().trim();
  document.querySelectorAll('.data-row').forEach(row => {{ row.hidden = term && !row.dataset.search.toLowerCase().includes(term); }});
}});
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {{ document.querySelectorAll('.tab').forEach(item => item.classList.remove('active')); tab.classList.add('active'); }}));
let activeRunId = null;
let activeSource = null;
const monitorStatus = document.querySelector('#monitor-status');
const monitorEvents = document.querySelector('#monitor-events');
const monitorResults = document.createElement('div');
monitorResults.className = 'form-message';
monitorEvents.parentElement.appendChild(monitorResults);
const cancelButton = document.querySelector('#cancel-run');
function renderRun(run) {{
  const progress = run.total_cases ? ` ${{run.completed_cases || 0}}/${{run.total_cases}} cases` : '';
  monitorStatus.textContent = `${{run.run_id}} — ${{run.status}}${{progress}} — ${{run.message || ''}}`;
  if (run.results) monitorResults.innerHTML = run.results.map(result => `<div style="padding:8px 0;border-top:1px solid var(--line)"><span class="status ${{result.status === 'PASS' ? 'pass' : result.status === 'BLOCKED' ? 'blocked' : 'fail'}}"><i></i>${{result.status}}</span> <span class="mono">${{result.test_id || ''}}</span> <span class="muted">${{result.actual_result || ''}}</span></div>`).join('');
}}
async function monitorRun(runId) {{
  activeRunId = runId;
  if (activeSource) activeSource.close();
  const run = await fetch(`/api/runs/${{runId}}`).then(response => response.json());
  renderRun(run);
  monitorEvents.textContent = '';
  const events = await fetch(`/api/runs/${{runId}}/events`).then(response => response.json());
  events.events.forEach(event => {{ monitorEvents.textContent += `[${{event.created_at}}] ${{event.event_type}} ${{event.test_id || ''}} ${{event.step || ''}} ${{event.message || ''}}\\n`; }});
  activeSource = new EventSource(`/api/runs/${{runId}}/stream`);
  activeSource.onmessage = event => {{ const item = JSON.parse(event.data); monitorEvents.textContent += `[${{item.created_at}}] ${{item.event_type}} ${{item.test_id || ''}} ${{item.step || ''}} ${{item.message || ''}}\\n`; fetch(`/api/runs/${{runId}}`).then(response => response.json()).then(renderRun); }};
  activeSource.addEventListener('complete', event => {{ renderRun(JSON.parse(event.data)); activeSource.close(); }});
}}
document.querySelectorAll('.run-link').forEach(link => link.addEventListener('click', () => monitorRun(link.dataset.runId)));
cancelButton.addEventListener('click', async () => {{ if (activeRunId) {{ const response = await fetch(`/api/runs/${{activeRunId}}/cancel`, {{method:'POST'}}); renderRun(await response.json()); }} }});
setInterval(async () => {{ const response = await fetch('/api/runs'); const data = await response.json(); if (activeRunId) {{ const current = data.runs.find(item => item.run_id === activeRunId); if (current) renderRun(current); }} }}, 2000);
</script></body></html>'''

    return app


app = create_app(default_database_path())


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
