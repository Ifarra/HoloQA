"""Desktop boundaries: review bytes, project paths, engine authority and stdio."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from pydantic import ValidationError

from holoqa import desktop, mcp, observe, workspace
from holoqa.desktop import DesktopError, Engine
from holoqa.run import forget_ledger

PLAN = """meta:
  app: desktop-test
  base_url: http://localhost:3000
stages:
  - id: A
    title: Discovery
steps:
  - id: A1
    stage: A
    title: Page is ready
    expect:
      - text_contains: Ready
"""


@pytest.fixture
def engine(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    (project / "holoqa.plan.yaml").write_text(PLAN, encoding="utf-8")
    engine = Engine(tmp_path / "state")
    engine.open_project(str(project))
    yield engine
    engine.stop()
    if engine.run:
        forget_ledger(engine.run.dir)


def test_discovery_is_read_only_bounded_and_does_not_execute_scripts(engine):
    root = engine.root
    (root / "package.json").write_text(json.dumps({"dependencies":{"next":"x"},"scripts":{"postinstall":"touch dangerous"}}))
    hidden = root / "node_modules";hidden.mkdir()
    (hidden / "secret.plan.yaml").write_text(PLAN)
    before = {p.relative_to(root) for p in root.rglob("*")}
    result = engine.project()
    assert result["framework"] == "Next.js"
    assert result["plans"] == ["holoqa.plan.yaml"]
    assert before == {p.relative_to(root) for p in root.rglob("*")}
    assert not (root / "dangerous").exists()


def test_arbitrary_paths_and_unselected_recent_projects_are_refused(engine):
    with pytest.raises(DesktopError, match="leaves"):
        engine.load_plan("../outside.plan.yaml")
    with pytest.raises(DesktopError, match="recent"):
        engine.dispatch("project.recent_open", {"path":str(engine.root.parent)})
    with pytest.raises(DesktopError, match="Unsupported"):
        engine.dispatch("shell.execute", {"command":"echo bad"})


def test_review_is_bound_to_bytes_and_resolved_target(engine, monkeypatch):
    summary = engine.load_plan("holoqa.plan.yaml")[1]
    token = engine.review(summary["path"], summary["sha256"])["token"]
    (engine.root / summary["path"]).write_text(PLAN.replace("Ready", "anything"))
    with pytest.raises(DesktopError, match="changed"):
        engine.start(token)
    (engine.root / summary["path"]).write_text(PLAN.replace("http://localhost:3000", "${DESKTOP_TEST_TARGET}"))
    monkeypatch.setenv("DESKTOP_TEST_TARGET", "http://localhost:3000")
    summary = engine.load_plan("holoqa.plan.yaml")[1]
    token = engine.review(summary["path"], summary["sha256"])["token"]
    monkeypatch.setenv("DESKTOP_TEST_TARGET", "https://different.test")
    with pytest.raises(DesktopError, match="changed"):
        engine.start(token)


def test_starter_plan_escapes_yaml_and_never_overwrites(engine):
    with pytest.raises(FileExistsError):
        engine.create_plan("store", "http://localhost:3000")
    assert (engine.root / "holoqa.plan.yaml").read_text() == PLAN
    (engine.root / "holoqa.plan.yaml").unlink()
    value = 'http://localhost:3000/"quoted"#x'
    result = engine.create_plan("store", value)
    assert result["target"] == value


def test_tools_remain_in_owning_engine_and_restart_cannot_authorize_release(engine, monkeypatch):
    plan, summary = engine.load_plan("holoqa.plan.yaml")
    engine.run = workspace.workspace_for(plan, root=engine.root / ".holoqa").start_run(plan)
    engine.lifecycle = "running"
    engine._specs()
    def dom(destination, *, session):
        data = {"url":"http://localhost:3000", "text":"Ready", "text_length":5,"truncated":False}
        destination.write_text(json.dumps(data), encoding="utf-8")
        return data
    monkeypatch.setattr(observe, "dom", dom)
    captured = engine.tool("holoqa_observe", {"step_id":"A1", "kind":"dom"})
    engine.tool("holoqa_judge", {"step_id":"A1"})
    snapshot = engine.snapshot()
    assert snapshot["status"]["decision"] == "RELEASE"
    assert snapshot["status"]["integrity"] == "verified"
    assert "vars" not in snapshot["status"]
    assert "Ready" in engine.artifact(snapshot["path"], captured["file"])["text"]
    # Reloading views/reading history doesn't adopt an untrusted ledger.
    assert engine.history()[0]["decision"] == "RELEASE"
    assert engine.snapshot()["status"]["integrity"] == "verified"
    forget_ledger(engine.run.dir)
    assert engine.snapshot()["status"]["decision"] == "HOLD"
    assert engine.snapshot()["status"]["integrity"] == "unverified"


def test_tool_arguments_cannot_redirect_run_or_author_a_verdict(engine):
    engine.run = object()
    engine.lifecycle = "running"
    specs = engine._specs()
    for tool in specs:
        assert "run_dir" not in tool["inputSchema"]["properties"]
        assert not ({"verdict","passed","status"} & tool["inputSchema"]["properties"].keys())
    with pytest.raises(ValidationError):
        engine.tool("holoqa_block", {"step_id":"A1","reason":"x","run_dir":"outside"})
    with pytest.raises(DesktopError):
        engine.tool("holoqa_run_start", {})
    engine.run = None


def test_only_captured_artifacts_can_be_read(engine):
    plan, _ = engine.load_plan("holoqa.plan.yaml")
    engine.run = workspace.workspace_for(plan, root=engine.root / ".holoqa").start_run(plan)
    with pytest.raises(DesktopError, match="captured artifacts"):
        engine.artifact(str(engine.run.dir.relative_to(engine.root)), "plan.pinned.yaml")


def test_symlinked_plan_and_storage_cannot_escape_project(engine, tmp_path):
    outside = tmp_path / "outside";outside.mkdir()
    try:
        (engine.root / ".holoqa").symlink_to(outside, target_is_directory=True)
        (outside / "plan.yaml").write_text(PLAN)
        (engine.root / "linked.plan.yaml").symlink_to(outside / "plan.yaml")
    except OSError:
        pytest.skip("Host requires permission for symlinks")
    assert "linked.plan.yaml" not in engine.project()["plans"]
    with pytest.raises(DesktopError, match="leaves"):
        engine.history()


def test_stdio_discovery_and_shutdown_are_protocol_only(tmp_path):
    project = tmp_path / "projet é spaces";project.mkdir()
    messages = [{"id":1,"method":"hello"},
                {"id":2,"method":"project.open","params":{"path":str(project)}}]
    process = subprocess.run([sys.executable,"-m","holoqa.desktop","--state-dir",str(tmp_path / "state")],
        input="\n".join(json.dumps(m) for m in messages)+"\n", capture_output=True,
        text=True, encoding="utf-8", timeout=20)
    assert process.returncode == 0, process.stderr
    results = {m["id"]:m for m in map(json.loads, process.stdout.splitlines())}
    assert results[1]["result"]["protocol"] == 1
    assert results[2]["result"]["name"] == project.name
    assert not list(project.iterdir())
