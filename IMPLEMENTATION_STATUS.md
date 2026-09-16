# HoloQA Implementation Status

**Assessment date:** 2026-09-15  
**Repository:** HoloQA  
**Overall completion against the full main plan:** **42%**

> This percentage is a planning estimate, not a line-of-code ratio. It weights the product capabilities in `PROJECT_PLAN.md` and `MCP_FIRST_ARCHITECTURE.md`, with extra weight on the core promise: repository understanding, browser execution, evidence, reports, self-hosting, and agent/MCP integration.

## Summary

| Area | Status | Completion | Notes |
|---|---:|---:|---|
| Product foundation and repository setup | Implemented | 100% | Python package, tests, Git repository, documentation, local commands |
| MCP-first interface | Partially implemented | 65% | Local stdio MCP tools exist; resources/prompts/remote HTTP do not |
| Project inspection and initialization | Partially implemented | 55% | Workspace/Git metadata and idempotent SQLite initialization exist; CodeGraph indexing does not |
| CodeGraph and durable code memory | Not implemented | 5% | Adapter and graph snapshot model are planned but not connected |
| Test workbook input/output | Partially implemented | 70% | XLSX import, validation, preservation, and annotated export exist; broad document adapters do not |
| Test planning and approval | Implemented | 75% | Reviewable plan and approval gate exist; graph-grounded planning and risk analysis do not |
| Browser/SIT execution | Partially implemented | 45% | Playwright/Chromium and demo flow work; arbitrary SIT workflows and full observations do not |
| Evidence and verdict model | Partially implemented | 40% | Screenshots and evidence records exist; step-level evidence and full PASS/FAIL/BLOCKED/INCONCLUSIVE semantics do not |
| Reports and artifacts | Partially implemented | 65% | JSON, HTML, XLSX outputs exist; source-linked, rich audit reports do not |
| Dashboard | Partially implemented | 50% | Project/run dashboard and APIs exist; graph explorer, live run view, and artifact UX are limited |
| Docker/self-hosted deployment | Implemented for local MVP | 75% | Compose, persistent volume, demo app, health checks, Playwright image work; production deployment does not |
| Repository providers | Not implemented | 0% | No GitHub/GitLab connection, cloning, webhooks, or OAuth |
| Security and multi-user platform | Not implemented | 10% | Approval exists; authentication, authorization, secrets, policy enforcement, and audit security do not |
| CI/CD and production operations | Not implemented | 10% | No queues, workers, retries, cancellation, CI integration, metrics, or backups |
| Open-source release hardening | Partially implemented | 25% | MIT-compatible direction and README/docs exist; contribution, release, security, and support processes do not |

## Implemented features

### 1. Local development foundation

- Python package with `pyproject.toml` and `uv.lock`.
- Source layout under `src/holoqa`.
- Automated tests; current suite passes with **12 tests**.
- Git repository and documented architecture/product plans.
- CLI entry points for MCP, dashboard, and demo application.

### 2. MCP server over stdio

The following tools are registered:

- `holoqa_project_inspect`
- `holoqa_initialize_project`
- `holoqa_import_test_workbook`
- `holoqa_create_run_plan`
- `holoqa_approve_run`
- `holoqa_execute_run`
- `holoqa_get_run_status`
- `holoqa_get_evidence`
- `holoqa_export_report`

The MCP server can be started with:

```bash
uv run holoqa-mcp
```

### 3. Workspace inspection and project initialization

Implemented:

- Workspace root inspection.
- Git presence detection.
- Detection of common project files such as `package.json`, `pyproject.toml`, `Cargo.toml`, and `go.mod`.
- SQLite project state.
- Idempotent project initialization behavior.
- Current Git metadata/snapshot storage.
- Persistent state database.

Not yet implemented:

- Framework-specific application discovery.
- Start-command detection and management.
- Environment prerequisite analysis.
- CodeGraph indexing during initialization.
- Graph snapshot upload/publishing.

### 4. Excel SIT workflow

Implemented:

- XLSX import using a defined template:

```text
Test ID | Title | Steps | Expected Result
```

- Workbook header validation.
- Test-case normalization.
- Semicolon-separated step support.
- Original workbook preservation.
- Annotated result workbook export.
- JSON report export.
- HTML report export.

Not yet implemented:

- PDF, DOCX, Markdown, issue, or API input adapters.
- Multiple workbook templates.
- Sheet selection and advanced column mapping.
- Data-driven test matrix handling.
- Automatic prerequisite extraction from requirements.

### 5. Plans, approval, and run state

Implemented:

- Reviewable plans before execution.
- Explicit approval requirement.
- Blocked execution when a plan is not approved.
- SQLite persistence for plans and runs.
- Run status retrieval.
- Persistent test results.
- Persistent screenshot evidence records.

Not yet implemented:

- Fine-grained per-step approval.
- Risk scoring.
- Destructive-action policies.
- Run cancellation.
- Retry and resume support.
- Durable background jobs.
- Audit event timeline.

### 6. Browser execution

Implemented:

- Playwright dependency.
- Chromium runtime in the Docker image.
- Headless browser execution.
- Demo application included in Compose.
- Semantic demo flow for creating a user.
- Initial and final screenshots.
- Failure screenshot capture.
- PASS/FAIL result generation for the supported flow.

