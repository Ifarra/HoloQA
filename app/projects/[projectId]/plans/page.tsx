'use client'
import { useEffect, useState } from 'react'
import { api, type Plan } from '@/lib/api'
import { FeaturePage } from '@/components/feature-page'

export default function Plans() {
  const [plans, setPlans] = useState<Plan[]>([])
  useEffect(() => { api<{plans:Plan[]}>('/plans').then(data => setPlans(data.plans)) }, [])
  async function approve(planId:string) { await api(`/plans/${planId}/approve`, {method:'POST'}); setPlans(old => old.map(plan => plan.plan_id === planId ? {...plan, approved:true} : plan)) }
  async function start(planId:string) { const baseUrl = window.prompt('Target base URL', 'http://localhost:3000'); if (!baseUrl) return; const run = await api<{run_id:string}>(`/plans/${planId}/start`, {method:'POST', body:JSON.stringify({base_url:baseUrl})}); window.location.href = `/runs/${run.run_id}` }
  return <FeaturePage eyebrow="Governance / 02" title="PLAN APPROVAL" intro="Review scope, approve an immutable plan version, and start execution against a named environment."><section className="section"><div className="table-wrap"><table><thead><tr><th>Plan</th><th>Environment</th><th>Cases</th><th>State</th><th>Actions</th></tr></thead><tbody>{plans.map((plan,index)=><tr key={plan.plan_id}><td><span className="index">{String(index+1).padStart(2,'0')}</span><strong>{plan.name}</strong><div className="mono muted">{plan.plan_id}</div></td><td className="mono">{plan.environment}</td><td className="mono">{plan.case_count}</td><td><span className={`status ${plan.approved?'pass':'blocked'}`}><i/>{plan.approved?'APPROVED':'DRAFT'}</span></td><td><div className="actions">{!plan.approved&&<button className="button secondary" onClick={()=>approve(plan.plan_id)}>Approve</button>}{plan.approved&&<button className="button primary" onClick={()=>start(plan.plan_id)}>Start run ↗</button>}</div></td></tr>)}</tbody></table>{!plans.length&&<div className="empty">No plans drafted yet.</div>}</div></section></FeaturePage>
}
