'use client'

import Link from 'next/link'
import { ArrowUpRight, CheckCircle2, Clock3, FileText, FolderGit2, Globe2, PlayCircle, ShieldCheck, TerminalSquare } from 'lucide-react'
import { use, useEffect, useMemo, useState } from 'react'
import { api, type Environment, type Plan, type Project, type Requirement, type Run } from '@/lib/api'
import { FeaturePage } from '@/components/feature-page'

const short = (value: string, length = 34) => value.length > length ? `${value.slice(0, length)}…` : value

export default function ProjectHome({ params }: { params: Promise<{ projectId: string }> }) {
  const projectId = use(params).projectId
  const [project, setProject] = useState<Project | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [requirements, setRequirements] = useState<Requirement[]>([])
  const [environments, setEnvironments] = useState<Environment[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      api<{ projects: Project[] }>('/projects'),
      api<{ plans: Plan[] }>('/plans'),
      api<{ runs: Run[] }>('/runs'),
      api<{ requirements: Requirement[] }>(`/requirements?project_id=${projectId}`),
      api<{ environments: Environment[] }>(`/environments?project_id=${projectId}`),
    ]).then(([projects, allPlans, allRuns, reqs, envs]) => {
      setProject(projects.projects.find(x => x.project_id === projectId) || null)
      const projectPlans = allPlans.plans.filter(x => x.project_id === projectId)
      setPlans(projectPlans)
      setRuns(allRuns.runs.filter(x => projectPlans.some(p => p.plan_id === x.plan_id)))
      setRequirements(reqs.requirements)
      setEnvironments(envs.environments)
    }).catch(e => setError(e.message))
  }, [projectId])

  const latest = runs[0]
  const passed = runs.filter(r => r.status === 'PASS').length
  const active = runs.find(r => r.status === 'RUNNING' || r.status === 'QUEUED')
  const pendingPlan = plans.find(p => !p.approved)
  const readiness = useMemo(() => {
    if (active) return { label: 'RUN IN PROGRESS', tone: 'running', detail: 'The connected AI coder is operating its local browser session.' }
    if (!requirements.length) return { label: 'ADD TESTCASE CONTEXT', tone: 'blocked', detail: 'Store the business flow before asking an AI coder to prepare a plan.' }
    if (pendingPlan) return { label: 'AGENT PLAN AWAITING APPROVAL', tone: 'blocked', detail: 'Review the plan in your AI coder conversation before execution.' }
    if (!runs.length) return { label: 'READY FOR AGENT', tone: 'pass', detail: 'Testcase context is available to your connected MCP client.' }
    return { label: 'MONITORING ACTIVE', tone: 'pass', detail: 'The project has an evidence-backed execution history.' }
  }, [active, pendingPlan, requirements.length, runs.length])

  return <FeaturePage eyebrow="Project utility / control plane" title={project?.project_name || 'PROJECT'} intro={project?.workspace_root || 'Workspace status, testcase context, agent plans, and observed execution.'} actions={<div className="actions"><Link className="button secondary" href="/projects">All projects ↗</Link><Link className="button primary" href={`/projects/${projectId}/tests`}>Create testcase ↗</Link></div>}>
    {error && <div className="callout section">{error}</div>}

    <section className="section">
      <div className="panel" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 24, alignItems: 'center' }}>
        <div><div className="eyebrow">Workspace status</div><h2 style={{ margin: '8px 0 6px' }}>{short(project?.workspace_root || 'Loading workspace…', 58)}</h2><div className="mono muted">{projectId}</div></div>
        <div style={{ textAlign: 'right' }}><span className={`status ${readiness.tone}`}><i />{readiness.label}</span><p className="muted" style={{ maxWidth: 330, margin: '8px 0 0' }}>{readiness.detail}</p></div>
      </div>
    </section>

    <section className="section grid-3">
      <div className="kpi"><span className="kpi-icon"><FileText aria-hidden="true" /></span><div><span className="label">Testcase context</span><strong>{requirements.length}</strong><span className="trend">stored documents</span></div></div>
      <div className="kpi"><span className="kpi-icon"><PlayCircle aria-hidden="true" /></span><div><span className="label">Observed runs</span><strong>{runs.length}</strong><span className="trend">{passed} passed</span></div></div>
      <div className="kpi"><span className="kpi-icon"><Globe2 aria-hidden="true" /></span><div><span className="label">Environments</span><strong>{environments.length}</strong><span className="trend">available targets</span></div></div>
    </section>

    <section className="section grid-2">
      <div className="panel"><div className="section-head" style={{ paddingTop: 0 }}><div><div className="eyebrow">Input surface</div><h3>Testcase context</h3></div><Link className="link mono" href={`/projects/${projectId}/tests`}>Open library ↗</Link></div><p className="muted">Business flows saved here are retrieved by Codex, Claude Code, or another MCP client.</p><div className="panel-links">{requirements.slice(0, 3).map(item => <div key={item.requirement_id} className="inline-flex items-center gap-2"><FileText className="size-4 text-primary" aria-hidden="true" /><span>{short(item.title)}</span></div>)}{!requirements.length && <span className="muted">No testcase context has been stored yet.</span>}</div></div>
      <div className="panel"><div className="section-head" style={{ paddingTop: 0 }}><div><div className="eyebrow">Agent activity</div><h3>Plan ledger</h3></div><Link className="link mono" href={`/projects/${projectId}/plans`}>Inspect plans ↗</Link></div><p className="muted">Plans are created and approved through the MCP workflow. This page only reports their state.</p><div className="panel-links">{plans.slice(0, 3).map(plan => <div key={plan.plan_id} className="inline-flex items-center gap-2"><ShieldCheck className="size-4 text-primary" aria-hidden="true" /><span>{short(plan.name)} <span className="mono muted">· {plan.approved ? 'approved' : 'awaiting approval'}</span></span></div>)}{!plans.length && <span className="muted">No agent plan has arrived yet.</span>}</div></div>
    </section>

    <section className="section grid-2">
      <div className="panel"><div className="section-head" style={{ paddingTop: 0 }}><div><div className="eyebrow">Target context</div><h3>Environments</h3></div><Link className="link mono" href={`/projects/${projectId}/environments`}>Manage targets ↗</Link></div>{environments.length ? <div className="panel-links">{environments.slice(0, 3).map(env => <div key={env.environment_id} className="inline-flex items-center gap-2"><Globe2 className="size-4 text-primary" aria-hidden="true" /><span><strong>{env.name}</strong><span className="mono muted"> · {env.base_url}</span></span></div>)}</div> : <div className="callout">No execution target registered yet. Add one so the agent has explicit environment context.</div>}</div>
      <div className="panel"><div className="section-head" style={{ paddingTop: 0 }}><div><div className="eyebrow">Handoff</div><h3>Operate through MCP</h3></div><Link className="link mono" href="/create-test">Workflow guide ↗</Link></div><div className="timeline" style={{ marginTop: 8 }}><div className="activity"><span className="timeline-dot hot" /><div><strong>Connect your AI coder</strong><small className="mono">Codex · Claude Code · MCP client</small></div></div><div className="activity"><span className="timeline-dot" /><div><strong>Retrieve and prepare</strong><small className="mono">context → plan → approval</small></div></div><div className="activity"><span className="timeline-dot" /><div><strong>Observe the worker</strong><small className="mono">run → evidence → report</small></div></div></div></div>
    </section>

    <section className="section"><div className="section-head" style={{ paddingTop: 0 }}><div><div className="eyebrow">Execution ledger</div><h2>Recent runs</h2></div><Link className="link mono" href="/runs">Open run center ↗</Link></div><div className="table-wrap"><table><thead><tr><th>Run</th><th>State</th><th>Progress</th><th>Current work</th><th /></tr></thead><tbody>{runs.slice(0, 5).map((run, index) => <tr key={run.run_id}><td><span className="index">{String(index + 1).padStart(2, '0')}</span><span className="mono">{run.run_id}</span></td><td><span className={`status ${run.status.toLowerCase()}`}><i />{run.status}</span></td><td className="mono">{run.completed_cases || 0}/{run.total_cases || 0}</td><td className="muted">{short(run.current_test_id || run.message)}</td><td><Link className="link mono" href={`/runs/${run.run_id}`}>Monitor ↗</Link></td></tr>)}{!runs.length && <tr><td colSpan={5}><div className="empty">No runs observed for this project yet.</div></td></tr>}</tbody></table></div></section>
  </FeaturePage>
}