Not yet implemented:

- Generic natural-language step execution.
- Agent-browser integration.
- Accessibility snapshot capture.
- DOM snapshot capture.
- Console event capture.
- Network event capture.
- Trace/video recording.
- Multi-page workflows.
- Authentication/session management.
- Browser isolation and network allowlists.
- Cross-browser execution.
- Parallel execution.

### 7. Dashboard

Implemented:

- FastAPI dashboard.
- `/health` endpoint.
- Project API.
- Run list API.
- Run detail API.
- Evidence download endpoint.
- Project list display.
- Recent run display.
- Docker exposure on port `8000`.

Not yet implemented:

- Interactive project setup UI.
- Workbook upload UI.
- Plan review/approval UI.
- Run control UI.
- Live execution progress.
- Evidence timeline with inline previews.
- Report download interface.
- CodeGraph visualization and search.
- User login and permissions.

### 8. Docker and self-hosting

Implemented and verified:

- Dockerfile based on the Playwright Python image.
- `docker-compose.yml`.
- HoloQA dashboard container.
- Demo application container.
- Health checks.
- Persistent named volume.
- Workspace mount.
- Playwright browser installation.
- Successful Compose image build.
- Successful service startup and health checks.

Current deployment shape:

```text
holoqa dashboard/control process
        │
        ├── SQLite state volume
        ├── artifact volume
        └── demo application
```

Not yet implemented:

- PostgreSQL.
- Object storage such as S3/MinIO.
- Redis or a job queue.
- Separate worker service.
- Reverse proxy/TLS.
- Remote MCP over Streamable HTTP.
- Authentication and authorization.
- Backup and restore.
- Production resource limits and scaling.

## Not implemented from the main plan

### CodeGraph integration

This is the largest missing core capability.

Missing:

- CodeGraph installation/invocation adapter.
- Tree-sitter indexing pipeline.
- Incremental graph updates.
- Graph snapshot persistence.
- Symbol/route/API/test surface extraction.
- Graph query MCP tools.
- Commit-pinned graph context.
- Graph-to-test-case mapping.
- Dashboard graph explorer.

### GitHub and GitLab connectivity

Missing:

- GitHub repository connection.
- GitLab repository connection.
- Clone/fetch workflows.
- Branch and commit selection.
- Provider webhooks.
- Pull request or issue ingestion.
- OAuth/token management.
- Remote execution runners.

### Agent capability layer

The external AI client owns the reasoning loop, but HoloQA still needs more deterministic capabilities:

- MCP resources for projects, snapshots, runs, evidence, and reports.
- MCP prompts for initialization and SIT workflows.
- Graph context retrieval.
- Browser workflow primitives beyond the demo adapter.
- Application lifecycle tools.
- Safe secrets and environment injection.
- Structured clarification/prerequisite results.

### Production SIT platform

Missing:

- Worker/orchestrator architecture.
- Durable operation IDs.
- Queue-based execution.
- Cancellation and resume.
- Retry policies.
- Run concurrency controls.
- Environment registry.
- Test data management.
- Service dependency management.
- CI/CD integration.
- Scheduled runs.
- Notifications.

### Security and governance

Missing or incomplete:

- User accounts.
- Teams and projects permissions.
- Authentication.
- Authorization.
- Secret storage.
- Credential redaction.
- Browser network restrictions.
- Filesystem sandboxing.
- Approval audit logs.
- Retention policies.
- Artifact access controls.
- Security policy documentation.

### Open-source readiness

Missing:

- Contribution guide.
- Code of conduct.
- Security policy.
- Issue and feature templates.
- Release/versioning process.
- CI pipeline.
- Container publishing workflow.
- Public API compatibility policy.
- Demo/sample repository.
- End-user troubleshooting guide.

## Completion interpretation

### Current MVP completion: **approximately 75%**

Against the narrowed Docker MVP scope, the project is approximately **75% complete** because it has:

- A working Docker Compose deployment.
- A ready dashboard.
- MCP workflow tools.
- Excel import/export.
- Approval-gated runs.
- Real Playwright execution for the demo application.
- Screenshot evidence.
- JSON/HTML/XLSX reports.

The remaining 25% of the MVP scope is mainly:

- A usable dashboard workflow instead of mostly read-only views.
- Generic test-step execution rather than one demo adapter.
- Real end-to-end MCP client validation.
- Rich evidence and report presentation.
- CodeGraph connection.
- Better failure, blocked, and inconclusive handling.

### Full main-plan completion: **approximately 42%**

The full product plan is larger than the current Docker MVP. Its major missing capabilities are CodeGraph memory, GitHub/GitLab connectivity, agent-browser/general browser execution, remote MCP, multi-user security, production workers, CI/CD, and open-source release hardening.

## Recommended next priorities

1. **Implement the CodeGraph adapter and snapshot model.**
2. **Replace the read-only dashboard with upload, plan approval, execution, and report screens.**
3. **Add generic browser action/assertion adapters and richer evidence capture.**
4. **Add canonical test specification models for text and document inputs.**
5. **Add GitHub/GitLab repository connection through a local bridge.**
6. **Add durable operations and a worker process.**
7. **Add authentication, secrets handling, and artifact access controls.**
8. **Add CI, release automation, contribution docs, and container publishing.**
