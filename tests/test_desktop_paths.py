"""Nested storage redirects must fail before writes or report collection."""
import json
import os
import subprocess

import pytest

from holoqa.desktop import DesktopError, Engine
from holoqa import desktop, observe
from holoqa.run import forget_ledger

PLAN = """meta:
  app: desktop-paths
  base_url: http://localhost:3000
steps:
  - id: A1
    title: Ready
    expect:
      - text_contains: Ready
"""


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    (root / "holoqa.plan.yaml").write_text(PLAN)
    engine = Engine(tmp_path / "state")
    engine.open_project(str(root))
    return engine


def link(source, destination, *, directory=True):
    try:
        source.symlink_to(destination, target_is_directory=directory)
    except OSError:
        pytest.skip("Host requires permission for symlinks")


@pytest.mark.parametrize("relative", [".holoqa/workspaces", ".holoqa/workspaces/desktop-paths", ".holoqa/workspaces/desktop-paths/runs"])
def test_nested_workspace_redirect_is_rejected_before_any_write(project, tmp_path, relative):
    outside = tmp_path / "outside"; outside.mkdir()
    source = project.root / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    link(source, outside)
    plan, _ = project.load_plan("holoqa.plan.yaml")
    with pytest.raises(DesktopError, match="Storage path leaves|Linked storage"):
        project.validated_workspace(plan)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("filename", [".workspace.lock", "workspace.json"])
def test_redirected_workspace_files_rejected_without_modifying_destination(project, tmp_path, filename):
    outside = tmp_path / "outside.json"; outside.write_text("do not change")
    directory = project.root / ".holoqa/workspaces/desktop-paths"
    directory.mkdir(parents=True)
    link(directory / filename, outside, directory=False)
    plan, _ = project.load_plan("holoqa.plan.yaml")
    with pytest.raises(DesktopError, match="Linked storage"):
        project.validated_workspace(plan)
    assert outside.read_text() == "do not change"


@pytest.mark.parametrize("location", ["out", "evidence"])
def test_export_redirects_and_unregistered_evidence_links_are_refused(project, tmp_path, location):
    plan, _ = project.load_plan("holoqa.plan.yaml")
    run = project.validated_workspace(plan).start_run(plan)
    outside = tmp_path / "outside"; outside.mkdir()
    secret = outside / "secret.txt"; secret.write_text("private")
    target = run.dir / location
    if location == "out":
        target.rmdir()
        link(target, outside)
    else:
        # report.package includes all evidence files, even unregistered ones.
        link(target / "unregistered.txt", secret, directory=False)
    try:
        with pytest.raises(DesktopError, match="Linked storage"):
            project.validate_export(run)
        assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    finally:
        forget_ledger(run.dir)


@pytest.mark.parametrize("run_id", ["../../escaped", "report:alternate-stream", "C:relative", "bad\x00name"])
def test_archive_identifier_cannot_escape_export_folder(project, run_id):
    plan, _ = project.load_plan("holoqa.plan.yaml")
    run = project.validated_workspace(plan).start_run(plan)
    try:
        data = run.read(); data["run_id"] = run_id
        (run.dir / "run.json").write_text(json.dumps(data))
        with pytest.raises(DesktopError, match="archive filename"):
            project.validate_export(run)
    finally:
        forget_ledger(run.dir)


def test_metadata_read_is_bounded_and_requires_a_json_object(project):
    path = project.root / "package.json"
    path.write_text('[' + ' ' * 100 + ']')
    with pytest.raises(DesktopError, match="smaller"):
        project.read_project_json("package.json", limit=16)
    path.write_text("[]")
    with pytest.raises(DesktopError, match="JSON object"):
        project.read_project_json("package.json")
    path.write_text('{"dependencies":{"react":"*"}}')
    assert project.read_project_json("package.json")["dependencies"]["react"] == "*"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_metadata_fifo_is_rejected_without_opening_it(project):
    os.mkfifo(project.root / "package.json")
    with pytest.raises(DesktopError, match="regular file"):
        project.read_project_json("package.json")


def test_storage_inspection_is_bounded(project):
    directory = project.root / ".holoqa"
    directory.mkdir()
    for i in range(4):
        (directory / str(i)).write_text("")
    with pytest.raises(DesktopError, match="inspection limit"):
        project.validate_storage_tree(directory, limit=3)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-junction boundary")
def test_windows_junction_cannot_redirect_workspace_writes(project, tmp_path):
    outside = tmp_path / "outside with spaces"; outside.mkdir()
    source = project.root / ".holoqa" / "workspaces"
    source.parent.mkdir(parents=True)
    # Junction creation works without the administrator/developer-mode rights
    # that symlinks require on some Windows hosts. Do not skip command failures.
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(source), str(outside)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        plan, _ = project.load_plan("holoqa.plan.yaml")
        with pytest.raises(DesktopError, match="Storage path leaves|Linked storage"):
            project.validated_workspace(plan)
        assert not list(outside.iterdir())
    finally:
        source.rmdir()  # Remove the junction itself, preserving its destination.


@pytest.mark.parametrize("failure", ["signed_out", "exception"])
def test_failed_retest_authentication_preserves_completed_prior_run(project, monkeypatch, failure):
    plan, summary = project.load_plan("holoqa.plan.yaml")
    run = project.validated_workspace(plan).start_run(plan)
    project.run = run
    project.lifecycle = "running"
    project._specs()

    def dom(destination, *, session):
        data = {"url": "http://localhost:3000", "text": "Ready", "text_length": 5, "truncated": False}
        destination.write_text(json.dumps(data), encoding="utf-8")
        return data

    monkeypatch.setattr(observe, "dom", dom)
    project.tool("holoqa_observe", {"step_id": "A1", "kind": "dom"})
    project.tool("holoqa_judge", {"step_id": "A1"})
    project.lifecycle = "completed"
    assert project.snapshot()["status"]["decision"] == "RELEASE"
    token = project.review(summary["path"], summary["sha256"])["token"]
    before = {p.relative_to(project.root) for p in project.root.rglob("*")}
    closed = []

    class SignedOutProvider:
        def __init__(self, *args):
            pass

        def initialize(self):
            if failure == "exception":
                raise DesktopError("Fixture authentication failed")
            return "signed_out"

        def close(self):
            closed.append(True)

    monkeypatch.setattr(desktop, "CodexSession", SignedOutProvider)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/fixture/" + name)
    monkeypatch.setattr(desktop.browser, "cleanup", lambda *_: pytest.fail("Prior completed run must not be cleaned up"))
    try:
        with pytest.raises(DesktopError, match="Sign into Codex|authentication failed"):
            project.start(token)
        assert project.run is run
        assert project.lifecycle == "completed"
        assert project.run_state(run) == "completed"
        assert project.snapshot()["status"]["decision"] == "RELEASE"
        assert project.history()[0]["decision"] == "RELEASE"
        assert project.provider is None and closed == [True]
        assert {p.relative_to(project.root) for p in project.root.rglob("*")} == before
    finally:
        forget_ledger(run.dir)
