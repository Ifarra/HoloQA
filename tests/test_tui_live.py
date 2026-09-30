"""TUI: readable transcript for every provider, and a step list that updates live.

Two failures from a real Claude run:
  - every log line rendered as "Provider event: assistant", because only Codex's
    wire format was understood;
  - the step list did not update until the agent stopped, because nothing read
    the authoritative run record during the run.
"""

from __future__ import annotations

import json

import pytest

from holoqa import plan as plan_module
from holoqa import run as run_module
from holoqa.tui import HoloQATui, TuiState

CLAUDE_ASSISTANT = (
    '{"type":"assistant","message":{"role":"assistant","content":['
    '{"type":"text","text":"I will capture the DOM for step A1."}]}}'
)
CLAUDE_TOOL_USE = (
    '{"type":"assistant","message":{"role":"assistant","content":['
    '{"type":"tool_use","id":"t1","name":"mcp__holoqa__holoqa_observe",'
    '"input":{"step_id":"A1","kind":"dom"}}]}}'
)
CLAUDE_TOOL_RESULT = (
    '{"type":"user","message":{"role":"user","content":['
    '{"type":"tool_result","tool_use_id":"t1","content":'
    '[{"type":"text","text":"captured 2 files"}]}]}}'
)
CLAUDE_SYSTEM = (
    '{"type":"system","subtype":"init","session_id":"s","model":"claude-sonnet-4",'
    '"tools":["Bash","Read"]}'
)
CLAUDE_RESULT = (
    '{"type":"result","subtype":"success","duration_ms":42000,'
    '"total_cost_usd":0.1234,"result":"Checklist complete."}'
)


# ------------------------------------------------------------ readable log

def test_a_claude_assistant_text_becomes_readable():
    rendered = HoloQATui._human_event("stdout", CLAUDE_ASSISTANT)
    assert rendered == "Agent: I will capture the DOM for step A1."
    assert "Provider event" not in rendered


def test_a_claude_tool_use_names_the_holoqa_operation_and_step():
    rendered = HoloQATui._human_event("stdout", CLAUDE_TOOL_USE)
    assert "Capturing dom" in rendered
    assert "A1" in rendered


def test_a_claude_tool_result_shows_its_text():
    rendered = HoloQATui._human_event("stdout", CLAUDE_TOOL_RESULT)
    assert rendered == "Result: captured 2 files"


def test_a_claude_system_init_reports_the_session():
    rendered = HoloQATui._human_event("stdout", CLAUDE_SYSTEM)
    assert "Session started" in rendered
    assert "claude-sonnet-4" in rendered


def test_a_claude_result_reports_duration_and_cost():
    rendered = HoloQATui._human_event("stdout", CLAUDE_RESULT)
    assert "42.0s" in rendered
    assert "$0.1234" in rendered
    assert "Checklist complete." in rendered


def test_a_claude_thinking_block_is_shown_as_thinking():
    line = (
        '{"type":"assistant","message":{"role":"assistant","content":['
        '{"type":"thinking","thinking":"The fixture may be missing."}]}}'
    )
    assert HoloQATui._human_event("stdout", line) == (
        "Agent is thinking: The fixture may be missing."
    )


def test_codex_rendering_is_unchanged():
    """The format that already worked must not regress."""
    line = (
        '{"type":"item.started","item":{"type":"mcp_tool_call",'
        '"tool":"holoqa_observe","arguments":{"step_id":"A1","kind":"dom"}}}'
    )
    assert HoloQATui._human_event("stdout", line) == "Capturing dom for step A1"
    assert HoloQATui._human_event("stdout", '{"type":"turn.started"}') == "Agent is thinking"


def test_an_unknown_event_still_falls_back_honestly():
    """A provider we do not know must not be described as if we did."""
    rendered = HoloQATui._human_event("stdout", '{"type":"brand.new.thing"}')
    assert rendered == "Provider event: brand.new.thing"


def test_non_json_output_is_passed_through():
    assert HoloQATui._human_event("stderr", "some warning") == "Provider: some warning"


# ------------------------------------------------------- live step updates

