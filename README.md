# HoloQA

HoloQA is an MCP-first, self-hosted system integration and user acceptance testing toolkit. AI coding clients use its structured tools to inspect a repository, import SIT cases, create and approve a plan, execute browser tests, preserve evidence, and export reports.

## Docker MVP

The Docker Compose setup starts both the HoloQA dashboard and a repeatable demo application used for real browser SIT validation:

```bash
docker compose up --build -d
```

Open:

- Dashboard: http://localhost:8000
- Demo application: http://localhost:8765/demo
- Health: http://localhost:8000/health

Stop it with `docker compose down`. Add `-v` to remove persistent state and artifacts.

The image includes Playwright and Chromium. State and generated artifacts are persisted in the `holoqa-data` volume. Mount a repository/workspace into `/workspace` when using the containerized MCP process.

## MCP client

For a local AI coding client, run the stdio server from the checkout:

```bash
uv sync --dev
uv run holoqa-mcp
```

For an isolated containerized MCP process:

```bash
docker compose -f docker-compose.mcp.yml run --rm holoqa-mcp
```

Available tools:

- `holoqa_project_inspect`
- `holoqa_initialize_project`
- `holoqa_import_test_workbook`
- `holoqa_create_run_plan`
- `holoqa_approve_run`
- `holoqa_execute_run`
- `holoqa_get_run_status`
- `holoqa_get_evidence`
- `holoqa_export_report`

If `base_url` is omitted, local execution uses the configured demo app URL. For Compose execution, pass `http://demo-app:8765`; for a host application use its URL reachable from the HoloQA container.

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

## Documents

- `MVP.md` — Docker MVP scope and runbook.
- `MCP_FIRST_ARCHITECTURE.md` — product and MCP architecture.
- `PROJECT_PLAN.md` — broader platform plan.
- `IDEA.md` — original SIT/UAT product notes.
