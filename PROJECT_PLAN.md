# HoloQA — Product and Initialization Plan

## 1. Product thesis

HoloQA is an open-source, self-hostable agentic SIT/UAT platform. It converts requirements and business workflows into executable browser tests, uses a persistent codebase knowledge graph to ground planning, executes through a controlled browser, and returns evidence-backed results.

The core promise is not “AI says the test passed.” It is:

> Every verdict is an auditable chain of requirement → action → observation → evidence → validation.

The current concept in `IDEA.md` already defines the testing foundation: browser-driven execution, SIT/UAT modes, evidence collection, recovery, replay, reports, and CI/CD integration. This plan adds the code-intelligence and platform architecture around it.

## 2. What the platform should understand

For each connected project, HoloQA should build a unified project model from:

- Source code and its dependency/call graph.
- Routes, pages, components, forms, API clients, and server endpoints.
- Database migrations and schemas when available.
- OpenAPI/GraphQL contracts.
- Existing tests, fixtures, seed data, and CI configuration.
- Requirements, acceptance criteria, issue descriptions, and uploaded documents.
- Runtime observations from the browser and test environment.

The code graph is the durable memory of the software structure. It should not be treated as a transcript or a vector-only memory. The graph should preserve source references, symbol relationships, version/commit identity, and confidence/provenance for every derived fact.

CodeGraph is a strong initial indexing foundation because its documentation describes Tree-sitter-based parsing, incremental indexing, an MCP server, and impact analysis.[1] Its repository describes local-first operation and agent integrations over MCP.[2]

## 3. Product boundaries

### In scope for the first release

1. Connect one repository from a local path or Git URL.
2. Index a supported web application and expose a searchable code graph.
3. Accept a written requirement or acceptance criteria.
4. Generate a reviewable test plan before execution.
5. Execute the plan in an isolated browser session.
6. Capture screenshots, DOM/accessibility snapshots, console events, URLs, network metadata, and timestamps.
7. Produce HTML/JSON/Markdown reports.
8. Support an uploaded tabular test specification and return an annotated result workbook.
9. Run locally with Docker Compose.
10. Expose agent tools through a stable internal tool interface and optionally MCP.

### Explicitly out of scope for the first release

- Fully autonomous production testing.
- Unrestricted access to private networks or arbitrary credentials.
- Automatic destructive data mutation without approval.
- Claiming backend correctness from UI evidence alone.
- Supporting every language, framework, browser, and document type at launch.
- Training a proprietary model.

## 4. Proposed architecture

```text
                         ┌─────────────────────────────┐
                         │ Web UI / CLI / API / CI      │
                         └──────────────┬──────────────┘
                                        │
                         ┌──────────────▼──────────────┐
                         │ Project & Test Orchestrator   │
                         │ jobs, approvals, policies    │
                         └───────┬─────────┬────────────┘
                                 │         │
              ┌──────────────────▼─┐   ┌──▼──────────────────┐
              │ Code Intelligence    │   │ Agent Runtime       │
              │ repository adapter   │   │ planner/executor    │
              │ parser + CodeGraph   │   │ validator/critic    │
              │ graph query service   │   │ report generator    │
              └──────────┬───────────┘   └──────────┬──────────┘
                         │                          │
              ┌──────────▼───────────┐   ┌──────────▼──────────┐
              │ Graph + metadata      │   │ Browser sandbox      │
              │ Postgres/graph store  │   │ Playwright/agent     │
              │ object artifact store │   │ browser + observers  │
              └───────────────────────┘   └─────────────────────┘
```

### Recommended service boundaries

- **API service:** authentication, projects, repositories, test cases, runs, reports.
- **Worker service:** indexing, document parsing, test execution, report generation.
- **Orchestrator:** durable job state, retries, cancellation, approval gates.
- **Code intelligence adapter:** wraps CodeGraph initially; isolates future replacement.
- **Agent runtime:** model-agnostic planner, tool loop, structured state machine.
- **Browser runner:** isolated browser container/session with strict network policy.
- **Evidence service:** immutable artifacts plus searchable event metadata.
- **Web UI:** project overview, graph explorer, test authoring, live run, evidence timeline.

Start as a modular monolith with separate worker processes, not microservices. Keep interfaces explicit so services can be split later.

## 5. Code graph design

### Ingestion flow

```text
Repository source
  → clone/mount at pinned commit
  → ignore generated/vendor files
  → parse and index
  → normalize symbols and relationships
  → detect routes/API/schema/test surfaces
  → store graph snapshot
  → expose scoped context to agents
```

### Required graph entities

