"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { FeaturePage } from "@/components/feature-page";

const suggestions = [
  "Show me the latest failures",
  "Create a checkout smoke plan",
  "What is blocking release?",
  "Open the run monitor",
];
export default function Assistant() {
  const [request, setRequest] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setAnswer("");
    try {
      const text = request.toLowerCase();
      if (text.includes("failure") || text.includes("block")) {
        const data = await api<{ findings: any[] }>("/findings");
        const open = data.findings.filter((f) => f.state !== "closed");
        setAnswer(
          `${open.length} open quality signals. The fastest next action is to inspect the finding ledger, where each item links back to its run evidence.`,
        );
      } else if (text.includes("checkout") || text.includes("plan")) {
        setAnswer(
          "I can draft a reviewable plan, but I need a workspace and target environment selected before execution. Open Test Studio to author the cases, then approve the immutable plan.",
        );
      } else if (text.includes("monitor") || text.includes("run")) {
        setAnswer(
          "The Run Center is the live operational surface. It streams worker events over SSE and exposes evidence as each case completes.",
        );
      } else {
        setAnswer(
          "I understood this as a quality-operations request. Try asking about failures, a plan, a run, or release readiness.",
        );
      }
    } catch (e) {
      setAnswer((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <FeaturePage
      eyebrow="MCP workspace / 09"
      title="ASK HOLOQA"
      intro="Inspect quality signals and prepare safe actions in natural language. Execution always remains governed by plan approval."
      actions={
        <Link className="button secondary" href="/projects">
          Back to workspace ↗
        </Link>
      }
    >
      <section className="section grid-2">
        <form className="panel" onSubmit={submit}>
          <div className="eyebrow">Operational query</div>
          <div className="field">
            <label>What do you need to decide?</label>
            <textarea
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              placeholder="Show all checkout failures from the last run…"
            />
          </div>
          <div className="actions">
            <button className="button primary" disabled={busy || !request}>
              {busy ? "Analyzing…" : "Ask HoloQA ↗"}
            </button>
            {suggestions.slice(0, 2).map((s) => (
              <button
                type="button"
                className="button secondary"
                key={s}
                onClick={() => setRequest(s)}
              >
                {s}
              </button>
            ))}
          </div>
          {answer && (
            <div className="callout" style={{ marginTop: 16 }}>
              <div className="eyebrow">Agent intent</div>
              <p>{answer}</p>
              <Link
                className="link mono"
                href={
                  answer.includes("signals")
                    ? "/findings"
                    : answer.includes("Run Center")
                      ? "/runs"
                      : "/projects/demo/tests"
                }
              >
                Continue in workspace ↗
              </Link>
            </div>
          )}
        </form>
        <div className="panel">
          <div className="eyebrow">Safe action model</div>
          <h3>Intent first. Execution second.</h3>
          <p className="muted">
            The assistant is a control layer over the same projects, plans,
            runs, evidence, and findings that the rest of HoloQA uses.
          </p>
          {suggestions.map((s, i) => (
            <button
              className="panel-action"
              key={s}
              onClick={() => setRequest(s)}
            >
              <span>0{i + 1}</span>
              {s}
              <b>↗</b>
            </button>
          ))}
          <div className="callout mono" style={{ marginTop: 16 }}>
            MCP / CONNECTED · APPROVAL REQUIRED
          </div>
        </div>
      </section>
    </FeaturePage>
  );
}
