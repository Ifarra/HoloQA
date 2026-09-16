# HoloQA MCP-First Architecture

## 1. Strategic shift

HoloQA should not be an autonomous AI platform that owns the agent loop. It should be an **agent capability platform** exposed primarily through MCP.

The user's existing AI coder—Codex, Claude Code, OpenCode, Cursor, Gemini, or another MCP-capable client—remains responsible for:

- Understanding the user's natural-language intent.
- Deciding which HoloQA tools to call.
- Combining HoloQA results with the repository context.
- Asking the user for clarification or approval.
- Maintaining the conversation and reasoning loop.

HoloQA is responsible for the deterministic and stateful capabilities that agents should not have to rebuild:

- Project initialization.
- CodeGraph indexing and graph queries.
- Requirement and test-case normalization.
- Browser/SIT execution primitives.
- Evidence capture and storage.
- Run state, artifacts, and reports.
- Safety policies, approvals, and audit history.

This is a stronger initial product boundary than hosting an agent ourselves. It avoids competing with coding agents and makes HoloQA useful wherever MCP is supported.

MCP is designed to connect AI applications to external data sources, tools, and workflows. Its server primitives map well to this product: tools are model-controlled operations, resources expose context, and prompts provide user-triggered workflows.[3][4][5]

MCP supports both local stdio and remote Streamable HTTP transports.[1]

## 2. The most important architectural distinction

There are two different things called “the platform”:

### HoloQA Control Plane

The self-hosted web/API service that stores:

- Projects and repository identities.
- CodeGraph snapshots or indexes.
- Test specifications.
- Execution runs and state transitions.
- Evidence metadata and artifact references.
- Reports and historical results.
- Users, permissions, policies, and approvals.

### HoloQA Local Bridge

A local MCP process or companion CLI running beside the user's repository. It can:

- Read the local workspace.
- Run CodeGraph against the exact checkout.
- Start or connect to the application under test.
- Access local Excel files supplied to the coding agent.
- Launch or connect to a browser runner.
- Upload graph snapshots, source metadata, test inputs, and artifacts to the control plane.

A remote self-hosted MCP server cannot magically read the user's local checkout or the Excel attachment on the user's machine. The local bridge is therefore necessary for local-first workflows. The bridge can communicate with the control plane over authenticated HTTPS, while the IDE connects to the bridge using stdio.

```text
┌────────────────────────────── User workstation ──────────────────────────────┐
│                                                                              │
│  IDE / Codex / Claude Code / OpenCode                                        │
│                 │ MCP stdio                                                  │
│                 ▼                                                            │
│          HoloQA Local Bridge ───── local repo, Excel, CodeGraph, browser     │
│                 │ authenticated HTTPS                                        │
└─────────────────┼────────────────────────────────────────────────────────────┘
                  ▼
       ┌──────────────────────────────┐
       │ Self-hosted HoloQA Control   │
       │ Plane                        │
       │ MCP HTTP endpoint + API      │
       │ project/run/evidence store  │
       │ web dashboard                │
       └──────────────┬───────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   PostgreSQL      Object store   Workers
   metadata       reports/media  indexing/runs
```

## 3. Deployment modes

### Mode A — Local-only

Best for privacy and the first MVP.

- IDE connects to a local HoloQA MCP process over stdio.
- Repository, CodeGraph, browser, evidence, and reports stay local.
- Optional local dashboard.
- No account or remote service required.

### Mode B — Local bridge + self-hosted control plane

Best for teams.

- Local bridge handles repository and browser access.
- Control plane stores shared history, reports, graph metadata, and project views.
- Multiple developers can see the same runs and reports.
- Source code upload is configurable: metadata-only, selected files, or full snapshot.

### Mode C — Remote repository execution

Best for CI or cloud-hosted Git repositories.

- Control plane receives a repository provider connection or CI workspace.
- A remote worker checks out a pinned commit.
- Browser and test environment run in an isolated worker.
- No local bridge is required, but private application environments may still require a runner inside the target network.

Do not force one mode to solve all environments. Make the workspace/execution location explicit in every project and run.

## 4. User experience

### Initialization

User says:

> Initialize HoloQA.

The external coding agent should discover and call a workflow such as:

```text
holoqa_project_inspect
holoqa_initialize_project
holoqa_index_status
holoqa_publish_snapshot
```

A robust initialization sequence is:

1. Detect the workspace root and Git remote.
2. Detect framework, language, package manager, test commands, and likely app entrypoints.
3. Ask for missing information only when required: application start command, base URL, test environment, or policy.
4. Create or select a HoloQA project.
5. Pin the current Git commit and create an initialization run.
6. Run CodeGraph indexing through the local bridge.
7. Extract project surfaces: routes, forms, APIs, tests, migrations, fixtures, and environment requirements.
8. Upload the graph snapshot or permitted graph metadata.
9. Return a concise initialization summary with project ID, commit, indexed files, detected app command, and unresolved items.

The tool should be idempotent. Repeating “Initialize HoloQA” must update the project and create a new snapshot rather than create duplicates.

