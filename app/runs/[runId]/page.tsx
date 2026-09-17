"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { api, type Run } from "@/lib/api";
import { FeaturePage } from "@/components/feature-page";

type EventItem = {
  event_id: number;
  event_type: string;
  test_id?: string;
  step?: string;
  status?: string;
  message?: string;
  created_at: string;
  payload?: Record<string, any>;
};
type Artifact = {
  evidence_id: string;
  artifact_path: string;
  kind: string;
  test_id?: string;
};

function ArtifactViewer({
  item,
  runId,
  onClose,
}: {
  item: Artifact;
  runId: string;
  onClose: () => void;
}) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(
    item.kind !== "screenshot" && item.kind !== "trace",
  );
  useEffect(() => {
    if (!loading) return;
    fetch(`/api/runs/${runId}/evidence/${item.evidence_id}`)
      .then((r) => r.text())
      .then(setContent)
      .catch((e) => setContent(`Unable to read artifact: ${e.message}`))
      .finally(() => setLoading(false));
  }, [item, runId, loading]);
  return (
    <div className="artifact-viewer" role="dialog" aria-modal="true">
      <div className="artifact-viewer-head">
        <div>
          <div className="eyebrow">
            In-platform artifact viewer / {item.kind}
          </div>
          <h2>{item.artifact_path.split(/[\\/]/).pop()}</h2>
        </div>
        <div className="actions">
          <a
            className="button secondary"
            href={`/api/runs/${runId}/evidence/${item.evidence_id}`}
            download
          >
            Download
          </a>
          <button type="button" className="button" onClick={onClose}>
            Close ×
          </button>
        </div>
      </div>
      {item.kind === "screenshot" ? (
        <img
          className="artifact-full-image"
          src={`/api/runs/${runId}/evidence/${item.evidence_id}`}
          alt="Captured browser state"
        />
      ) : item.kind === "trace" ? (
        <div className="trace-view">
          <div className="artifact-icon">◌</div>
          <h3>Playwright trace package</h3>
          <p className="muted">
            This replay package contains browser snapshots, network records,
            screenshots, and source context for the run.
          </p>
          <a
            className="button primary"
            href={`/api/runs/${runId}/evidence/${item.evidence_id}`}
            download
          >
            Download trace package ↗
          </a>
        </div>
      ) : (
        <pre className="artifact-text">
          {loading ? "Reading captured artifact…" : content}
        </pre>
      )}
    </div>
  );
}

