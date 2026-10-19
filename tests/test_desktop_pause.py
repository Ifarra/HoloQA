"""Pause/resume preserves one owning engine without trusting edited plans."""
from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from holoqa import desktop, observe
from holoqa.desktop import DesktopError, Engine
from holoqa.run import forget_ledger
from tests.test_desktop import PLAN
from tests.test_desktop_codex import PAUSE_PEER


def wait_for(predicate, message, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), message


@pytest.fixture
def session(tmp_path, monkeypatch):
    project = tmp_path / "paused project é with spaces"
    project.mkdir()
    (project / "holoqa.plan.yaml").write_text(PLAN, encoding="utf-8")
    peer = tmp_path / "pause app server.py"
    peer.write_text(PAUSE_PEER, encoding="utf-8")
    session_type = desktop.CodexSession
    monkeypatch.setattr(desktop, "CodexSession", lambda cwd, emit, tool=None:
        session_type(cwd, emit, tool, command=[sys.executable, "-u", str(peer)]))
    original_which = desktop.shutil.which
    monkeypatch.setattr(desktop.shutil, "which", lambda name:
        "/fixture/agent-browser" if name == "agent-browser" else original_which(name))
    events, cleanups = [], []
    monkeypatch.setattr(desktop.browser, "cleanup", lambda run: cleanups.append(run.dir) or [])
    engine = Engine(tmp_path / "state", lambda event, data: events.append((event, data)))
    engine.open_project(str(project))
    summary = engine.load_plan("holoqa.plan.yaml")[1]
    token = engine.review(summary["path"], summary["sha256"])["token"]
    engine.start(token)
    wait_for(lambda: any(event == "approval.request" for event, _ in events), "Missing initial approval")
    try:
        yield engine, events, cleanups
    finally:
        engine.stop()
        if engine.worker:
            engine.worker.join(timeout=5)
            assert not engine.worker.is_alive(), "Provider watcher leaked after teardown"
        if engine.run:
            forget_ledger(engine.run.dir)


def confirm_pause(engine):
    engine.dispatch("run.pause", {})
    assert engine.lifecycle == "pausing", "Interrupt acknowledgement must not claim a paused run"
    engine.provider.send({"method": "fixture/complete"})
    wait_for(lambda: engine.lifecycle == "paused", "Interrupted turn was not recognized as paused")


def test_pause_resume_preserves_pinned_plan_ledger_process_and_thread(session, monkeypatch):
    engine, events, cleanups = session
    provider, run = engine.provider, engine.run
    pid, thread = provider.process.pid, provider.thread_id

    def dom(destination, *, session):
        data = {"url": "http://localhost:3000", "text": "Ready", "text_length": 5, "truncated": False}
        destination.write_text(json.dumps(data), encoding="utf-8")
        return data

    monkeypatch.setattr(observe, "dom", dom)
    capture = engine.tool("holoqa_observe", {"step_id": "A1", "kind": "dom"})
    engine.tool("holoqa_judge", {"step_id": "A1"})
    original_pin = run.plan_pin.read_bytes()
    stale_key = next(data["id"] for event, data in events if event == "approval.request")

    confirm_pause(engine)
    assert cleanups == [], "Pausing must preserve the existing browser session"
    assert engine.snapshot()["status"]["decision"] == "HOLD"
    assert engine.snapshot()["status"]["integrity"] == "verified"
    assert engine.snapshot()["status"]["counts"]["PASS"] == 1
    assert any(event == "approvals.cleared" for event, _ in events)
    with pytest.raises(DesktopError, match="no longer running"):
        engine.dispatch("approval.respond", {"id": stale_key, "decision": "accept"})

    for method, params in [
        ("project.open", {"path": str(engine.root.parent)}),
        ("project.recent_open", {"path": str(engine.root)}),
        ("plan.create", {"app": "other", "url": "http://localhost:4000"}),
        ("run.start", {"token": "old"}),
        ("run.export", {}),
        ("provider.check", {}),
    ]:
        with pytest.raises(DesktopError, match="active run"):
            engine.dispatch(method, params)
    with pytest.raises(DesktopError, match="No active QA run"):
        engine.tool("holoqa_run_status", {})

    # Editing the authoring file does not replace this run's approved pin.
    (engine.root / "holoqa.plan.yaml").write_text(PLAN.replace("Ready", "Changed"), encoding="utf-8")
    engine.dispatch("run.resume", {})
    assert engine.lifecycle == "running"
    assert engine.run is run and engine.provider is provider
    assert provider.process.pid == pid and provider.thread_id == thread
    assert provider.turn_id == "turn-2"
    assert run.plan_pin.read_bytes() == original_pin
    assert engine.snapshot()["status"]["integrity"] == "verified"
    assert "Ready" in engine.artifact(engine.snapshot()["path"], capture["file"])["text"]
    provider.send({"method": "fixture/complete"})
    wait_for(lambda: engine.lifecycle == "completed", "Resumed provider did not complete")
    assert engine.snapshot()["status"]["decision"] == "RELEASE"
    assert len(engine.history()) == 1
    assert cleanups == [run.dir]


