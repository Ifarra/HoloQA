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

## Agent-browser execution

After importing a workbook and creating/approving a plan, call `holoqa_open_run_session` with the approved plan.

```text
agent_id = cursor
```

The connected AI coder runs agent-browser locally against the reachable target, handles recoverable UI changes, and sends ordered events to HoloQA. Use holoqa_prepare_artifact_upload and holoqa_commit_artifact for Garage-backed evidence.

## MVP limitations

- The demo app is intentionally simple and is included for repeatable validation.
- The server does not include Chromium or a browser worker; arbitrary workflows are handled by the connected AI coder through agent-browser.
- Authentication remains local to the client browser; raw cookies and storage state are never sent to HoloQA.
- Docker Desktop must be running before `docker compose` commands can be verified.
