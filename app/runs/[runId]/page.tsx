"use client";

import { use, useEffect, useMemo, useState } from "react";
import { api, type Run } from "@/lib/api";
import { FeaturePage } from "@/components/feature-page";

type EventItem = { event_id: number; event_type: string; test_id?: string; step?: string; status?: string; message?: string; created_at: string; payload?: Record<string, any> };
type Artifact = { evidence_id: string; artifact_path: string; kind: string; test_id?: string };
type TestCase = { test_id?: string; id?: string; title?: string; name?: string; expected?: string; status?: string; actual_result?: string };
type LiveFrame = { data?: string; mime?: string; url?: string; captured_at?: number };

function statusIcon(status: string) {
  const value = status.toUpperCase();
  if (["RUNNING", "AGENT_CONNECTED", "IN_PROGRESS"].includes(value)) return "↻";
  if (value === "PASS") return "✓";
  if (value === "FAIL") return "×";
  if (value === "BLOCKED") return "!";
  return "·";
}

function ArtifactViewer({ item, runId, onClose }: { item: Artifact; runId: string; onClose: () => void }) {
  const [content, setContent] = useState("");
  const binary = item.kind === "screenshot" || item.kind === "trace";
  useEffect(() => {
    if (binary) return;
    fetch(`/api/runs/${runId}/evidence/${item.evidence_id}`).then((r) => r.text()).then(setContent).catch((e) => setContent(`Unable to read artifact: ${e.message}`));
  }, [binary, item.evidence_id, runId]);
  return <div className="artifact-viewer" role="dialog" aria-modal="true">
    <div className="artifact-viewer-head"><div><div className="eyebrow">Artifact / {item.kind}</div><h2>{item.artifact_path.split(/[\\/]/).pop()}</h2></div><div className="actions"><a className="button secondary" href={`/api/runs/${runId}/evidence/${item.evidence_id}`} download>Download</a><button type="button" className="button" onClick={onClose}>Close ×</button></div></div>
    {item.kind === "screenshot" ? <img className="artifact-full-image" src={`/api/runs/${runId}/evidence/${item.evidence_id}`} alt="Captured browser state" /> : item.kind === "trace" ? <div className="trace-view"><div className="artifact-icon">◌</div><h3>Trace package</h3><p className="muted">Download the trace bundle to inspect the browser session locally.</p><a className="button primary" href={`/api/runs/${runId}/evidence/${item.evidence_id}`} download>Download trace ↗</a></div> : <pre className="artifact-text">{content || "Reading captured artifact…"}</pre>}
  </div>;
}

