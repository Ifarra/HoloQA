"""The MCP surface, and the one invariant the whole design exists to protect.

If ``test_no_tool_accepts_a_verdict`` ever fails, HoloQA has become a logging
endpoint for an agent's claims — which is exactly what it was rebuilt to stop
being. Do not fix that test by narrowing it.
"""

from __future__ import annotations

import asyncio

import pytest

from holoqa import AGENT_ASSERTABLE, BLOCKED

EXPECTED_TOOLS = {
    "holoqa_plan_validate",
    "holoqa_run_start",
    "holoqa_run_status",
    "holoqa_observe",
    "holoqa_judge",
    "holoqa_block",
    "holoqa_note",
    "holoqa_kb_add",
    "holoqa_run_package",
    "holoqa_run_compare",
}

#: Parameter names through which a caller could smuggle in an outcome.
VERDICT_SHAPED = {"status", "verdict", "passed", "outcome", "result_status", "pass"}


@pytest.fixture(scope="module")
def tools():
    from holoqa.mcp import server

    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_exactly_the_documented_tools_are_registered(tools):
    assert set(tools) == EXPECTED_TOOLS


def test_no_tool_accepts_a_verdict(tools):
    """The agent must have no channel through which to assert an outcome."""
    offenders = {
        f"{name}.{parameter}"
        for name, tool in tools.items()
        for parameter in (tool.input_schema or {}).get("properties", {})
        if parameter.lower() in VERDICT_SHAPED
    }
    assert not offenders, (
        "a tool now accepts a verdict-shaped parameter: "
        + ", ".join(sorted(offenders))
    )


def test_only_blocked_is_agent_assertable():
    assert AGENT_ASSERTABLE == (BLOCKED,)


def test_every_tool_is_documented(tools):
    for name, tool in tools.items():
        assert (tool.description or "").strip(), f"{name} has no description"


def test_block_requires_a_substantive_reason(tools, tmp_path, monkeypatch):
    from holoqa import plan as plan_module
    from holoqa.mcp import holoqa_block
    from holoqa.run import GuardrailError, Run

    source = tmp_path / "plan.yaml"
    source.write_text(
        "meta: { app: t }\nsteps:\n  - id: X\n    title: t\n    expect:\n"
        "      - screenshot: required\n",
        encoding="utf-8",
    )
    run = Run.create(tmp_path / "run", plan_module.load(source))
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))

    with pytest.raises(GuardrailError, match="concrete cause"):
        holoqa_block("X", "failed")

    result = holoqa_block("X", "the VIEWER role does not exist in this environment")
    assert result["verdict"] == BLOCKED
    assert run.read()["steps"]["X"]["decided_by"] == "agent"
