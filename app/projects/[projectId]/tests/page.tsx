'use client'

import Link from 'next/link'
import { use, useEffect, useState } from 'react'
import { FeaturePage } from '@/components/feature-page'
import { api, type Requirement } from '@/lib/api'

export default function TestcaseLibrary({ params }: { params: Promise<{ projectId: string }> }) {
  const projectId = use(params).projectId
  const [items, setItems] = useState<Requirement[]>([])
  const [title, setTitle] = useState('')
  const [document, setDocument] = useState('')
  const [message, setMessage] = useState('')

  const load = () => api<{ requirements: Requirement[] }>(`/requirements?project_id=${projectId}`).then(x => setItems(x.requirements))
  useEffect(() => { load().catch(e => setMessage(e.message)) }, [projectId])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    try {
      await api('/requirements', {
        method: 'POST',
        body: JSON.stringify({ project_id: projectId, title, description: document, priority: 'medium' }),
      })
      setTitle('')
      setDocument('')
      setMessage('Testcase document saved. Your AI coder can retrieve it through HoloQA.')
      load()
    } catch (error) {
      setMessage((error as Error).message)
    }
  }

  return <FeaturePage
    eyebrow={`Control plane / ${projectId}`}
    title="TESTCASE LIBRARY"
    intro="Store the business flow here. Codex, Claude Code, or another connected agent retrieves it, maps it to the repository, and decides when a run is appropriate."
    actions={<Link className="button secondary" href={`/projects/${projectId}`}>Project overview ↗</Link>}
  >
    <section className="section grid-2">
      <form className="panel" onSubmit={save}>
        <div className="eyebrow">User input / durable context</div>
        <h3>Register a testcase document</h3>
        <p className="muted">Paste a scenario, acceptance criteria, or a compact test specification. Saving this does not create a plan or start a run.</p>
        <div className="field"><label>Document title</label><input required value={title} onChange={e => setTitle(e.target.value)} placeholder="Checkout — guest purchase" /></div>
        <div className="field"><label>Testcase document</label><textarea required value={document} onChange={e => setDocument(e.target.value)} placeholder={'Goal\nPreconditions\nSteps\nExpected result'} spellCheck={false} /></div>
        <button className="button primary">Save testcase context ↗</button>
        <p className="muted">{message}</p>
      </form>
      <div className="panel">
        <div className="eyebrow">Agent handoff</div>
        <h3>HoloQA observes. Your AI coder operates.</h3>
        <p className="muted">The platform keeps the source intent available through the API/MCP surface. The connected agent can inspect it, normalize it, create a reviewable plan, request approval, and trigger execution from its own workflow.</p>
        <div className="callout mono">NO PLAN CREATED · NO RUN STARTED</div>
        <div className="panel-links" style={{ marginTop: 18 }}>
          <Link href={`/projects/${projectId}/plans`}>View agent plans <span>read only ↗</span></Link>
        </div>
      </div>
    </section>
    <section className="section">
      <div className="section-head"><div><div className="eyebrow">Stored context</div><h2>Available to connected agents</h2></div><span className="mono muted">{items.length} document{items.length === 1 ? '' : 's'}</span></div>
      <div className="grid-3">
        {items.map((item, index) => <article className="panel" key={item.requirement_id}>
          <div className="eyebrow"><span className="index">{String(index + 1).padStart(2, '0')}</span> {item.key}</div>
          <h3>{item.title}</h3>
          <p className="muted" style={{ whiteSpace: 'pre-wrap' }}>{item.description}</p>
          <span className={`status ${item.status === 'covered' ? 'pass' : 'blocked'}`}><i />{item.status === 'covered' ? 'COVERED' : 'AWAITING AGENT MAPPING'}</span>
        </article>)}
        {!items.length && <div className="empty">No testcase documents yet. Add the first business flow above.</div>}
      </div>
    </section>
  </FeaturePage>
}
