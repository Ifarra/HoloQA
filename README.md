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

## Development Compose

Use the development stack while changing Python or dashboard code. It bind-mounts the repository into the containers and runs Uvicorn with reload enabled, so source changes do not require an image rebuild:

```bash
docker compose -f docker-compose.dev.yml up
```

Open the same dashboard and demo URLs. Run the MCP service separately when an MCP client needs it:

```bash
docker compose -f docker-compose.dev.yml run --rm mcp
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

The reproducible demo workbook is at `fixtures/demo_cases.xlsx`. Regenerate it with
`uv run python scripts/create_demo_workbook.py`. The demo supports an intentional
server failure for negative testing when started with `HOLOQA_DEMO_FAILURE=1`.

## Documents

- `docs/MVP_RUNBOOK.md` — Docker MVP scope and runbook.
- `docs/ARCHITECTURE.md` — product and MCP architecture.
- `docs/PRODUCT_PLAN.md` — canonical product plan and scope.
- `docs/MILESTONES.md` — active delivery milestones.
- `docs/testing/JUICESHOP_TEST_PLAN.md` — Juice Shop validation matrix.
