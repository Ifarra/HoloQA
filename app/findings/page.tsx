"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, type Finding } from "@/lib/api";
import { FeaturePage } from "@/components/feature-page";
export default function Findings() {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const load = () =>
    api<{ findings: Finding[] }>("/findings")
      .then((x) => setFindings(x.findings))
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);
  const visible = useMemo(
    () =>
      findings.filter((f) =>
        `${f.title} ${f.test_id} ${f.state}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [findings, query],
  );
  return (
    <FeaturePage
      eyebrow="Defect ledger / 07"
      title="FINDINGS"
      intro="Turn failed and blocked outcomes into traceable, actionable quality work."
      actions={
        <Link className="button primary" href="/reports">
          Release report ↗
        </Link>
      }
    >
      {error && <div className="callout section">{error}</div>}
      <section className="section">
        <div className="section-head">
          <div>
            <div className="eyebrow">Triage queue</div>
            <h2>
              {findings.filter((f) => f.state !== "closed").length} signals need
              attention
            </h2>
          </div>
          <input
            className="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter findings…"
          />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Finding</th>
                <th>Test</th>
                <th>Severity</th>
                <th>State</th>
                <th>Evidence</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((f, i) => (
                <tr key={f.finding_id}>
                  <td>
                    <span className="index">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <strong>{f.title}</strong>
                    <div className="muted">{f.actual}</div>
                  </td>
                  <td className="mono">{f.test_id}</td>
                  <td>
                    <span
                      className={`status ${f.severity === "critical" || f.severity === "high" ? "fail" : "blocked"}`}
                    >
                      <i />
                      {f.severity}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`status ${f.state === "closed" ? "pass" : "blocked"}`}
                    >
                      <i />
                      {f.state}
                    </span>
                  </td>
                  <td className="mono">{f.evidence?.length || 0} artifacts</td>
                  <td>
                    <Link
                      className="link mono"
                      href={`/findings/${f.finding_id}`}
                    >
                      Inspect ↗
                    </Link>
                  </td>
                </tr>
              ))}
              {!visible.length && (
                <tr>
                  <td colSpan={6}>
                    <div className="empty">
                      No findings match the current filter.
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
