# HoloQA Delivery Milestones

This roadmap follows the MVP vertical slice. The goal is to preserve HoloQA's core promise: every verdict is an auditable chain of requirement, action, observation, evidence, and validation.

## Milestone 0 — MVP vertical slice

Status: complete.

The local MCP server can inspect and initialize a workspace, import an XLSX test specification, create and approve a plan, execute a deterministic browser workflow, preserve evidence, and export JSON, HTML, and XLSX reports.

## Milestone 1 — Product contract and trust hardening

Make the MVP a reliable foundation.

- Versioned schemas for projects, snapshots, test cases, plans, runs, steps, evidence, and reports.
- Bind approval to the exact plan, commit, environment, and selected cases.
- Enforce explicit `PASS`, `FAIL`, `BLOCKED`, and `INCONCLUSIVE` semantics.
- Validate inputs and return actionable errors.
- Remove or clearly label simulated execution.
- Add regression tests for safety-critical behavior.

Exit criterion: no run can claim success without valid execution evidence and matching approval context.

## Milestone 2 — Real CodeGraph integration

Make repository understanding a HoloQA product capability.

- Add a CodeGraph adapter interface.
- Index projects during initialization.
- Pin graph snapshots to commits.
- Query routes, handlers, APIs, forms, dependencies, and related tests.
- Attach graph context to plans.
- Explain why a route, API, or test path was selected.
- Expose graph search through MCP and dashboard APIs.

Exit criterion: a requirement produces a plan referencing its relevant implementation surfaces and related tests.

## Milestone 3 — General browser execution engine

Move beyond hard-coded demo actions.

- Support navigation, click, fill, select, upload, wait, inspect, and assert actions.
- Use accessibility-first page inspection.
- Add semantic element matching and selector fallbacks.
- Support multi-page workflows and modals.
- Add configurable retries and timeouts.
- Add authentication and session support.

Exit criterion: several unrelated workflows run without custom code for each test case.

## Milestone 4 — Complete evidence and validation system

Make every verdict reconstructable and reviewable.

- Store before/after screenshots per step.
- Capture DOM and accessibility snapshots.
- Capture console logs and network requests/responses.
- Record URL and navigation history.
- Support optional traces and video.
- Add step-level assertions, confidence, and validation.
- Add a critic/review stage.
- Redact secrets and personal data from evidence.

Exit criterion: every result links requirement, action, observation, assertion, verdict, and evidence.

## Milestone 5 — Durable execution and orchestration

Make runs production-shaped.

- Background worker and queue.
- Operation IDs and progress events.
- Cancellation, retry, resume, and run locking.
- Concurrency controls and failure recovery.
- Audit event timeline.

Exit criterion: a disconnected client can later inspect or safely resume a run.

## Milestone 6 — Operational dashboard

Turn the dashboard into a complete control plane.

- Project setup and workbook upload.
- Plan review and approval UI.
- Run start, cancel, retry, and progress controls.
- Evidence timeline and report downloads.
- CodeGraph explorer and failure investigation.

Exit criterion: a user can complete the workflow without manually calling MCP tools.

## Milestone 7 — Security and governance

Make self-hosting safe for real environments.

- Authentication, accounts, teams, and permissions.
- Secret and credential management.
- Browser network allowlists and filesystem isolation.
- Dangerous-action approval policies.
- Artifact access controls and retention.
- Redaction and security audit logs.

Exit criterion: untrusted repositories, webpages, credentials, and test data are isolated and governed.

## Milestone 8 — Repository and requirement integrations

Expand inputs after the core workflow is stable.

- Local and HTTPS Git providers.
- GitHub and GitLab integrations.
- Markdown, PDF, DOCX, and issue imports.
- OpenAPI and GraphQL ingestion.
- Requirement-to-test traceability.

Exit criterion: users can connect a repository and requirement source without manual format conversion.

## Milestone 9 — CI/CD and release gates

Make HoloQA part of delivery pipelines.

- CLI/API execution mode.
- GitHub Actions and GitLab CI integrations.
- Webhooks and scheduled runs.
- Environment registry and release policies.
- Machine-readable exit codes and notifications.
- Regression history and release-readiness scoring.

Exit criterion: a deployment can trigger HoloQA and block release when critical tests fail.

## Milestone 10 — Open-source and scale hardening

Prepare for external adoption.

- Stable public API and MCP contracts.
- Database migrations, PostgreSQL, and object storage.
- Production Docker profile, backups, and restore.
- Observability, metrics, and resource limits.
- Contribution, security, and release processes.
- Example repositories and plugin interfaces.

Exit criterion: a new user can install, understand, extend, operate, and upgrade HoloQA reliably.

## Recommended order

Complete Milestones 1–4 first. Then build Milestone 5, followed by Milestones 6 and 7. Add integrations and CI/CD only after the execution and evidence model is stable.
