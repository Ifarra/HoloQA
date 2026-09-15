# HoloQA Docker MVP

## Included

- Dashboard at `http://localhost:8000`
- Demo application at `http://localhost:8765/demo`
- Local MCP server over stdio
- Project initialization and Git snapshot metadata
- XLSX test-case import
- Reviewable run plans and approval gate
- Real browser execution with Playwright/Chromium
- Screenshots as evidence
- JSON, HTML, and annotated XLSX reports
- Persistent Docker volume for project state and artifacts

## Start the platform

```bash
docker compose up --build -d
```

Open:

- Dashboard: http://localhost:8000
- Demo application: http://localhost:8765/demo
- Dashboard health: http://localhost:8000/health

Stop it:

```bash
docker compose down
```

State is stored in the `holoqa-data` Docker volume. To remove state too:

```bash
docker compose down -v
```

## Local MCP connection

The MCP server must run where the AI coding client can access the workspace. For a local checkout, use the local Python command:

```bash
uv sync --dev
uv run holoqa-mcp
```

The containerized MCP process is available for environments that mount the workspace into the container:

```bash
docker compose -f docker-compose.mcp.yml run --rm holoqa-mcp
```

## Workbook format

The initial importer expects these headers on the first sheet:

```text
Test ID | Title | Steps | Expected Result
```

Separate multiple steps with semicolons.

## Real browser execution

After importing a workbook and creating/approving a plan, call `holoqa_execute_run` with:

```text
base_url = http://demo-app:8765
```

The worker runs Chromium inside the HoloQA container and stores screenshots under the configured state volume.

## MVP limitations

- The demo app is intentionally simple and is included for repeatable validation.
- The browser runner currently supports semantic execution for the demo user-creation flow; arbitrary workflows need additional action adapters.
- Authentication, multi-user permissions, remote Streamable HTTP, and GitHub/GitLab OAuth are not included yet.
- Docker Desktop must be running before `docker compose` commands can be verified.
