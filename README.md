# HoloQA

HoloQA is an MCP-first, self-hosted system integration and user acceptance testing harness. The connected AI coder owns browser execution through local `agent-browser`; HoloQA stores intent, run state, events, evidence, and reports.

## Docker MVP

The Docker Compose setup starts both the HoloQA dashboard and a repeatable demo application used for real browser SIT validation:

```bash
docker compose up --build -d
```

Open:

- Dashboard: http://localhost:8000
- Remote MCP: http://localhost:8100/mcp
- Demo application: http://localhost:8765/demo
- Health: http://localhost:8000/health

Stop it with `docker compose down`. Add `-v` to remove persistent state and artifacts.

The server image does not include a browser runtime. State and generated artifacts are persisted in the `holoqa-data` volume. Mount a repository/workspace into `/workspace` when using the containerized MCP process.

## Development Compose

Use the development stack while changing Python or dashboard code. It bind-mounts the repository into the containers and runs Uvicorn with reload enabled, so source changes do not require an image rebuild:

```bash
docker compose -f docker-compose.dev.yml up
```

Open the same dashboard and demo URLs. Run the MCP service separately when an MCP client needs it:

For a remote Streamable HTTP MCP connection in the development stack, start the `mcp-http` service and use `http://localhost:8100/mcp` (or replace `localhost` with the Docker host):

```bash
docker compose -f docker-compose.dev.yml up mcp-http
```

The first development start creates a named `.venv` volume and may install dependencies. After changing `pyproject.toml` or `uv.lock`, refresh that environment with:

```bash
docker compose -f docker-compose.dev.yml run --rm holoqa uv sync --dev
```

If the dependency environment becomes stale, remove only the development volumes and start again:

```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up
```

## MCP client

HoloQA supports remote Streamable HTTP MCP only. Start the Compose stack and configure Cursor, Claude Code, or another AI coding client with:

```text
http://<docker-server-host>:8100/mcp
```

The application under test must be mounted on the Docker host beneath `./workspace`, which is exposed to the MCP container as `/workspace`. The dashboard and MCP server use the same `/data/.holoqa/state.db`; tools do not accept client-provided database paths.

Artifacts are stored in the bundled Garage S3-compatible object store under the `holoqa-artifacts` bucket. Garage persists its metadata and objects in the `garage-meta` and `garage-data` Docker volumes. Set `HOLOQA_S3_ACCESS_KEY_ID` and `HOLOQA_S3_SECRET_ACCESS_KEY` in the deployment environment; the Compose defaults are for development only.

Available tools:

- `holoqa_project_inspect`
- `holoqa_initialize_project`
- `holoqa_import_test_workbook`
- `holoqa_create_run_plan`
- `holoqa_approve_run`
- `holoqa_open_run_session`
- `holoqa_agent_heartbeat`
- `holoqa_record_agent_event`
- `holoqa_prepare_artifact_upload`
- `holoqa_commit_artifact`
- `holoqa_update_case_verdict`
- `holoqa_complete_run`
- `holoqa_get_run_status`
- `holoqa_get_evidence`
- `holoqa_export_report`

The AI coder creates and approves plans through MCP, opens an agent session, runs the browser locally with `agent-browser`, and streams ordered events and Garage-backed artifacts to the server. Authentication stays local to the browser session; raw cookies and storage state are never sent to HoloQA. The dashboard is read-only for plans and runs.

## SIT workbook

The initial importer expects these headers on the first sheet:

```text
Test ID | Title | Steps | Expected Result
```

Separate steps with semicolons. HoloQA never overwrites the source workbook. Reports are written as JSON, HTML, and an annotated XLSX containing status, actual result, and evidence paths.

## Development

```bash
uv sync --dev
uv run pytest -q
```

The reproducible demo workbook is at `fixtures/demo_cases.xlsx`. Regenerate it with
`uv run python scripts/create_demo_workbook.py`.

## Documents

- `docs/MVP_RUNBOOK.md` — Docker MVP scope and runbook.
- `docs/ARCHITECTURE.md` — product and MCP architecture.
- `docs/PRODUCT_PLAN.md` — canonical product plan and scope.
- `docs/MILESTONES.md` — active delivery milestones.
- `docs/testing/JUICESHOP_TEST_PLAN.md` — Juice Shop validation matrix.