### SIT request with Excel

User says:

> Please do SIT on this project. I attached an Excel document.

The coding agent should call a workflow like:

```text
holoqa_inspect_project
holoqa_import_test_workbook
holoqa_validate_test_spec
holoqa_create_run_plan
holoqa_request_approval
holoqa_execute_run
holoqa_get_run_status
holoqa_export_test_workbook
```

Expected sequence:

1. Locate the attached workbook or ask the client to provide its local path/resource URI.
2. Upload or register the file through the local bridge; preserve the original unchanged.
3. Detect sheets, headers, test IDs, steps, expected results, data fields, and ambiguous rows.
4. Return a validation preview before execution.
5. Match test cases to code-graph surfaces and environment capabilities.
6. Identify missing prerequisites: credentials, seed data, base URL, service dependencies, or test data.
7. Produce an execution plan with risk and approval requirements.
8. Execute only approved cases.
9. Store step-level evidence and outcome states.
10. Export a new annotated workbook and a report URL/resource.

The agent is responsible for interpreting the user's phrase “do SIT,” but HoloQA must provide structured output so the agent cannot accidentally skip validation or approval stages.

## 5. MCP surface design

Avoid exposing dozens of low-level tools initially. Large tool lists reduce discoverability and increase accidental calls. Expose a small number of workflow tools with strict schemas and structured results.

### Core tools

#### `holoqa_project_inspect`

Read-only. Detects current workspace/project state.

Returns:

- workspace identity;
- Git remote and current commit;
- framework/runtime detection;
- app start/test commands;
- existing HoloQA project association;
- index freshness;
- missing configuration.

#### `holoqa_initialize_project`

Creates or updates a project and starts indexing.

Required inputs should include an explicit `workspace_root`. Optional inputs include repository provider, project name, app command, base URL, and upload policy.

Must return a job ID and immediately usable status—not pretend indexing is complete if it is asynchronous.

#### `holoqa_index_status`

Reports job state, indexed commit, parser errors, unsupported files, and freshness.

#### `holoqa_query_codegraph`

Read-only, scoped graph queries such as:

- find route or page;
- trace request path;
- find callers/callees;
- find related tests;
- impact analysis;
- summarize subsystem around a symbol.

Do not make the model construct arbitrary database queries in the MVP. Use named query types plus validated parameters.

#### `holoqa_import_test_workbook`

Accepts a local resource reference or uploaded artifact ID. Produces a normalized test specification and validation issues. It must not execute anything.

#### `holoqa_create_run_plan`

Combines requirements, workbook cases, project configuration, graph context, and policies into a reviewable plan. Returns planned cases, prerequisites, risks, and required approvals.

#### `holoqa_approve_run`

Records explicit approval for a plan or selected test cases. This is important because MCP guidance recommends keeping a human in the loop for sensitive tool invocations.[3]

#### `holoqa_execute_run`

Starts execution of an approved plan. Returns a run ID. It should support cancellation and never hide blocked or inconclusive results as failures or passes.

#### `holoqa_get_run_status`

Returns state, progress, current case, blockers, and links to reports/evidence.

#### `holoqa_get_evidence`

Returns filtered evidence by run, test case, step, timestamp, or artifact type.

#### `holoqa_export_report`

Creates HTML, JSON, Markdown, or XLSX output and returns a resource link.

### MCP resources

Use resources for durable read-oriented context rather than forcing the agent to call a tool for every display operation. Candidate URI schemes:

```text
holoqa://projects/{project_id}
holoqa://projects/{project_id}/graph/snapshot/{snapshot_id}
holoqa://runs/{run_id}
holoqa://runs/{run_id}/timeline
holoqa://runs/{run_id}/evidence/{evidence_id}
holoqa://artifacts/{artifact_id}
holoqa://reports/{report_id}
```

Resources should expose metadata first and large content through explicit reads or artifact links. MCP resources support text and binary content, which is useful for reports, screenshots, and workbooks.[4]

### MCP prompts

Provide user-controlled prompts for common workflows, not hidden system instructions:

- `holoqa_initialize_project`
- `holoqa_prepare_sit_from_workbook`
- `holoqa_review_failed_run`
- `holoqa_generate_regression_cases`
- `holoqa_explain_project_architecture`

Prompts should produce a structured starting request that the user can review. MCP prompts are designed to be explicitly user-controlled.[5]

## 6. Tool contract rules

Every mutating tool should return:

```json
{
  "operation_id": "op_123",
  "status": "accepted",
  "resource_uri": "holoqa://runs/run_123",
  "next_actions": ["holoqa_get_run_status"],
  "requires_approval": false,
  "warnings": []
}
```

Every asynchronous operation needs:

- stable operation ID;
- state endpoint/resource;
- cancellation behavior;
- timeout and retry semantics;
- idempotency key;
- clear partial-failure state.

Never return a giant opaque text blob as the only result. Use `structuredContent` and an output schema so clients can validate and agents can reliably continue the workflow.[3]

## 7. What belongs in the platform versus the coding agent

### HoloQA owns