def _write_run(tmp_path, steps: dict) -> str:
    """A minimal run directory, as the wrapper would leave it."""
    run_dir = tmp_path / ".holoqa" / "runs" / "r1"
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    (run_dir / "out").mkdir(parents=True, exist_ok=True)
    plan_path = tmp_path / "plan.yaml"
    body = "meta:\n  app: tui\nsteps:\n" + "".join(
        f"  - id: {sid}\n    title: {sid} title\n    expect:\n      - screenshot: required\n"
        for sid in steps
    )
    plan_path.write_text(body, encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "r1", "plan_path": str(plan_path), "plan_sha256": "x",
        "app": "tui", "language": "en", "meta": {}, "steps": steps,
    }), encoding="utf-8")
    return str(run_dir)


@pytest.mark.anyio
async def test_a_recorded_verdict_reaches_the_step_list_during_the_run(tmp_path):
    """The bug: a finished step stayed PENDING until the agent stopped."""
    run_dir = _write_run(tmp_path, {
        "A1": {"verdict": "PASS", "observations": [], "assertions": []},
        "A2": {"verdict": None, "observations": [], "assertions": []},
    })
    state = TuiState(steps=[("A1", "A1 title", "PENDING"), ("A2", "A2 title", "PENDING")])
    app = HoloQATui(state=state, run_dir=run_dir)
    async with app.run_test() as pilot:
        app._sync_from_run()
        await pilot.pause()
        verdicts = {sid: v for sid, _t, v in app.state.steps}
        assert verdicts["A1"] == "PASS", "a judged step must update mid-run"
        assert verdicts["A2"] == "PENDING"


@pytest.mark.anyio
async def test_an_in_progress_step_is_not_overwritten_by_a_missing_verdict(tmp_path):
    run_dir = _write_run(tmp_path, {
        "A1": {"verdict": None, "observations": [], "assertions": []},
    })
    state = TuiState(steps=[("A1", "A1 title", "IN PROGRESS")])
    app = HoloQATui(state=state, run_dir=run_dir)
    async with app.run_test() as pilot:
        app._sync_from_run()
        await pilot.pause()
        assert app.state.steps[0][2] == "IN PROGRESS"


@pytest.mark.anyio
async def test_an_incomplete_run_never_reads_as_release(tmp_path):
    run_dir = _write_run(tmp_path, {
        "A1": {"verdict": "PASS", "observations": [], "assertions": []},
        "A2": {"verdict": None, "observations": [], "assertions": []},
    })
    state = TuiState(steps=[("A1", "A1 title", "PENDING"), ("A2", "A2 title", "PENDING")])
    app = HoloQATui(state=state, run_dir=run_dir)
    async with app.run_test() as pilot:
        app._sync_from_run()
        await pilot.pause()
        assert app.state.decision == "HOLD"


@pytest.mark.anyio
async def test_evidence_appears_during_the_run(tmp_path):
    run_dir = _write_run(tmp_path, {
        "A1": {
            "verdict": "PASS",
            "observations": [{"kind": "screenshot", "file": "a1.png", "bytes": 10}],
            "assertions": [{"kind": "screenshot", "ok": True, "detail": "captured"}],
        },
    })
    state = TuiState(steps=[("A1", "A1 title", "PENDING")])
    app = HoloQATui(state=state, run_dir=run_dir)
    async with app.run_test() as pilot:
        app._sync_from_run()
        await pilot.pause()
        assert any("a1.png" in item for item in app.state.evidence)
        assert app.state.assertions["A1"][0].detail == "captured"


@pytest.mark.anyio
async def test_a_missing_run_directory_is_not_an_error(tmp_path):
    """The dashboard must survive the window before the wrapper creates the run."""
    state = TuiState(steps=[("A1", "A1 title", "PENDING")])
    app = HoloQATui(state=state, run_dir=str(tmp_path / "not-yet"))
    async with app.run_test() as pilot:
        app._sync_from_run()
        await pilot.pause()
        assert app.state.steps[0][2] == "PENDING"


@pytest.mark.anyio
async def test_demo_mode_does_not_read_a_run(tmp_path):
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        before = list(app.state.steps)
        app._sync_from_run()
        await pilot.pause()
        assert app.state.steps == before
