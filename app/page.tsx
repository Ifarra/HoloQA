"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Project, type Run } from "@/lib/api";

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([api<{ projects: Project[] }>("/projects"), api<{ runs: Run[] }>("/runs")])
      .then(([p, r]) => { setProjects(p.projects); setRuns(r.runs); })
      .catch((e) => setError(e.message));
  }, []);
  const pass = runs.filter((r) => r.status === "PASS").length;
  const held = runs.filter((r) => r.status === "BLOCKED").length;
  return <main className="main">
    <section className="hero">
      <div className="hero-copy"><div className="eyebrow">MCP-first SIT / UAT operations</div><h1>CONTROL<span>_</span><br />SURFACE</h1><div className="hero-note"><span className="cross">×</span><span>One operating layer for testcase context, agent-led execution, evidence, and release decisions.</span></div></div>
      <div className="hero-panel"><span className="tag">Live workspace</span><h2>Operational clarity.</h2><p>Quality signals aligned to the work teams actually ship.</p><div className="statline"><div className="stat"><strong>{String(projects.length).padStart(2, "0")}</strong><span>Projects</span></div><div className="stat"><strong>{String(runs.length).padStart(2, "0")}</strong><span>Observed runs</span></div><div className="stat"><strong>{pass}/{held}</strong><span>Pass / held</span></div></div></div>
    </section>
    {error && <div className="callout section">{error} — confirm FastAPI is running on port 8000.</div>}
    <section className="section"><div className="section-head"><div><div className="eyebrow">Execution ledger / 01</div><h2>Recent runs</h2></div><Link className="link mono" href="/runs">Inspect all ↗</Link></div>
      <div className="table-wrap"><table><thead><tr><th>Run</th><th>State</th><th>Progress</th><th>Message</th></tr></thead><tbody>{runs.slice(0, 6).map((run, i) => <tr key={run.run_id}><td><span className="index">{String(i + 1).padStart(2, "0")}</span><Link className="link mono" href={`/runs/${run.run_id}`}>{run.run_id}</Link></td><td><span className={`status ${run.status.toLowerCase()}`}><i />{run.status}</span></td><td className="mono">{run.completed_cases || 0}/{run.total_cases || 0}</td><td className="muted">{run.message}</td></tr>)}</tbody></table>{!runs.length && <div className="empty">No runs observed yet. Runs appear after an AI coder starts an approved plan through MCP.</div>}</div>
    </section>
  </main>;
}
