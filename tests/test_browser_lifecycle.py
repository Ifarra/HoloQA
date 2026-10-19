"""Browser ownership and memory backpressure regressions, without Chromium."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from holoqa import agent, browser, observe, plan
from holoqa.run import Run


@pytest.fixture
def run(tmp_path):
    source = tmp_path / 'plan.yaml'
    source.write_text('''meta:
  app: lifecycle
  actors:
    admin: {}
    anon: {}
steps:
  - id: A
    title: admin
    actor: admin
    expect: [{screenshot: required}]
  - id: B
    title: anonymous
    actor: anon
    expect: [{screenshot: required}]
''')
    return Run.create(tmp_path / 'run', plan.load(source))


def test_sessions_are_stable_distinct_and_scoped(run, tmp_path):
    names = browser.sessions(run)
    assert names == browser.sessions(run)
    assert names['admin'] != names['anon']
    assert names['admin'] != browser.session_name(tmp_path / 'other', 'admin')


def test_managed_capture_uses_physical_session_without_changing_actor(run, monkeypatch):
    monkeypatch.setenv('HOLOQA_MANAGED_BROWSER', '1')
    monkeypatch.setenv('HOLOQA_RUN_DIR', str(run.dir))
    monkeypatch.setattr(observe, '_binary', lambda: 'agent-browser')
    seen = []
    def invoke(command, **kwargs):
        seen.append(kwargs['env']['AGENT_BROWSER_SESSION'])
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(observe.subprocess, 'run', invoke)
    observe.run_cli(['eval', '1'], session='admin')
    observe.run_cli(['close'], session=browser.sessions(run)['admin'])
    assert seen == [browser.sessions(run)['admin']] * 2


def test_standalone_session_names_are_unchanged(monkeypatch):
    monkeypatch.delenv('HOLOQA_MANAGED_BROWSER', raising=False)
    monkeypatch.setattr(observe, '_binary', lambda: 'agent-browser')
    def invoke(command, **kwargs):
        assert kwargs['env']['AGENT_BROWSER_SESSION'] == 'admin'
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(observe.subprocess, 'run', invoke)
    observe.run_cli(['eval', '1'], session='admin')


def test_cleanup_is_scoped_and_attempts_every_actor_on_failure(run, monkeypatch):
    seen = []
    def close(args, *, session, timeout):
        assert args == ['close']
        assert timeout == 10
        seen.append(session)
        raise observe.CaptureError('fixture failure')
    monkeypatch.setattr(observe, 'run_cli', close)
    errors = browser.cleanup(run)
    assert set(seen) == set(browser.sessions(run).values())
    assert len(errors) == 2


def test_launch_cleans_up_when_provider_raises(run, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, 'provider_binary', lambda _: sys.executable)
    monkeypatch.setattr(agent, 'provider_info', lambda _: {'version': 'fixture'})
    monkeypatch.setattr(agent, '_stream_process', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('failed')))
    cleaned = []
    monkeypatch.setattr(browser, 'cleanup', lambda run: cleaned.append(run.dir) or [])
    with pytest.raises(RuntimeError, match='failed'):
        agent.launch(agent.AgentRunRequest('codex', tmp_path), run)
    assert cleaned == [run.dir]


def test_rounds_share_sessions_and_cleanup_only_at_end(run, tmp_path, monkeypatch):
    cleaned = []
    monkeypatch.setattr(browser, 'cleanup', lambda run: cleaned.append(run.dir) or [])
    monkeypatch.setattr(agent, 'provider_binary', lambda _: sys.executable)
    monkeypatch.setattr(agent, 'provider_info', lambda _: {'version': 'fixture'})
    calls = []
    def stream(*args, **kwargs):
        assert not cleaned
        calls.append(1)
        return 0
    monkeypatch.setattr(agent, '_stream_process', stream)
    statuses = iter([
        {'pending': ['B'], 'total': 2},
        {'pending': [], 'total': 2},
    ])
    monkeypatch.setattr(agent, '_status', lambda _: next(statuses))
    result = agent.run_until_done(agent.AgentRunRequest('codex', tmp_path), run)
    assert len(calls) == 2
    assert cleaned == [run.dir]
    assert result['agent']['browser_cleanup_errors'] == []


def test_callback_failure_terminates_provider(run, tmp_path):
    pid_path = tmp_path / 'pid'
    command = [sys.executable, '-u', '-c',
               f'import os,time; open({str(pid_path)!r}, "w").write(str(os.getpid())); print("event"); time.sleep(60)']
    def callback(*args):
        raise RuntimeError('consumer failed')
    request = agent.AgentRunRequest('codex', tmp_path, output_callback=callback)
    spec = agent.LaunchSpec(command, dict(os.environ), tmp_path / 'config')
    with pytest.raises(RuntimeError, match='consumer failed'):
        agent._stream_process(spec, request, run)
    if os.name != 'nt':
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_path.read_text()), 0)


def test_backpressure_preserves_all_output(run, tmp_path):
    received = []
    spec = agent.LaunchSpec(
        [sys.executable, '-c', 'for i in range(3000): print(i)'],
        dict(os.environ), tmp_path / 'config',
    )
    def consume(channel, line):
        received.append(line)
        if len(received) % 200 == 0:
            time.sleep(0.01)
    request = agent.AgentRunRequest('codex', tmp_path, output_callback=consume)
    assert agent._stream_process(spec, request, run) == 0
    assert received == [str(i) for i in range(3000)]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signal escalation')
def test_timeout_kills_provider_that_ignores_termination(run, tmp_path):
    spec = agent.LaunchSpec(
        [sys.executable, '-u', '-c',
         'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready"); time.sleep(60)'],
        dict(os.environ), tmp_path / 'config',
    )
    request = agent.AgentRunRequest('codex', tmp_path, output_callback=lambda *a: None)
    started = time.monotonic()
    assert agent._stream_process(spec, request, run, deadline=started + 0.5) == 130
    assert time.monotonic() - started < 10
