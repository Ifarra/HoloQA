"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Plan, type Project, type Run } from "@/lib/api";
import { FeaturePage } from "@/components/feature-page";

export default function Runs() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([api<{ runs: Run[] }>("/runs"), api<{ plans: Plan[] }>("/plans"), api<{ projects: Project[] }>("/projects")])
      .then(([r, p, ps]) => { setRuns(r.runs); setPlans(p.plans); setProjects(ps.projects); })
      .catch((e) => setError(e.message));
  }, []);
  const visible = useMemo(() => runs.filter((run) => {
    const plan = plans.find((item) => item.plan_id === run.plan_id);
    const project = projects.find((item) => item.project_id === plan?.project_id);
    return `${run.run_id} ${run.status} ${run.current_test_id || ""} ${run.message} ${project?.project_name || ""}`.toLowerCase().includes(query.toLowerCase());
  }), [runs, plans, projects, query]);
  return <FeaturePage eyebrow="Execution ledger / 04" title="RUN CENTER" intro="Inspect execution records created by connected AI coders. HoloQA monitors client-side agent sessions; it never launches a browser from this page.">
    <section className="section"><div className="section-head"><div><div className="eyebrow">Observed activity</div><h2>Runs</h2></div><label className="search inline-flex items-center gap-2"><Search className="size-4" aria-hidden="true" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search runs or projects..." aria-label="Search runs or projects" /></label></div>
      {error && <div className="callout">{error}</div>}
      <div className="table-wrap"><table><thead><tr><th>Run</th><th>Project</th><th>State</th><th>Progress</th><th>Current work</th><th></th></tr></thead><tbody>
        {visible.map((r, i) => { const plan = plans.find((item) => item.plan_id === r.plan_id); const project = projects.find((item) => item.project_id === plan?.project_id); return <tr key={r.run_id}><td><span className="index">{String(i + 1).padStart(2, "0")}</span><span className="mono">{r.run_id}</span></td><td><strong>{project?.project_name || "Unknown project"}</strong><div className="mono muted">{project?.project_id || "—"}</div></td><td><span className={`status ${r.status.toLowerCase()}`}><i />{r.status}</span></td><td className="mono">{r.completed_cases || 0}/{r.total_cases || 0}</td><td className="muted">{r.current_test_id || r.message}</td><td><Link className="link mono" href={`/runs/${r.run_id}`}>Monitor ↗</Link></td></tr> })}
      </tbody></table>{!visible.length && <div className="empty">{runs.length ? "No runs match this search." : "No runs observed yet."}</div>}</div>
    </section>
  </FeaturePage>;
}
