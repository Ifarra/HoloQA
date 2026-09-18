'use client'

import { useEffect, useState } from 'react'
import { api, type Plan } from '@/lib/api'
import { FeaturePage } from '@/components/feature-page'

export default function Plans() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [error, setError] = useState('')
  useEffect(() => { api<{ plans: Plan[] }>('/plans').then(data => setPlans(data.plans)).catch(e => setError(e.message)) }, [])

  return <FeaturePage eyebrow="Control plane / read only" title="AGENT PLANS" intro="Plans appear here after an AI coder has interpreted testcase context. This view records intent and approval state; it cannot launch execution.">
    <section className="section">
      <div className="callout"><strong>Execution stays with the connected agent.</strong><br /><span className="muted">Ask Codex, Claude Code, or another MCP client to inspect testcase context, prepare a plan, request approval, and execute locally through agent-browser. HoloQA monitors the agent session here.</span></div>
    </section>
    <section className="section"><div className="table-wrap"><table><thead><tr><th>Plan</th><th>Environment</th><th>Cases</th><th>State</th><th>Source</th></tr></thead><tbody>
      {plans.map((plan, index) => <tr key={plan.plan_id}><td><span className="index">{String(index + 1).padStart(2, '0')}</span><strong>{plan.name}</strong><div className="mono muted">{plan.plan_id}</div></td><td className="mono">{plan.environment}</td><td className="mono">{plan.case_count}</td><td><span className={`status ${plan.approved ? 'pass' : 'blocked'}`}><i />{plan.approved ? 'APPROVED' : 'AWAITING APPROVAL'}</span></td><td className="mono muted">MCP / AI coder</td></tr>)}
      {!plans.length && <tr><td colSpan={5}><div className="empty">No agent-created plans yet. Store testcase context, then ask your connected AI coder to prepare one.</div></td></tr>}
    </tbody></table></div>{error && <p className="muted">{error}</p>}</section>
  </FeaturePage>
}