def test_pause_waits_for_inflight_tool_before_confirming(session, monkeypatch):
    engine, _, cleanups = session
    started, release = threading.Event(), threading.Event()
    errors = []

    def dom(destination, *, session):
        started.set()
        assert release.wait(5), "Test did not release the in-flight capture"
        data = {"url": "http://localhost:3000", "text": "Ready", "text_length": 5, "truncated": False}
        destination.write_text(json.dumps(data), encoding="utf-8")
        return data

    def capture():
        try:
            engine.tool("holoqa_observe", {"step_id": "A1", "kind": "dom"})
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(observe, "dom", dom)
    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    try:
        assert started.wait(3)
        engine.pause()
        engine.provider.send({"method": "fixture/complete"})
        assert engine.provider.finished.wait(3)
        assert engine.lifecycle == "pausing"
    finally:
        release.set()
        worker.join(timeout=5)
    wait_for(lambda: engine.lifecycle == "paused", "Capture boundary did not allow pause")
    assert not errors
    assert cleanups == []
    assert engine.snapshot()["steps"][0]["observations"], "The completed capture was lost"


def test_stop_from_paused_terminates_provider_and_cleans_browser(session):
    engine, _, cleanups = session
    provider, run = engine.provider, engine.run
    confirm_pause(engine)
    engine.stop()
    assert engine.lifecycle == "interrupted"
    assert engine.provider is None
    assert provider.process.poll() is not None
    assert cleanups == [run.dir]
    assert engine.snapshot()["status"]["decision"] == "HOLD"
    with pytest.raises(DesktopError):
        engine.resume()


def test_unconfirmed_pause_times_out_and_stops_fail_closed(session):
    engine, _, cleanups = session
    provider = engine.provider
    engine.pause()
    engine.pause_deadline = time.monotonic() - 1
    wait_for(lambda: engine.lifecycle == "interrupted", "Unconfirmed pause did not stop")
    assert provider.process.poll() is not None
    assert engine.provider is None and len(cleanups) == 1
    assert engine.snapshot()["status"]["decision"] == "HOLD"
    with pytest.raises(DesktopError):
        engine.resume()


def test_disconnect_while_paused_cannot_resume_or_release(session):
    engine, _, cleanups = session
    provider = engine.provider
    confirm_pause(engine)
    provider.process.kill()
    wait_for(lambda: engine.lifecycle == "failed", "Disconnected paused session was not failed")
    assert engine.provider is None and len(cleanups) == 1
    assert engine.snapshot()["status"]["decision"] == "HOLD"
    with pytest.raises(DesktopError):
        engine.resume()


def test_watcher_rechecks_old_finished_flag_after_resume_acquires_lock(session, monkeypatch):
    engine, _, _ = session
    provider = engine.provider
    confirm_pause(engine)
    observed_old_finished = threading.Event()
    original_wait, original_resume = provider.finished.wait, provider.resume

    def observe_wait(timeout=None):
        result = original_wait(timeout)
        if result and engine.lifecycle == "running":
            observed_old_finished.set()
        return result

    def delayed_resume(prompt):
        # Engine is now running but holds its lock, and the old turn's flag is
        # still set. Force the watcher to observe it before starting a new turn.
        engine.state_changed.set()
        assert observed_old_finished.wait(3), "Watcher did not see the old completion during resume"
        return original_resume(prompt)

    monkeypatch.setattr(provider.finished, "wait", observe_wait)
    monkeypatch.setattr(provider, "resume", delayed_resume)
    engine.resume()
    assert engine.lifecycle == "running"
    assert engine.provider is provider
    assert not provider.finished.is_set()
    provider.send({"method": "fixture/complete"})
    wait_for(lambda: engine.lifecycle == "completed", "Resumed turn failed after stale completion race")


def test_natural_completion_winning_pause_race_finalizes_normally(session, monkeypatch):
    engine, _, cleanups = session
    provider = engine.provider
    original_interrupt = provider.interrupt

    def complete_then_interrupt():
        provider.handle({"method": "turn/completed", "params": {
            "threadId": provider.thread_id, "turn": {"id": provider.turn_id, "status": "completed"}}})
        return original_interrupt()

    monkeypatch.setattr(provider, "interrupt", complete_then_interrupt)
    engine.pause()
    wait_for(lambda: engine.lifecycle == "completed", "Natural completion was downgraded by a Pause click")
    assert engine.provider is None and len(cleanups) == 1