- CodeGraph execution and graph persistence.
- Repository snapshotting and commit identity.
- Workbook parsing and output generation.
- Browser/session orchestration.
- Evidence capture and immutable artifact metadata.
- Test/run state machine.
- Safety policies and approval enforcement.
- Authentication, authorization, audit logs, retention, and redaction.
- Stable schemas and compatibility across clients.

### The external coding agent owns

- Natural-language interpretation.
- Selecting a workflow based on the user's request.
- Explaining results in the conversation.
- Editing application code if the user asks for fixes.
- Deciding whether to rerun after a code change.
- Providing human clarification when requirements are ambiguous.

### The user owns

- Approving sensitive or destructive actions.
- Supplying valid credentials and environment access.
- Confirming ambiguous test mappings.
- Deciding whether a failure is accepted, fixed, or deferred.

This division keeps HoloQA deterministic and makes it portable across agents.

## 8. Excel attachment reality

“Attached Excel document” is not a universal MCP primitive. Different clients expose attachments differently. Therefore support three paths:

1. **Local path:** the client passes a path under the workspace.
2. **MCP resource URI:** the client exposes the attachment as a readable resource.
3. **Upload tool:** the local bridge uploads the file and returns an artifact ID.

The MCP server should not assume it can access arbitrary client filesystem paths. Validate paths against allowed workspace roots, enforce file-size/type limits, preserve the original artifact, and create a new output workbook.

## 9. Execution model

The MCP tool call starts and observes execution; it should not require the coding agent to emulate the full browser loop.

```text
Agent calls create_run_plan
          ↓
HoloQA validates policy and prerequisites
          ↓
Agent/user approves
          ↓
Agent calls execute_run
          ↓
HoloQA worker runs browser + observers
          ↓
Agent polls or receives status/resource updates
          ↓
Agent requests report/workbook
```

This is the key design compromise: HoloQA does not host an LLM agent, but it does host deterministic workers that perform indexing, browser actions, evidence capture, and report generation. Otherwise every external coding agent would need to implement and coordinate those mechanics independently.

## 10. Security and trust model

The MCP server is a high-impact tool server. Treat every repository file, workbook cell, webpage, and graph-derived description as untrusted data.

- Validate every tool argument and resource URI.
- Enforce project/user authorization on every operation.
- Scope local paths to configured workspace roots.
- Use allowlisted browser domains and block private network/metadata targets by default.
- Never put secrets in graph nodes, screenshots, DOM snapshots, logs, or workbook output.
- Redact credentials and personal data before persistence.
- Require approval for production targets, deletes, payments, permission changes, email, uploads, and external side effects.
- Rate-limit indexing, execution, artifact downloads, and graph queries.
- Keep audit records for tool calls, approvals, policy decisions, and artifact access.
- Bind local HTTP services to localhost; for remote Streamable HTTP use authentication and Origin validation as required by MCP guidance.[1][2]
- Do not pass an inbound MCP access token through to downstream providers; use separately scoped credentials.

## 11. Recommended first MVP

Build only this vertical slice:

```text
IDE MCP connection
  → local bridge
  → inspect local demo repository
  → initialize project
  → run CodeGraph indexing
  → import one known XLSX format
  → create plan
  → approve plan
  → execute against a deterministic demo web app
  → return evidence-backed XLSX + HTML report
```

The first release should support one local stdio server and one optional remote control-plane connection. Add remote Streamable HTTP after the local workflow is stable.

### MVP acceptance criteria

- Works from at least two MCP clients.
- Re-running initialization is idempotent.
- All asynchronous operations have IDs and status resources.
- A workbook is never overwritten.
- Every case has PASS, FAIL, BLOCKED, or INCONCLUSIVE status.
- Every PASS links to evidence.
- The platform records exact commit, environment, and run IDs.
- The agent can recover after a dropped MCP connection by reading run status.
- A user can inspect and approve the generated run plan before browser execution.

## 12. Decisions to make now

1. **Local bridge first or remote HTTP first?** Recommended: local stdio first.
2. **Does the control plane receive source code?** Recommended: configurable metadata-only default; full source upload opt-in.
3. **Does HoloQA execute browser actions itself?** Recommended: yes, through deterministic workers; the external agent still owns intent and conversation.
4. **Does HoloQA include its own LLM?** Recommended: no for MVP.
5. **Does CodeGraph remain an external dependency?** Recommended: adapter plus pinned version; do not fork initially.
6. **How does the agent know to use HoloQA?** Use clear tool descriptions, MCP prompts, and a concise integration README; do not depend on hidden instructions.
7. **How are long runs handled?** Durable run state plus polling/resource reads; optional notifications later.

## Sources

[1] https://modelcontextprotocol.io/specification/2025-06-18/basic/transports — MCP transports
[2] https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization — MCP authorization
[3] https://modelcontextprotocol.io/specification/2025-06-18/server/tools — MCP tools
[4] https://modelcontextprotocol.io/specification/2025-06-18/server/resources — MCP resources
[5] https://modelcontextprotocol.io/specification/2025-06-18/server/prompts — MCP prompts
