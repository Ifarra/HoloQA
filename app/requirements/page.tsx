"use client";
import { useEffect, useState } from "react";
import { api, type Project, type Requirement, type Run } from "@/lib/api";
import { FeaturePage } from "@/components/feature-page";

export default function Requirements() {
  const [items, setItems] = useState<Requirement[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [projectId, setProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [message, setMessage] = useState("");
  const load = () => {
    Promise.all([
      api<{ requirements: Requirement[] }>(
        `/requirements${projectId ? `?project_id=${projectId}` : ""}`,
      ),
      api<{ projects: Project[] }>("/projects"),
      api<{ runs: Run[] }>("/runs"),
    ])
      .then(([r, p, rs]) => {
        setItems(r.requirements);
        setProjects(p.projects);
        setRuns(rs.runs);
      })
      .catch((e) => setMessage(e.message));
  };
  useEffect(() => {
    load();
  }, [projectId]);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId) {
      setMessage("Choose a project first.");
      return;
    }
    try {
      await api("/requirements", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          title,
          description,
          priority: "high",
        }),
      });
      setTitle("");
      setDescription("");
      setMessage("Requirement added to the traceability register.");
      load();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  return (
    <FeaturePage
      eyebrow="Traceability / 06"
      title="REQUIREMENTS"
      intro="Connect business intent to test cases, execution evidence, and release decisions."
    >
      <section className="section grid-2">
        <form className="panel" onSubmit={create}>
          <div className="eyebrow">Register intent</div>
          <h3>New requirement</h3>
          <div className="field">
            <label>Project</label>
            <select
              required
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
            >
              <option value="">Select workspace</option>
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.project_name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Title</label>
            <input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Checkout calculates the final price"
            />
          </div>
          <div className="field">
            <label>Acceptance intent</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What must be observable for this to pass?"
            />
          </div>
          <button className="button primary">Add to register ↗</button>
          <p className="muted">{message}</p>
        </form>
        <div className="panel">
          <div className="eyebrow">Coverage contract</div>
          <h3>Evidence, not checkbox theater.</h3>
          <p className="muted">
            Requirements become useful when they point to executable tests and
            real run evidence. Link test IDs in the requirement record, then use
            the release report to expose uncovered intent.
          </p>
          <div className="metrics">
            <div className="metric">
              <strong>{items.length}</strong>
              <span>requirements</span>
            </div>
            <div className="metric">
              <strong>
                {items.filter((r) => r.status === "covered").length}
              </strong>
              <span>covered</span>
            </div>
            <div className="metric">
              <strong>{runs.filter((r) => r.status === "FAIL").length}</strong>
              <span>failed runs</span>
            </div>
          </div>
        </div>
      </section>
      <section className="section">
        <div className="section-head">
          <div>
            <div className="eyebrow">Traceability matrix</div>
            <h2>Business intent in flight</h2>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Requirement</th>
                <th>Priority</th>
                <th>Linked tests</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r, i) => (
                <tr key={r.requirement_id}>
                  <td className="mono">
                    <span className="index">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    {r.key}
                  </td>
                  <td>
                    <strong>{r.title}</strong>
                    <div className="muted">{r.description}</div>
                  </td>
                  <td>
                    <span className="status fail">
                      <i />
                      {r.priority}
                    </span>
                  </td>
                  <td className="mono">{r.test_ids?.length || 0}</td>
                  <td>
                    <span
                      className={`status ${r.status === "covered" ? "pass" : "blocked"}`}
                    >
                      <i />
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
              {!items.length && (
                <tr>
                  <td colSpan={5}>
                    <div className="empty">
                      No requirements yet. Add one above or import from your
                      workbook through MCP.
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </FeaturePage>
  );
}