export default function RunDetail({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const runId = use(params).runId;
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [tab, setTab] = useState("timeline");
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let source: EventSource | undefined;
    let alive = true;
    const hydrate = () =>
      api<Run>(`/runs/${runId}`)
        .then(setRun)
        .catch((e) => setError(e.message));
    api<{ events: EventItem[] }>(`/runs/${runId}/events`)
      .then((x) => setEvents(x.events))
      .then(hydrate)
      .then(() => {
        source = new EventSource(`/api/runs/${runId}/stream`);
        source.onmessage = (e) => {
          if (!alive) return;
          const item = JSON.parse(e.data) as EventItem;
          setEvents((old) =>
            old.some((x) => x.event_id === item.event_id)
              ? old
              : [...old, item],
          );
          hydrate();
        };
        source.addEventListener("complete", (e) => {
          setRun(JSON.parse((e as MessageEvent).data));
          source?.close();
        });
      })
      .catch((e) => setError(e.message));
    return () => {
      alive = false;
      source?.close();
    };
  }, [runId]);
  const artifacts = (run?.evidence || []) as Artifact[];
  const screenshots = artifacts.filter((x) => x.kind === "screenshot");
  const latest = events[events.length - 1];
  const progress = run?.total_cases
    ? Math.round((run.completed_cases / run.total_cases) * 100)
    : 0;
  const activeAction = useMemo(
    () => events.filter((e) => e.event_type === "step_started").at(-1),
    [events],
  );
  async function setControl(mode: string) {
    try {
      setRun(
        await api<Run>(`/runs/${runId}/control`, {
          method: "POST",
          body: JSON.stringify({ mode }),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <FeaturePage
      eyebrow={`Live execution / ${runId}`}
      title="RUN MONITOR"
      intro="Watch the agent move, inspect why it acted, take control when needed, and view every captured artifact in place."
      actions={
        <div className="actions">
          <Link className="button secondary" href="/runs">
            Run center ↗
          </Link>
          <button
            type="button"
            className="button secondary"
            onClick={() => setTab("evidence")}
          >
            View artifacts ({artifacts.length}) ↗
          </button>
          {run?.status && ["RUNNING", "QUEUED"].includes(run.status) && (
            <>
              <button
                type="button"
                className="button primary"
                onClick={() =>
                  setControl(run.control_mode === "human" ? "agent" : "human")
                }
              >
                {run.control_mode === "human"
                  ? "Return control to agent"
                  : "Take browser control"}
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={() =>
                  api(`/runs/${runId}/cancel`, { method: "POST" }).then(() =>
                    location.reload(),
                  )
                }
              >
                Cancel run
              </button>
            </>
          )}
        </div>
      }
    >
      {error && <div className="callout section">{error}</div>}
      <section className="section run-hero">
        <div className="run-browser">
          <div className="browser-chrome">
            <span className="traffic red" />
            <span className="traffic yellow" />
            <span className="traffic green" />
            <span className="browser-url">
              LIVE BROWSER · {run?.current_step || "waiting for worker"}
            </span>
            <span className="mono">{progress}%</span>
          </div>
          {screenshots.at(-1) ? (
            <img
              src={`/api/runs/${runId}/evidence/${screenshots.at(-1)!.evidence_id}`}
              alt="Latest captured browser state"
            />
          ) : (
            <div className="browser-placeholder">
              <div className="eyebrow">Browser viewport</div>
              <h2>{run?.status || "LOADING"}</h2>
              <p className="muted">
                {run?.current_step ||
                  "Waiting for the worker to capture the first state."}
              </p>
              <div className="fake-cursor">+</div>
            </div>
          )}
          <div className="browser-footer mono">
            <span>
              {run?.control_mode === "human"
                ? "HUMAN CONTROL ACTIVE"
                : "AGENT CONTROL ACTIVE"}
            </span>
            <span>{screenshots.length} frames captured</span>
          </div>
        </div>
        <div className="run-side">
          <div className="eyebrow">Agent intent</div>
          <h3>{run?.current_test_id || "Preparing execution"}</h3>
          <p className="muted">
            {latest?.message ||
              run?.message ||
              "The worker will expose its operational intent as events arrive."}
          </p>
          <div className="intent-card">
            <span
              className={`status ${run?.control_mode === "human" ? "blocked" : "running"}`}
            >
              <i />
              {run?.control_mode === "human"
                ? "HUMAN TAKEOVER"
                : "CURRENT ACTION"}
            </span>
            <strong>
              {run?.control_mode === "human"
                ? "Browser paused for you"
                : activeAction?.step || "No action in flight"}
            </strong>
            <small>
              {activeAction?.payload?.reason ||
                "Following the approved plan and preserving evidence."}
            </small>
          </div>
          <div className="run-progress">
            <div>
              <span>CASE PROGRESS</span>
              <b>
                {run?.completed_cases || 0}/{run?.total_cases || 0}
              </b>
            </div>
            <div className="progress-track">
              <i style={{ width: `${progress}%` }} />
            </div>
          </div>
        </div>
      </section>
      <section className="section">
        <div className="run-tabs">
          {[
            ["timeline", "Replay timeline"],
            ["evidence", "All artifacts"],
            ["network", "Network"],
            ["results", "Results"],
          ].map(([key, label]) => (
            <button
              type="button"
              key={key}
              className={tab === key ? "active" : ""}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="run-detail-panel">
          {tab === "timeline" && (
            <div className="event-timeline">
              {events.map((e, i) => {
                const path = e.payload?.evidence_items?.[0]?.path;
                const artifact = artifacts.find(
                  (a) => a.artifact_path === path,
                );
                return (
                  <div className="event-row" key={`${e.event_id}-${i}`}>
                    <span
                      className={`event-marker ${e.status === "FAIL" ? "bad" : e.status === "PASS" ? "good" : i === events.length - 1 ? "hot" : ""}`}
                    />
                    <div>
                      <div className="event-title">
                        <strong>{e.event_type.replaceAll("_", " ")}</strong>
                        <span className="mono muted">
                          {new Date(e.created_at).toLocaleTimeString()}
                        </span>
                      </div>
                      <p>{e.message || e.step || "worker event"}</p>
                      {e.step && (
                        <small className="mono">
                          {e.test_id || "run"} · {e.step}
                        </small>
                      )}
                      {artifact && (
                        <button
                          type="button"
                          className="link mono artifact-inline-link"
                          onClick={() => setSelected(artifact)}
                        >
                          View captured frame ↗
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
              {!events.length && (
                <div className="empty">Waiting for worker events…</div>
              )}
            </div>
          )}
          {tab === "evidence" && (
            <div className="artifact-grid">
              {artifacts.map((item) => (
                <button
                  type="button"
                  className="panel artifact-card"
                  key={item.evidence_id}
                  onClick={() => setSelected(item)}
                >
                  <div className="eyebrow">
                    {item.kind} · {item.test_id || "run"}
                  </div>
                  {item.kind === "screenshot" ? (
                    <img
                      src={`/api/runs/${runId}/evidence/${item.evidence_id}`}
                      alt="Captured browser state"
                    />
                  ) : (
                    <div className="artifact-icon">
                      {item.kind === "network"
                        ? "⇄"
                        : item.kind === "console"
                          ? "⌘"
                          : item.kind === "trace"
                            ? "◌"
                            : "≡"}
                    </div>
                  )}
                  <p className="mono">
                    {item.artifact_path.split(/[\\/]/).pop()}
                  </p>
                  <span className="link">View in platform ↗</span>
                </button>
              ))}
              {!artifacts.length && (
                <div className="empty">
                  Artifacts appear here as cases execute.
                </div>
              )}
            </div>
          )}
          {tab === "network" && (
            <pre className="code-block">
              {JSON.stringify(
                (run?.results || []).flatMap((r: any) => r.network || []),
                null,
                2,
              ) || "No network records yet."}
            </pre>
          )}
          {tab === "results" && (
            <div className="result-stack">
              {(run?.results || []).map((r: any) => (
                <div className="result-card" key={r.test_id}>
                  <span className={`status ${String(r.status).toLowerCase()}`}>
                    <i />
                    {r.status}
                  </span>
                  <strong>{r.test_id}</strong>
                  <p className="muted">{r.actual_result}</p>
                  {r.status !== "PASS" && (
                    <Link
                      className="link mono"
                      href={`/findings/finding_${runId}_${r.test_id}`}
                    >
                      Open finding context ↗
                    </Link>
                  )}
                </div>
              ))}
              {!run?.results?.length && (
                <div className="empty">
                  Results will populate after a case completes.
                </div>
              )}
            </div>
          )}
        </div>
      </section>
      {selected && (
        <ArtifactViewer
          item={selected}
          runId={runId}
          onClose={() => setSelected(null)}
        />
      )}
    </FeaturePage>
  );
}
