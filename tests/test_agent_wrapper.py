"""Offline contract tests for the provider-neutral coding-agent wrapper."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from holoqa import agent as agent_module
from holoqa import plan as plan_module
from holoqa.cli import main
from holoqa.run import Run


PLAN = """meta: { app: wrapper, base_url: http://example.test }
steps:
  - id: A1
    title: page loads
    expect:
      - screenshot: required
"""


def _run(tmp_path: Path) -> Run:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(PLAN, encoding="utf-8")
    return Run.create(tmp_path / "run", plan_module.load(plan_path))


def test_all_adapters_make_isolated_mcp_configuration(tmp_path, monkeypatch):
    run = _run(tmp_path)
    monkeypatch.setattr(agent_module, "provider_binary", lambda provider: f"/{provider}")
    for provider in agent_module.PROVIDERS:
        temp = tmp_path / provider
        temp.mkdir()
        request = agent_module.AgentRunRequest(provider=provider, cwd=tmp_path)
        spec = agent_module.build_launch(request, run, "test prompt", temp)
        assert f"/{provider}" in spec.command
        assert spec.env["HOLOQA_RUN_DIR"] == str(run.dir)
        assert spec.config_path.is_file()
        rendered = spec.config_path.read_text(encoding="utf-8") + " ".join(spec.command)
        assert "holoqa" in rendered
        if provider == "codex":
            assert "--ignore-user-config" in spec.command
            assert any("default_tools_approval_mode=\"approve\"" in item for item in spec.command)
    unattended_dir = tmp_path / "codex-unattended"
    unattended_dir.mkdir()
    unattended = agent_module.build_launch(
        agent_module.AgentRunRequest(provider="codex", cwd=tmp_path, mode="unattended"),
        run, "test prompt", unattended_dir,
    )
    assert "--approve-for-me" in unattended.command
    assert "--ignore-rules" in unattended.command
    assert "--sandbox" not in unattended.command


def test_launch_records_manifest_and_holoqa_remains_authoritative(tmp_path, monkeypatch, capsys):
    run = _run(tmp_path)
    request = agent_module.AgentRunRequest(provider="codex", cwd=tmp_path)

    def fake_build(request, run, prompt, directory):
        config = directory / "config"
        config.write_text("safe", encoding="utf-8")
        return agent_module.LaunchSpec(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write('progress ✓\\n{\\\"type\\\": \\\"turn.completed\\\"}\\n'.encode())"],
            {**__import__("os").environ, "HOLOQA_RUN_DIR": str(run.dir)}, config,
        )

    monkeypatch.setattr(agent_module, "build_launch", fake_build)
    result = agent_module.launch(request, run)
    assert result["agent"]["state"] == "completed"
    assert result["agent"]["exit_code"] == 0
    assert result["holoqa"]["decision"] == "HOLD"
    manifest = json.loads((run.out_dir / "agent.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "codex"
    assert manifest["plan_sha256"] == run.read()["plan_sha256"]
    assert (run.out_dir / manifest["prompt_file"]).is_file()
    output = capsys.readouterr().out
    assert "turn.completed" in output
    assert "progress ✓" in output


def test_dry_run_creates_no_run_or_provider_process(tmp_path, capsys):
    plan = tmp_path / "plan.yaml"
    plan.write_text(PLAN, encoding="utf-8")
    code = main(["agent", "run", str(plan), "--provider", "codex", "--cwd", str(tmp_path), "--dry-run"])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["note"].startswith("dry run")
    assert not (tmp_path / ".holoqa").exists()


def test_agent_status_includes_wrapper_manifest(tmp_path, monkeypatch):
    run = _run(tmp_path)
    (run.out_dir / "agent.json").write_text(json.dumps({"provider": "claude", "state": "completed"}), encoding="utf-8")
    result = agent_module.status(run.dir)
    assert result["agent"]["provider"] == "claude"
    assert result["holoqa"]["run_id"] == "run"


def test_agent_output_with_unicode_does_not_break_the_wrapper(tmp_path, monkeypatch, capsys):
    run = _run(tmp_path)
    request = agent_module.AgentRunRequest(provider="codex", cwd=tmp_path)

    def fake_build(request, run, prompt, directory):
        config = directory / "config"
        config.write_text("safe", encoding="utf-8")
        return agent_module.LaunchSpec(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write('event → complete\\n'.encode())"],
            {**os.environ, "HOLOQA_RUN_DIR": str(run.dir)}, config,
        )

    monkeypatch.setattr(agent_module, "build_launch", fake_build)
    assert agent_module.launch(request, run)["agent"]["state"] == "completed"
    assert "event → complete" in capsys.readouterr().out


def test_provider_stdin_is_closed_after_the_prompt(tmp_path, monkeypatch):
    run = _run(tmp_path)
    request = agent_module.AgentRunRequest(provider="codex", cwd=tmp_path)

    def fake_build(request, run, prompt, directory):
        config = directory / "config"
        config.write_text("safe", encoding="utf-8")
        return agent_module.LaunchSpec(
            [sys.executable, "-c", "import sys; sys.stdin.read(); print('turn complete')"],
            {**os.environ, "HOLOQA_RUN_DIR": str(run.dir)}, config,
        )

    monkeypatch.setattr(agent_module, "build_launch", fake_build)
    result = agent_module.launch(request, run)
    assert result["agent"]["state"] == "completed"


@pytest.mark.parametrize("provider_name", agent_module.PROVIDERS)
def test_wrapper_controls_a_simulated_coder_through_real_mcp(tmp_path, monkeypatch, provider_name):
    """Full safe flow: wrapper -> provider -> HoloQA MCP -> capture -> verdict.

    The fake provider is a deterministic MCP client, not a model. Its browser
    binary writes known fixture bytes, so this proves our orchestration without
    spending tokens, touching a real application, or opening the network.
    """
    plan = tmp_path / "plan.yaml"
    plan.write_text("""meta: { app: simulated, base_url: http://fixture.test }
steps:
  - id: A1
    title: Fixture page is ready
    expect:
      - url_contains: fixture.test
      - text_contains: Fixture ready
      - screenshot: required
""", encoding="utf-8")
    browser = tmp_path / "agent-browser.cmd"
    browser.write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake_browser.py" %*\r\n', encoding="utf-8")
    (tmp_path / "fake_browser.py").write_text("""import json, sys
from pathlib import Path
command = sys.argv[1]
if command == 'screenshot':
    Path(sys.argv[2]).write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture-image')
elif command == 'eval':
    print(json.dumps({'url': 'http://fixture.test/ready', 'title': 'fixture', 'text': 'Fixture ready', 'text_length': 13, 'truncated': False}))
else:
    raise SystemExit('unexpected browser command: ' + command)
""", encoding="utf-8")
    provider = tmp_path / f"{provider_name}.cmd"
    provider.write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake_coder.py" %*\r\n', encoding="utf-8")
    (tmp_path / "fake_coder.py").write_text("""import asyncio, json, sys
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

def setting(name):
    prefix = name + '='
    for argument in sys.argv[1:]:
        if argument.startswith(prefix):
            return json.loads(argument[len(prefix):])
    raise RuntimeError('missing ' + name + ': ' + repr(sys.argv))

def server_config():
    if '--mcp-config' in sys.argv:
        path = sys.argv[sys.argv.index('--mcp-config') + 1]
        return json.load(open(path, encoding='utf-8'))['mcpServers']['holoqa']
    if 'OPENCODE_CONFIG' in __import__('os').environ:
        return json.load(open(__import__('os').environ['OPENCODE_CONFIG'], encoding='utf-8'))['mcp']['servers']['holoqa']
    return {
        'command': setting('mcp_servers.holoqa.command'),
        'args': setting('mcp_servers.holoqa.args'),
        'env': {'HOLOQA_RUN_DIR': setting('mcp_servers.holoqa.env.HOLOQA_RUN_DIR')},
    }

async def main():
    config = server_config()
    command = config['command']
    args = config.get('args', [])
    if isinstance(command, list):
        command, args = command[0], command[1:]
    run_dir = (config.get('env') or config.get('environment'))['HOLOQA_RUN_DIR']
    params = StdioServerParameters(command=command, args=args, env={'HOLOQA_RUN_DIR': run_dir})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool('holoqa_observe', {'step_id': 'A1', 'kind': 'dom'})
            await session.call_tool('holoqa_observe', {'step_id': 'A1', 'kind': 'screenshot'})
            judged = await session.call_tool('holoqa_judge', {'step_id': 'A1'})
            if judged.is_error:
                raise RuntimeError(str(judged.content))

asyncio.run(main())
print(json.dumps({'type': 'turn.completed'}))
""", encoding="utf-8")

    monkeypatch.setattr(agent_module, "provider_binary", lambda _: str(provider))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    run_dir = tmp_path / "run"
    code = main([
        "agent", "run", str(plan), "--provider", provider_name, "--cwd", str(tmp_path),
        "--run-dir", str(run_dir), "--package",
    ])

    assert code == 0
    result = agent_module.status(run_dir)
    assert result["agent"]["provider"] == provider_name
    assert result["agent"]["state"] == "completed"
    assert result["holoqa"]["counts"]["PASS"] == 1
    assert result["holoqa"]["decision"] == "RELEASE"
    assert (run_dir / "out" / "run.zip").is_file()
