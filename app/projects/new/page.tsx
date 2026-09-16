'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { FeaturePage } from '@/components/feature-page'
import { api } from '@/lib/api'

export default function NewProject() {
  const router = useRouter(); const [path, setPath] = useState(''); const [message, setMessage] = useState('')
  async function submit(event: React.FormEvent) { event.preventDefault(); setMessage('Inspecting workspace and pinning snapshot…'); try { const data = await api<{project_id:string}>('/projects/initialize', {method:'POST', body:JSON.stringify({workspace_root:path})}); setMessage(`${data.project_id} initialized.`); setTimeout(() => router.push(`/projects/${data.project_id}`), 500) } catch (error) { setMessage((error as Error).message) } }
  return <FeaturePage eyebrow="Project setup / 01" title="INITIALIZE" intro="Create a pinned workspace snapshot before authoring or running quality work."><section className="section grid-2"><form className="panel" onSubmit={submit}><div className="field"><label>Workspace path</label><input value={path} onChange={event => setPath(event.target.value)} placeholder="C:\\workspaces\\checkout" required /></div><div className="actions"><button className="button primary">Initialize workspace ↗</button><button type="button" className="button secondary" onClick={() => router.push('/projects')}>Cancel</button></div><p className="muted">{message}</p></form><div className="panel"><div className="eyebrow">MCP boundary</div><h3>Inspection is explicit.</h3><p className="muted">The API runs the same snapshot and CodeGraph workflow used by MCP, then returns the project record for the web operator.</p></div></section></FeaturePage>
}
