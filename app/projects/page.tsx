"use client";

import Link from "next/link";
import { ArrowUpRight, Check, CircleAlert, FolderKanban, PlayCircle, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Finding, type Plan, type Project, type Run } from "@/lib/api";

const preview = (value: string) => value.length > 30 ? `${value.slice(0, 30)}…` : value;

export default function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { Promise.all([api<{ projects: Project[] }>("/projects"), api<{ runs: Run[] }>("/runs"), api<{ plans: Plan[] }>("/plans"), api<{ findings: Finding[] }>("/findings")]).then(([p, r, pl, f]) => { setProjects(p.projects); setRuns(r.runs); setPlans(pl.plans); setFindings(f.findings); }).catch((e) => setError(e.message)); }, []);
  const visible = useMemo(() => projects.filter((p) => `${p.project_name} ${p.workspace_root}`.toLowerCase().includes(query.toLowerCase())), [projects, query]);
  const active = runs.find((r) => r.status === "RUNNING" || r.status === "QUEUED");
  const failed = runs.filter((r) => r.status === "FAIL" || r.status === "BLOCKED").length;
  return <main className="dashboard">
    <section className="dashboard-heading"><div><div className="eyebrow">Control plane / workspace index</div><h1>PROJECTS</h1><p className="muted">Workspaces, snapshots, agent activity, and evidence in one place.</p></div></section>
    {error && <div className="callout section">{error}</div>}
    <section className="kpi-grid"><div className="kpi"><span className="kpi-icon"><FolderKanban aria-hidden="true" /></span><div><span className="label">Workspaces</span><strong>{projects.length}</strong><span className="trend up inline-flex items-center gap-1"><ArrowUpRight className="size-3" aria-hidden="true" /> indexed targets</span></div></div><div className="kpi"><span className="kpi-icon"><PlayCircle aria-hidden="true" /></span><div><span className="label">Observed runs</span><strong>{runs.length}</strong><span className="trend inline-flex items-center gap-1">{active ? <><span className="status running"><i /></span> execution active</> : "workers idle"}</span></div></div><div className="kpi"><span className="kpi-icon"><CircleAlert aria-hidden="true" /></span><div><span className="label">Attention</span><strong className="accent-text">{failed + findings.filter((f) => f.state !== "closed").length}</strong><span className="trend alert">failures + open findings</span></div></div><div className="kpi"><span className="kpi-icon"><Check aria-hidden="true" /></span><div><span className="label">Agent plans</span><strong>{plans.length}</strong><span className="trend">{plans.filter((p) => p.approved).length} approved</span></div></div></section>
    <section className="dashboard-grid"><div className="dashboard-panel projects-panel"><div className="panel-heading"><h2>Workspace registry</h2><label className="search"><Search className="size-4" aria-hidden="true" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter projects..." aria-label="Filter projects" /></label></div><div className="project-list"><div className="project-row header"><span>Project</span><span>Snapshot</span><span>Runs</span><span>Quality</span><span /></div>{visible.map((p) => { const pruns = runs.filter((r) => r.plan_id && plans.find((pl) => pl.plan_id === r.plan_id)?.project_id === p.project_id); const open = findings.filter((f) => f.state !== "closed" && pruns.some((r) => r.run_id === f.run_id)).length; return <div className="project-row" key={p.project_id}><span><strong>{p.project_name}</strong><small className="mono muted">{p.workspace_root}</small></span><span className="mono">{p.codegraph_status || "snapshot ready"}</span><span className="mono">{pruns.length} runs</span><span className={`status ${open ? "fail" : "pass"}`}><i />{open ? `${open} attention` : "READY"}</span><Link className="link inline-flex items-center gap-1" href={`/projects/${p.project_id}`}>Open <ArrowUpRight className="size-3" aria-hidden="true" /></Link></div>; })}{!visible.length && <div className="empty">No workspaces match this filter.</div>}</div></div><div className="dashboard-panel activity-panel"><div className="panel-heading"><h2>Agent activity</h2><Link className="link mono inline-flex items-center gap-1" href="/runs">All runs <ArrowUpRight className="size-3" aria-hidden="true" /></Link></div><div className="timeline">{runs.slice(0, 6).map((r, i) => <div className="activity" key={r.run_id}><span className={`timeline-dot ${i === 0 ? "hot" : ""}`} /><div><strong>{r.status === "RUNNING" ? "Agent is executing" : `Run ${r.status.toLowerCase()}`}</strong><small className="mono">{r.run_id} · {preview(r.current_test_id || r.message)}</small></div><time>{r.finished_at ? new Date(r.finished_at).toLocaleDateString() : "now"}</time></div>)}{!runs.length && <div className="empty">Agent activity will appear here.</div>}</div></div></section>
  </main>;
}
