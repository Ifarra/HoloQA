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


def test_plan_validate_with_no_path_returns_the_format_reference():
    """A fresh agent must be able to learn the schema without guessing."""
    from holoqa.mcp import holoqa_plan_validate

    reference = holoqa_plan_validate()
    assert reference["status"] == "reference"
    assert set(reference["assertions"]) == {
        "url_contains", "url_matches", "text_contains", "text_not_contains",
        "api", "json", "screenshot", "changed", "capture",
    }
    assert "quoting" in reference
    assert "meta:" in reference["template"]


def test_the_shipped_template_actually_validates(tmp_path):
    """A scaffold that does not pass its own linter is a broken first run."""
    from holoqa import plan as plan_module

    path = tmp_path / "holoqa.plan.yaml"
    path.write_text(plan_module.scaffold("demo", "https://staging.test"), encoding="utf-8")
    assert plan_module.lint(path)["status"] == "ok"


def test_commented_out_sections_are_not_a_validation_error(tmp_path):
    """Users comment out known_behaviors first; YAML makes that null, not []."""
    from holoqa import plan as plan_module

    path = tmp_path / "p.yaml"
    path.write_text(
        "meta: { app: t }\nknown_behaviors:\n  # - id: KB-001\nstages:\n"
        "steps:\n  - id: A\n    title: t\n    expect:\n      - screenshot: required\n",
        encoding="utf-8",
    )
    plan = plan_module.load(path)
    assert plan.known_behaviors == []
    assert plan.stages == []