export default function RunDetail({ params }: { params: Promise<{ runId: string }> }) {
  const runId = use(params).runId;
  const [run, setRun] = useState<Run | null>(null);
  const [planCases, setPlanCases] = useState<TestCase[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [tab, setTab] = useState("testcases");
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [selectedTestId, setSelectedTestId] = useState<string | null>(null);
  const [liveFrame, setLiveFrame] = useState<LiveFrame | null>(null);
  const [liveConnected, setLiveConnected] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let source: EventSource | undefined;
    let socket: WebSocket | undefined;
    let alive = true;
    const hydrate = () => api<Run>(`/runs/${runId}`).then((next) => {
      if (!alive) return;
      setRun(next);
      return api<{ cases?: TestCase[] }>(`/plans/${next.plan_id}`).then((plan) => setPlanCases(plan.cases || []));
    }).catch((e) => setError(e.message));
    api<{ events: EventItem[] }>(`/runs/${runId}/events`).then((x) => setEvents(x.events)).then(hydrate).then(() => {
      source = new EventSource(`/api/runs/${runId}/stream`);
      source.onmessage = (e) => { if (!alive) return; const item = JSON.parse(e.data) as EventItem; setEvents((old) => old.some((x) => x.event_id === item.event_id) ? old : [...old, item]); hydrate(); };
      source.addEventListener("complete", (e) => { setRun(JSON.parse((e as MessageEvent).data)); source?.close(); });
    }).catch((e) => setError(e.message));
    const dashboardOrigin = process.env.NEXT_PUBLIC_HOLOQA_API_URL || "http://localhost:8000";
    const liveOrigin = new URL(dashboardOrigin, window.location.origin);
    liveOrigin.protocol = liveOrigin.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${liveOrigin.origin}/api/runs/${runId}/live?role=viewer`);
    socket.onmessage = (message) => { const item = JSON.parse(message.data); if (item.type === "live_state") { setLiveConnected(Boolean(item.connected)); if (item.frame) setLiveFrame(item.frame); } if (item.type === "frame") { setLiveFrame(item); setLiveConnected(true); } if (item.type === "agent_state") setLiveConnected(Boolean(item.run?.agent_connected ?? item.connected)); if (item.type === "agent_event" && item.event) setEvents((old) => [...old, item.event]); };
    socket.onclose = () => setLiveConnected(false);
    return () => { alive = false; source?.close(); socket?.close(); };
  }, [runId]);

  const artifacts = (run?.evidence || []) as Artifact[];
  const results = (run?.results || []) as TestCase[];
  const cases = planCases.length ? planCases : results;
  const caseCards = useMemo(() => cases.map((item, index) => {
    const id = String(item.test_id || item.id || `CASE-${String(index + 1).padStart(3, "0")}`);
    const result = results.find((candidate) => String(candidate.test_id || candidate.id) === id);
    const status = String(result?.status || (run?.current_test_id === id ? "RUNNING" : "PENDING"));
    return { ...item, ...result, test_id: id, status, title: item.title || item.name || result?.title || "Untitled test" };
  }), [cases, results, run?.current_test_id]);
  const selectedCase = caseCards.find((item) => item.test_id === selectedTestId) || caseCards.find((item) => item.test_id === run?.current_test_id) || caseCards[0];
  const selectedArtifacts = artifacts.filter((item) => item.test_id === selectedCase?.test_id);
  const screenshots = artifacts.filter((x) => x.kind === "screenshot");
  const latest = events[events.length - 1];
  const progress = run?.total_cases ? Math.round((run.completed_cases / run.total_cases) * 100) : 0;
  const activeAction = useMemo(() => events.filter((e) => ["step_started", "agent_action", "agent_observation", "recovery_started", "recovery_finished"].includes(e.event_type)).at(-1), [events]);
  const liveSource = liveFrame?.data ? `data:${liveFrame.mime || "image/jpeg"};base64,${liveFrame.data}` : screenshots.at(-1) ? `/api/runs/${runId}/evidence/${screenshots.at(-1)!.evidence_id}` : "";
  async function setControl(mode: string) { try { setRun(await api<Run>(`/runs/${runId}/control`, { method: "POST", body: JSON.stringify({ mode }) })); } catch (e) { setError((e as Error).message); } }

  return <FeaturePage eyebrow={`Live execution / ${runId}`} title="RUN MONITOR" intro="Observe the connected AI coder, inspect agent intent, and open evidence by testcase.">
    {error && <div className="callout section">{error}</div>}
    <section className="section run-hero">
      <div className="run-browser"><div className="browser-chrome"><span className="traffic red" /><span className="traffic yellow" /><span className="traffic green" /><span className="browser-url">LIVE AGENT BROWSER · {run?.current_step || "waiting for agent"}</span><span className="mono">{progress}%</span></div>
        {liveSource ? <img src={liveSource} alt="Live connected agent browser" /> : <div className="browser-placeholder"><div className="eyebrow">Browser viewport</div><h2>{run?.status || "LOADING"}</h2><p className="muted">{liveConnected ? "Connected; waiting for the first frame." : "No live browser stream connected."}</p><div className="fake-cursor">+</div></div>}
        <div className="browser-footer mono"><span>{liveConnected ? "LIVE STREAM CONNECTED" : "LIVE STREAM OFFLINE"}</span><span>{screenshots.length} stored frames · {run?.agent_id || "agent"}</span></div>
      </div>
      <div className="run-side"><div className="eyebrow">Agent intent</div><h3>{run?.current_test_id || "Preparing execution"}</h3><p className="muted">{latest?.message || run?.message || "The connected AI coder will expose its intent as events arrive."}</p><div className="intent-card"><span className={`status ${run?.control_mode === "human" ? "blocked" : "running"}`}><i />{run?.control_mode === "human" ? "HUMAN TAKEOVER" : "CURRENT ACTION"}</span><strong>{run?.control_mode === "human" ? "Browser paused for you" : activeAction?.step || "No action in flight"}</strong><small>{activeAction?.payload?.reason || "Following the approved plan with recovery and evidence capture."}</small></div><div className="run-progress"><div><span>CASE PROGRESS</span><b>{run?.completed_cases || 0}/{run?.total_cases || 0}</b></div><div className="progress-track"><i style={{ width: `${progress}%` }} /></div></div><div className="actions" style={{ marginTop: 18 }}><button type="button" className="button secondary" onClick={() => setControl(run?.control_mode === "human" ? "agent" : "human")}>{run?.control_mode === "human" ? "Return control" : "Take browser control"}</button></div></div>
    </section>
    <section className="section"><div className="run-tabs">{[["testcases", "Test Cases"], ["timeline", "Agent intent"], ["network", "Network"], ["reports", "Reports"]].map(([key, label]) => <button type="button" key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>)}</div><div className="run-detail-panel">
      {tab === "testcases" && <div><div className="section-head"><div><div className="eyebrow">Approved plan / {caseCards.length} cases</div><h2>Test case workspace</h2></div><span className="mono muted">{artifacts.length} evidence items</span></div><div className="testcase-layout"><div className="testcase-grid">{caseCards.map((item) => { const count = artifacts.filter((a) => a.test_id === item.test_id).length; return <button type="button" className={`testcase-card ${selectedCase?.test_id === item.test_id ? "active" : ""}`} key={item.test_id} onClick={() => setSelectedTestId(item.test_id!)}><span className={`testcase-status-icon status-${item.status.toLowerCase()}`}>{statusIcon(item.status)}</span><span className="mono">{item.test_id}</span><strong>{item.title}</strong><small className="muted">{item.status} · {count} artifacts</small></button>; })}{!caseCards.length && <div className="empty">The approved plan has not exposed its testcase workbook yet.</div>}</div><div className="testcase-detail panel">{selectedCase ? <><div className="eyebrow">Selected testcase</div><h3>{selectedCase.test_id}</h3><p><strong>{selectedCase.title}</strong></p><p className="muted">{selectedCase.actual_result || selectedCase.expected || "Waiting for the agent to produce a case observation."}</p><div className="artifact-grid">{selectedArtifacts.map((item) => <button type="button" className="artifact-card" key={item.evidence_id} onClick={() => setSelected(item)}><div className="eyebrow">{item.kind}</div>{item.kind === "screenshot" ? <img src={`/api/runs/${runId}/evidence/${item.evidence_id}`} alt="Testcase evidence" /> : <div className="artifact-icon">{item.kind === "network" ? "⇄" : item.kind === "console" ? "⌘" : "≡"}</div>}<p className="mono">{item.artifact_path.split(/[\\/]/).pop()}</p></button>)}{!selectedArtifacts.length && <div className="empty">Evidence for this testcase will appear here.</div>}</div></> : <div className="empty">Select a testcase card to inspect its evidence.</div>}</div></div></div>}
      {tab === "timeline" && <div className="event-timeline">{events.map((e, i) => <div className="event-row" key={`${e.event_id}-${i}`}><span className={`event-marker ${e.status === "FAIL" ? "bad" : e.status === "PASS" ? "good" : i === events.length - 1 ? "hot" : ""}`} /><div><div className="event-title"><strong>{e.event_type.replaceAll("_", " ")}</strong><span className="mono muted">{new Date(e.created_at).toLocaleTimeString()}</span></div><p>{e.message || e.step || "agent event"}</p>{e.step && <small className="mono">{e.test_id || "run"} · {e.step}</small>}</div></div>)}{!events.length && <div className="empty">Waiting for agent intent events…</div>}</div>}
      {tab === "network" && <div><div className="eyebrow">Captured network evidence</div><div className="artifact-grid" style={{ marginTop: 16 }}>{artifacts.filter((item) => ["network", "har"].includes(item.kind) || /\.har$/i.test(item.artifact_path)).map((item) => <button type="button" className="artifact-card" key={item.evidence_id} onClick={() => setSelected(item)}><div className="artifact-icon">⇄</div><p className="mono">{item.test_id || "run"} · {item.artifact_path.split(/[\\/]/).pop()}</p></button>)}{!artifacts.some((item) => ["network", "har"].includes(item.kind) || /\.har$/i.test(item.artifact_path)) && <div className="empty">No HAR or network capture has been committed by the agent.</div>}</div></div>}
      {tab === "reports" && <div><div className="eyebrow">Server-generated run reports</div><div className="artifact-grid" style={{ marginTop: 16 }}>{artifacts.filter((item) => item.kind.startsWith("report_")).map((item) => <a className="artifact-card" key={item.evidence_id} href={`/api/runs/${runId}/evidence/${item.evidence_id}`} download><div className="artifact-icon">▤</div><p className="mono">{item.kind}</p><span className="link">Download report ↗</span></a>)}{!artifacts.some((item) => item.kind.startsWith("report_")) && <div className="empty">The report is registered when the agent completes the run.</div>}</div></div>}
    </div></section>
    {selected && <ArtifactViewer item={selected} runId={runId} onClose={() => setSelected(null)} />}
  </FeaturePage>;
}
