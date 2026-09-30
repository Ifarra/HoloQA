"""Tier 4: the wrapper and TUI behaviours that wasted a real run.

Three concrete failures from the reviewer's session:
  - `unattended` for Claude *denied* the login it needed, and the refusal was
    recorded as a BLOCKED that looked like an application defect;
  - the agent stopped after one stage and nothing continued it;
  - the TUI could not be opened on a live run at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from holoqa import agent as agent_module
from holoqa import plan as plan_module
from holoqa.run import Run

PLAN = """
meta:
  app: wrapper
steps:
  - id: A1
    title: one step
    expect:
      - screenshot: required
  - id: A2
    title: another step
    expect:
      - screenshot: required
"""


@pytest.fixture()
def plan(tmp_path) -> plan_module.Plan:
    path = tmp_path / "plan.yaml"
    path.write_text(PLAN, encoding="utf-8")
    return plan_module.load(path)


def _request(provider="claude", mode="unattended"):
    return agent_module.AgentRunRequest(provider=provider, cwd=Path("."), mode=mode)


# ------------------------------------------------------- unattended semantics

def test_claude_unattended_allows_the_tools_the_workflow_needs(tmp_path, plan):
    """`--permission-prompts none` alone means *deny*, which is not unattended."""
    run = Run.create(tmp_path / "run", plan)
    spec = agent_module.build_launch(
        _request("claude", "unattended"), run, "prompt", tmp_path
    )
    joined = " ".join(spec.command)
    assert "--allowedTools" in joined
    assert "mcp__holoqa__*" in joined
    assert "Bash(agent-browser:*)" in joined


def test_claude_supervised_does_not_grant_blanket_tools(tmp_path, plan):
    run = Run.create(tmp_path / "run", plan)
    spec = agent_module.build_launch(
        _request("claude", "supervised"), run, "prompt", tmp_path
    )
    assert "--allowedTools" not in " ".join(spec.command)


def test_every_provider_states_what_unattended_means():
    """Choosing a mode should not require reading the source to know its effect."""
    for provider in agent_module.PROVIDERS:
        info = agent_module.provider_info(provider)
        assert info["unattended"], provider
    # The meanings are genuinely different, which is why they are documented.
    meanings = {agent_module.UNATTENDED_MEANING[name] for name in agent_module.PROVIDERS}
    assert len(meanings) == len(agent_module.PROVIDERS)


def test_codex_unattended_still_avoids_the_bypass_flag(tmp_path, plan):
    run = Run.create(tmp_path / "run", plan)
    spec = agent_module.build_launch(
        _request("codex", "unattended"), run, "prompt", tmp_path
    )
    joined = " ".join(spec.command)
    assert "--approve-for-me" in joined
    assert "--dangerously-bypass" not in joined
    assert "--yolo" not in joined


# ------------------------------------------------------------------- until-done

def test_until_done_stops_when_no_steps_remain(monkeypatch, plan, tmp_path):
    run = Run.create(tmp_path / "run", plan)

    def fake_launch(request, run, *, resume=False, timeout_s=0):
        # Simulate the agent judging both steps in one round.
        for step_id in ("A1", "A2"):
            shot = run.evidence_dir / f"{step_id}.png"
            shot.write_bytes(b"\x89PNG\r\n\x1a\n" + step_id.encode())
            run.attach(step_id, kind="screenshot", path=shot)
            run.set_verdict(step_id, "PASS", assertions=[], by="holoqa")
        return {"agent": {"state": "completed"}, "holoqa": _status(run)}

    monkeypatch.setattr(agent_module, "launch", fake_launch)
    result = agent_module.run_until_done(_request(), run, max_rounds=5)

    assert result["until_done"] is True
    assert len(result["rounds"]) == 1


def test_until_done_stops_when_a_round_makes_no_progress(monkeypatch, plan, tmp_path):
    """Another identical round cannot help, so it must not be spent."""
    run = Run.create(tmp_path / "run", plan)
    calls = []

    def fake_launch(request, run, *, resume=False, timeout_s=0):
        calls.append(resume)
        return {"agent": {"state": "completed"}, "holoqa": _status(run)}

    monkeypatch.setattr(agent_module, "launch", fake_launch)
    result = agent_module.run_until_done(_request(), run, max_rounds=5)

    assert result["until_done"] is False
    # The first round judged nothing, so a second would be identical: stop.
    assert len(calls) == 2
    assert calls[0] is False and calls[1] is True


def test_until_done_resumes_when_progress_is_made(monkeypatch, plan, tmp_path):
    run = Run.create(tmp_path / "run", plan)
    rounds = []

    def fake_launch(request, run, *, resume=False, timeout_s=0):
        rounds.append(resume)
        step_id = "A1" if len(rounds) == 1 else "A2"
        shot = run.evidence_dir / f"{step_id}.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + step_id.encode())
        run.attach(step_id, kind="screenshot", path=shot)
        run.set_verdict(step_id, "PASS", assertions=[], by="holoqa")
        return {"agent": {"state": "completed"}, "holoqa": _status(run)}

    monkeypatch.setattr(agent_module, "launch", fake_launch)
    result = agent_module.run_until_done(_request(), run, max_rounds=5)

    assert result["until_done"] is True
    assert len(rounds) == 2
    # The second round must know it is a continuation.
    assert rounds[1] is True


def test_until_done_respects_max_rounds(monkeypatch, tmp_path):
    """With more steps than rounds, the cap is what stops it."""
    body = "meta:\n  app: many\nsteps:\n" + "".join(
        f"  - id: S{i}\n    title: step {i}\n    expect:\n      - screenshot: required\n"
        for i in range(1, 6)
    )
    path = tmp_path / "many.plan.yaml"
    path.write_text(body, encoding="utf-8")
    plan = plan_module.load(path)
    run = Run.create(tmp_path / "many-run", plan)
    count = []

    def fake_launch(request, run, *, resume=False, timeout_s=0):
        count.append(1)
        step_id = f"S{len(count)}"
        shot = run.evidence_dir / f"{step_id}.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + step_id.encode())
        run.attach(step_id, kind="screenshot", path=shot)
        run.set_verdict(step_id, "PASS", assertions=[], by="holoqa")
        return {"agent": {"state": "completed"}, "holoqa": _status(run)}

    monkeypatch.setattr(agent_module, "launch", fake_launch)
    result = agent_module.run_until_done(_request(), run, max_rounds=3)

    assert len(count) == 3
    assert result["until_done"] is False
    assert len(result["rounds"]) == 3


def _status(run):
    plan = plan_module.load(run.read()["plan_path"])
    return run.status(plan)


# --------------------------------------------------------------------- timeout

def test_launch_records_the_timeout_it_was_given(monkeypatch, plan, tmp_path):
    run = Run.create(tmp_path / "run", plan)

    def fake_stream(spec, request, run, *, deadline=None):
        assert deadline is not None, "a timeout must reach the stream loop"
        return 0

    monkeypatch.setattr(agent_module, "_stream_process", fake_stream)
    monkeypatch.setattr(agent_module, "provider_binary", lambda p: "true")
    result = agent_module.launch(_request("codex", "supervised"), run, timeout_s=30)

    assert result["agent"]["timeout_s"] == 30


def test_a_timeout_is_reported_as_timed_out_not_interrupted(monkeypatch, plan, tmp_path):
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setattr(agent_module, "_stream_process", lambda *a, **k: 130)
    monkeypatch.setattr(agent_module, "provider_binary", lambda p: "true")

    result = agent_module.launch(_request("codex", "supervised"), run, timeout_s=5)

    # A hang must not be indistinguishable from a user pressing cancel.
    assert result["agent"]["state"] == "timed_out"


def test_a_cancel_without_a_timeout_is_still_interrupted(monkeypatch, plan, tmp_path):
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setattr(agent_module, "_stream_process", lambda *a, **k: 130)
    monkeypatch.setattr(agent_module, "provider_binary", lambda p: "true")

    result = agent_module.launch(_request("codex", "supervised"), run)

    assert result["agent"]["state"] == "interrupted"
