# HoloQA Next Step: Prove the MCP Vertical Slice

## Decision

The next step is **not** to build the full control plane, dashboard, remote MCP, or multi-agent compatibility layer.

Build and verify one local MCP workflow end to end:

```text
AI coding client
  → HoloQA local MCP server
  → inspect repository
  → initialize project
  → index with CodeGraph
  → import a known XLSX test file
  → generate a structured run plan
  → approve plan
  → execute against a deterministic demo app
  → return evidence-backed report and XLSX
```

This validates the central product hypothesis before infrastructure expands.

## Why this is the correct next step

The largest uncertainty is not whether MCP can expose tools. The uncertainty is whether an external coding agent can reliably use HoloQA to perform a complete SIT workflow with local files, CodeGraph context, browser execution, approvals, and structured results.

A vertical slice will reveal the real interface requirements between:

- the IDE/AI coding client;
- the local MCP server;
- CodeGraph;
- the browser runner;
- the workbook parser;
- the evidence model; and
- the report generator.

Do not optimize for broad integrations until this path works with one deterministic demo application and one workbook format.

## Scope of the first implementation

### Include

- Local MCP server over stdio.
- One local workspace/project.
- One pinned Git commit.
- CodeGraph adapter with a mock fallback for tests.
- One demo web application owned by this repository.
- Playwright or the selected browser adapter.
- One documented XLSX template.
- SQLite or PostgreSQL for run metadata; SQLite is acceptable for the first local prototype.
- Local artifact directory for screenshots, traces, reports, and workbooks.
- Structured MCP tool responses.
- Explicit plan approval before execution.

### Exclude

- Remote Streamable HTTP.
- OAuth and multi-user authorization.
- GitHub/GitLab OAuth integrations.
- Cloud object storage.
- Full dashboard.
- Multiple model providers.
- Arbitrary Excel layouts.
- Production execution.
- Autonomous self-healing.
- Hosting an LLM inside HoloQA.

## First workflow contract

### `holoqa_project_inspect`

Read-only. Detects:

- workspace root;
- Git commit;
- framework and package manager;
- application start command;
- existing HoloQA project state;
- CodeGraph availability;
- missing configuration.

### `holoqa_initialize_project`

Creates or updates the local project and starts indexing.

Must be idempotent for the same workspace and commit.

Returns:

```json
{
  "project_id": "project_demo",
  "snapshot_id": "snapshot_abc",
  "commit": "full-git-sha",
  "status": "completed",
  "indexed_files": 42,
  "warnings": []
}
```

If indexing is asynchronous, return `status: "accepted"` and an operation ID instead of claiming completion.

### `holoqa_import_test_workbook`

Accepts a permitted local file path or uploaded artifact reference.

Must:

- preserve the original workbook;
- validate required columns;
- assign stable test-case IDs;
- return parsing errors and ambiguous rows;
- produce a normalized test specification.

### `holoqa_create_run_plan`

Combines the normalized workbook, project metadata, CodeGraph context, and execution policy.

Must return:

- test cases selected;
- planned steps;
- matched routes/API surfaces;
- prerequisites;
- risks;
- required approvals;
- unresolved ambiguities.

This tool must not execute the test.

### `holoqa_approve_run`

Records approval for an exact plan version and selected test cases.

Approval must expire if the plan, commit, environment, or selected cases change.

### `holoqa_execute_run`

Executes only an approved plan.

Returns a run ID and state resource. It must support cancellation.

### `holoqa_get_run_status`

Returns:

- run state;
- current test case and step;
- progress;
- blockers;
- artifact references;
- final result when complete.

### `holoqa_export_report`

Generates:

- HTML report;
- JSON result;
- annotated XLSX output.

The output workbook must be a new file and must include the original test ID, actual result, status, failure reason, evidence references, commit, environment, and run ID.

## Demo application

Create a deliberately small demo application with:

- Login page.
- Admin dashboard.
- User management page.
- Create-user form.
- Backend endpoint for user creation.
- Success notification.
- User list showing the newly created user.
- One intentional failure mode controlled by test data or an environment variable.

The first workbook should contain 3–5 cases:

1. Create a valid user — expected PASS.
2. Reject an invalid email — expected PASS.
3. Prevent duplicate user — expected PASS.
4. Simulated server failure — expected FAIL or BLOCKED depending on the assertion.

This gives the evidence and verdict model more coverage than a single happy-path test.

## Repository initialization tasks

1. Create the Git repository.
2. Add `README.md`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md`.
3. Choose the runtime; recommended: TypeScript for the MCP server and orchestration layer.
4. Create the initial package layout:

```text
apps/
  mcp-server/
  demo-app/
packages/
  contracts/
  codegraph-adapter/
  workbook/
  browser-runner/
  evidence/
  reports/
  storage/
tests/
  contract/
  integration/
fixtures/
  workbooks/
  expected-reports/
```

5. Define JSON Schemas for project, snapshot, test case, run plan, run status, evidence, and report.
6. Implement the MCP server with only the six workflow tools above.
7. Add a fake CodeGraph adapter so contract tests run without the external dependency.
8. Add the real CodeGraph adapter behind the same interface.
9. Add the deterministic demo app and browser fixture.
10. Run the complete workflow from an MCP client.

## Definition of done

The slice is complete only when all of these pass:

- A supported AI coding client can discover the HoloQA MCP server.
- `holoqa_project_inspect` returns the correct workspace and commit.
- Initialization can be run twice without duplicate project records.
- CodeGraph context is tied to the exact commit.
- The workbook is parsed without modifying the original.
- The run plan is visible before execution.
- Execution cannot start without approval.
- The demo happy path produces PASS with screenshots and browser evidence.
- The intentional failure produces FAIL, BLOCKED, or INCONCLUSIVE correctly.
- Every PASS and FAIL has linked evidence.
- The generated XLSX opens successfully and contains the expected result columns.
- A run can be resumed or inspected after the MCP client disconnects.
- Contract tests validate tool inputs and structured outputs.

## After the vertical slice

Only after the above works should we add, in this order:

1. Docker Compose packaging.
2. PostgreSQL and MinIO adapters.
3. Remote Streamable HTTP MCP.
4. Authentication and project permissions.
5. GitHub/GitLab repository providers.
6. Dashboard and graph visualization.
7. Multiple workbook formats and document adapters.
8. CI/CD integrations.
9. Additional MCP client compatibility testing.

## Immediate product decision

Use the local MCP server as the product's first public interface. Treat the control plane and dashboard as extensions, not prerequisites.
