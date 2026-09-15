# HoloQA

HoloQA is an MCP-first, self-hosted system integration and user acceptance testing toolkit. It gives AI coding clients structured tools for project inspection, CodeGraph indexing, test planning, browser execution, evidence capture, and report generation.

## Current status

The repository is at the first vertical-slice stage. The initial implementation target is a local MCP server that can inspect a workspace and initialize a HoloQA project.

## Development

```bash
uv sync --dev
uv run pytest -q
uv run holoqa-mcp
```

The MCP server uses stdio so it can be connected to an MCP-capable coding client.

## Documents

- `NEXT_STEP.md` — immediate implementation scope and acceptance criteria.
- `MCP_FIRST_ARCHITECTURE.md` — product and MCP architecture.
- `PROJECT_PLAN.md` — broader platform plan.
- `IDEA.md` — original SIT/UAT product notes.