- Repository, branch, commit, file, module.
- Class, function, method, variable, type, component.
- Route, UI element, API endpoint, event, database table.
- Test, fixture, requirement, acceptance criterion.
- Runtime observation and evidence artifact.

### Required relationships

- `defines`, `imports`, `calls`, `extends`, `implements`.
- `renders`, `routes_to`, `requests`, `responds_with`.
- `reads`, `writes`, `migrates`, `covered_by`.
- `satisfies`, `observed_by`, `failed_at`.

Every graph fact should carry:

- source file and line range when available;
- commit or snapshot ID;
- parser/derivation method;
- confidence and freshness;
- visibility/sensitivity classification.

The agent should query small, task-specific graph neighborhoods rather than receive the entire graph. Example query: “For the invite-member requirement, find the route, rendered form, submit handler, API endpoint, persistence path, related tests, and likely seed data.”

## 6. Agent workflow

### Phase A — Understand

1. Parse the requirement into goals, actors, preconditions, actions, expected outcomes, and ambiguity.
2. Query the code graph for likely implementation surfaces.
3. Identify the target environment, credentials, test data, and safety policy.
4. Generate a structured test plan with citations to code and requirement sources.
5. Ask for approval when the plan is destructive, ambiguous, or high-risk.

### Phase B — Execute

1. Create a run with a pinned repository and environment configuration.
2. Open a browser session with network and filesystem restrictions.
3. Perform semantic browser actions using accessibility/page state first, selectors second.
4. Capture evidence before and after meaningful actions.
5. Observe UI, URL, DOM, console, and network signals.
6. Use code-graph context to recover from unexpected application states.

A browser automation layer is appropriate here because agent-browser-style tools provide compact agent-oriented page snapshots and interaction primitives; the referenced project also emphasizes safety controls such as blocking unsafe protocols/private targets by default.[3][4]

### Phase C — Validate

For each step, store:

```json
{
  "action": "click",
  "target": "Invite member",
  "observation": "Invitation form appeared",
  "assertions": ["form is visible"],
  "evidence_ids": ["..."],
  "status": "passed",
  "confidence": 0.96
}
```

Separate these verdicts:

- **PASS:** expected behavior is directly supported by evidence.
- **FAIL:** an expected assertion is contradicted by evidence.
- **BLOCKED:** execution could not continue because of environment, access, or missing data.
- **INCONCLUSIVE:** evidence is insufficient or conflicting.

The platform must never silently convert blocked or inconclusive into passed.

### Phase D — Report

Generate:

- Human report with summary, step timeline, screenshots, failures, and reproduction details.
- Machine report in JSON for CI/CD.
- Workbook output when the input is Excel.
- Links from every verdict to its evidence and source requirement.

## 7. Input and output contracts

### Input adapters

- Text prompt.
- Markdown, PDF, DOCX, and issue export.
- XLSX test cases.
- GitHub/GitLab issue or pull request.
- API/CLI JSON request.

Normalize all inputs into a canonical `TestSpecification`:

```json
{
  "title": "Invite organization member",
  "actor": "organization administrator",
  "preconditions": [],
  "steps": [],
  "expected_outcomes": [],
  "source_refs": [],
  "data_requirements": [],
  "risk_policy": {}
}
```

### Excel behavior

Do not overwrite the user’s original workbook. Return a new workbook containing:

- Original test case ID and text.
- Normalized steps and expected results.
- Execution status.
- Actual result.
- Evidence links or artifact paths.
- Failure reason and suspected component.
- Run ID, environment, commit, and timestamp.
- Reviewer/approval field.

Support a documented column mapping first; add heuristic column detection only with a preview and user confirmation.

## 8. Security model

Self-hosted does not mean safe by default. Design for hostile repositories, prompts, webpages, and test data.

- Run repository indexing and browser execution in separate containers.
- Use read-only repository mounts where possible.
- Default browser network policy to approved hosts only.
- Block cloud metadata endpoints, local network ranges, unsafe protocols, and arbitrary downloads by default.
- Store secrets in a secret manager or injected runtime references; never in prompts, graph nodes, logs, screenshots, or reports.
- Redact credentials, tokens, cookies, and personal data from evidence.
- Add explicit approval gates for delete, payment, email, permission, and production actions.
- Record policy decisions and tool calls in an audit log.
- Treat repository content and web content as untrusted data, not instructions.

## 9. Storage proposal

Use replaceable interfaces, with a practical default stack:

- **PostgreSQL:** users, projects, repositories, runs, normalized graph metadata, event indexes.
- **Graph representation:** start with relational edge tables or an embedded graph layer; do not introduce a specialized graph database until query patterns prove it necessary.
- **Object storage:** S3-compatible MinIO for screenshots, videos, traces, workbooks, and reports.
- **Queue:** Redis-backed jobs initially; move to a durable workflow engine if long-running runs require it.
- **Search/vector index:** optional later, for requirements and evidence retrieval. The code graph remains authoritative for code relationships.

## 10. Repository and provider strategy

Create a provider abstraction:

```text
RepositoryProvider
  - LocalWorkspaceProvider
  - GitProvider
  - GitHubProvider
  - GitLabProvider
  - Future: BitbucketProvider
```

Each provider should return the same pinned workspace contract: repository identity, commit SHA, checkout path, changed files, and access scope. Begin with local path + HTTPS Git clone. Add GitHub/GitLab OAuth or app integration only after the local workflow is reliable.

## 11. Initialization sequence

### Milestone 0 — Decisions and repository hygiene

- Choose project name, license, language/runtime, supported model providers, and supported browsers.
- Create a real Git repository with `README`, `LICENSE`, `CONTRIBUTING`, `SECURITY`, `CODE_OF_CONDUCT`, and ADR directory.
- Convert this plan and `IDEA.md` into a product spec plus explicit non-goals.
- Define the canonical JSON schemas before implementing UI.

### Milestone 1 — Vertical slice

Build one end-to-end path:

```text
local repo + text requirement
  → code index
  → approved test plan
  → browser run against demo app
  → evidence bundle
  → HTML/JSON report
```

Use a deterministic demo application and seeded data. The success criterion is a repeatable run, not broad framework support.

### Milestone 2 — Code intelligence

- Integrate CodeGraph behind an adapter.
- Store graph snapshot metadata and source references.
- Add graph search and impact/context queries.
- Show “why the agent chose this route/API/test” in the UI.

### Milestone 3 — Production-shaped execution

- Job queue and resumable runs.
- Browser sandbox and network policies.
- Retry/recovery states.
- Artifact storage and redaction.
- Human approval checkpoints.

### Milestone 4 — Input/output expansion

- XLSX adapter and annotated workbook output.
- GitHub/GitLab integrations.
- CI webhook and release-gate mode.
- Markdown/PDF/DOCX requirement adapters.

### Milestone 5 — Open-source hardening

- Docker Compose quick start.
- Versioned API and schema migrations.
- Plugin interfaces for parsers, model providers, browser tools, repository providers, and report formats.
- Test fixtures, threat model, reproducible demo, contributor documentation.

## 12. Suggested initial repository layout

```text
holoqa/
  apps/
    api/
    worker/
    web/
  packages/
    contracts/
    code-intelligence/
    agent-runtime/
    browser-runner/
    evidence/
    report-formats/
    repository-providers/
  infra/
    docker/
    migrations/
  examples/
    demo-app/
    demo-tests/
  docs/
    adr/
    architecture/
    security/
  tests/
    integration/
    fixtures/
  docker-compose.yml
```

## 13. Key architectural decisions to make next

1. **Runtime:** TypeScript end-to-end, or TypeScript API plus Rust/Python indexing workers?
2. **Agent protocol:** internal tool contracts only first, or MCP from the first public release?
3. **Graph:** wrap CodeGraph as an external process/package, or fork/embed its engine?
4. **Execution:** Playwright directly, or an agent-browser-compatible adapter?
5. **Workflow durability:** queue + database state machine first, or Temporal-like workflow engine?
6. **Model policy:** bring-your-own-key only, local models, hosted providers, or all three through adapters?
7. **Multi-tenancy:** single-user self-hosted first, or organization/role model from day one?
8. **Evidence retention:** configurable retention and redaction policy before any cloud deployment.

Recommended answers for the MVP: TypeScript modular monolith, CodeGraph adapter rather than fork, Playwright/browser adapter, PostgreSQL + MinIO + Redis, BYO model provider, single-tenant deployment, and MCP after the internal contracts stabilize.

## 14. Success criteria

The first credible release should demonstrate:

- A new user can start the stack with Docker Compose.
- A local repository can be indexed at a pinned commit.
- A requirement produces a reviewable plan with source references.
- A test runs against the demo app without hand-written selectors for every step.
- Each verdict links to concrete evidence.
- A failed run is reproducible from its run ID and artifacts.
- An XLSX input returns a new annotated XLSX output.
- The system fails safely when credentials, access, or evidence are insufficient.

## Sources

[1] https://colbymchenry.github.io/codegraph — CodeGraph documentation
[2] https://github.com/colbymchenry/codegraph — CodeGraph repository
[3] https://github.com/talocode/agent-browser — Agent Browser repository
[4] https://agent-browser.dev — Agent Browser documentation
