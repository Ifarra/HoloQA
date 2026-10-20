from __future__ import annotations

import threading
from pathlib import Path

from holoqa import agent
from holoqa.desktop_claude import ClaudeSession


def test_claude_session_pauses_and_resumes_the_same_provider_session(tmp_path, monkeypatch):
    class FakeRun:
        dir = tmp_path / "run"

    first_started = threading.Event()
    resumed_started = threading.Event()
    complete_resume = threading.Event()
    seen_commands = []

    def build(request, run, prompt, directory):
        return agent.LaunchSpec(["claude", "-p", prompt], {}, directory / "config.json")

    def stream(spec, request, run):
        seen_commands.append(spec.command)
        if "--resume" in spec.command:
            resumed_started.set()
            assert complete_resume.wait(2)
            return 0
        first_started.set()
        assert request.cancel_event.wait(2)
        return agent.StreamExit(130, "interrupted")

    monkeypatch.setattr(agent, "build_launch", build)
    monkeypatch.setattr(agent, "_stream_process", stream)
    session = ClaudeSession(tmp_path, lambda *_: None, FakeRun())
    try:
        session.start("initial prompt")
        assert first_started.wait(2)
        assert session.interrupt() is True
        assert session.finished.wait(2)
        assert session.turn_status == "interrupted"
        assert not session.disconnected

        session.resume("continue the run")
        assert resumed_started.wait(2)
        resume_command = seen_commands[-1]
        assert resume_command[resume_command.index("--resume") + 1] == session.session_id
        assert resume_command[-1] == "continue the run"
        complete_resume.set()
        assert session.finished.wait(2)
        assert session.turn_status == "completed"
        assert not session.disconnected
    finally:
        complete_resume.set()
        session.close()
