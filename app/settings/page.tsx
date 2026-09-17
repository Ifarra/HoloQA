"use client";
import { useEffect, useState } from "react";
import { FeaturePage } from "@/components/feature-page";
export default function Settings() {
  const [saved, setSaved] = useState(false);
  const [form, setForm] = useState({
    workspace: "HoloQA / Labs",
    retention: "30",
    notifications: true,
    autoFindings: true,
  });
  useEffect(() => {
    const raw = localStorage.getItem("holoqa.settings");
    if (raw)
      try {
        setForm(JSON.parse(raw));
      } catch {}
  }, []);
  function save(e: React.FormEvent) {
    e.preventDefault();
    localStorage.setItem("holoqa.settings", JSON.stringify(form));
    setSaved(true);
    setTimeout(() => setSaved(false), 1800);
  }
  return (
    <FeaturePage
      eyebrow="Administration / 10"
      title="SETTINGS"
      intro="Control the local quality workspace, notification policy, and evidence retention defaults."
    >
      <section className="section grid-2">
        <form className="panel" onSubmit={save}>
          <div className="eyebrow">Workspace policy</div>
          <div className="field">
            <label htmlFor="workspace-name">Workspace name</label>
            <input
              id="workspace-name"
              value={form.workspace}
              onChange={(e) => setForm({ ...form, workspace: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="retention">Evidence retention / days</label>
            <input
              id="retention"
              type="number"
              min="1"
              value={form.retention}
              onChange={(e) => setForm({ ...form, retention: e.target.value })}
            />
          </div>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={form.notifications}
              onChange={(e) =>
                setForm({ ...form, notifications: e.target.checked })
              }
            />
            <span>
              <strong>Important run notifications</strong>
              <small>Failures, blockers, and intervention requests only.</small>
            </span>
          </label>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={form.autoFindings}
              onChange={(e) =>
                setForm({ ...form, autoFindings: e.target.checked })
              }
            />
            <span>
              <strong>Finding candidates</strong>
              <small>Suggest findings from failed and blocked cases.</small>
            </span>
          </label>
          <button type="submit" className="button primary">
            {saved ? "Saved ✓" : "Save policy ↗"}
          </button>
        </form>
        <div className="panel">
          <div className="eyebrow">System posture</div>
          <h3>Local-first, evidence-forward</h3>
          <p className="muted">
            HoloQA keeps plans, run events, artifacts, and findings in the local
            state database. Environment secrets should be injected by deployment
            configuration, never embedded in a plan.
          </p>
          <div className="callout mono">
            WORKER / APPROVAL GATED
            <br />
            ARTIFACTS / RETAINED {form.retention} DAYS
            <br />
            NOTIFICATIONS / {form.notifications ? "SELECTIVE" : "OFF"}
          </div>
          <div className="panel-links">
            <a href="/reports">
              Review release posture <span>↗</span>
            </a>
            <a href="/assistant">
              Ask about configuration <span>↗</span>
            </a>
          </div>
        </div>
      </section>
    </FeaturePage>
  );
}
