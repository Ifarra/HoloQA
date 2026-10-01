"""Workspaces: one home per application, many runs inside it.

The bugs these close were reproduced against the old layout before this module
existed:

* two runs of the same plan in the same minute collided and the second was
  REFUSED ("run already exists"), so retesting meant deleting the failing run;
* ``run.find()`` resolved a process-relative root, so a run was invisible from
  any directory but the one it was created in.

Both are asserted here as behaviour, not as comments.
"""

from __future__ import annotations

import json

import pytest

from holoqa import workspace as workspace_module
from holoqa.run import GuardrailError, Run
from holoqa import plan as plan_module
from holoqa.workspace import Workspace, workspace_for

PLAN = """
meta:
  app: shop
steps:
  - id: A1
    title: the home page loads
    expect:
      - screenshot: required
  - id: A2
    title: the cart works
    expect:
      - screenshot: required
"""


@pytest.fixture()
def project(tmp_path):
    """A project directory with a plan in it, the way a real checkout looks."""
    root = tmp_path / "project"
    root.mkdir()
    path = root / "shop.plan.yaml"
    path.write_text(PLAN, encoding="utf-8")
    return root, plan_module.load(path)


@pytest.fixture()
def workspace(project, tmp_path):
    """A workspace pinned under the test's own temp directory.

    The root is passed explicitly so a stray ``.holoqa`` anywhere above the temp
    tree can never capture the test — which is exactly the bug that once made a
    test write into the user's home directory.
    """
    _root, plan = project
    ws = workspace_for(plan, root=tmp_path / ".holoqa")
    ws.dir.mkdir(parents=True, exist_ok=True)
    return ws


# ---------------------------------------------------------------- the two bugs


def test_two_runs_in_the_same_minute_do_not_collide(workspace, project):
    """The retest bug: the old layout refused a same-minute second run."""
    _root, plan = project
    first = workspace.start_run(plan, meta={"tester": "one"})
    second = workspace.start_run(plan, meta={"tester": "two"})

    assert first.dir != second.dir
    assert first.dir.is_dir() and second.dir.is_dir()
    assert first.read()["run_id"] != second.read()["run_id"]


def test_retest_keeps_the_previous_run_and_its_evidence(workspace, project):
    """A failing run is the baseline a fix is measured against, not garbage."""
    _root, plan = project
    first = workspace.start_run(plan, meta={"tester": "one"})
    # Give the first run some evidence, then fail it — the run a developer
    # would have had to delete under the old layout.
    evidence = first.evidence_dir / "step-a1-dom.json"
    evidence.write_text(json.dumps({"url": "http://x/", "text": "shop"}), encoding="utf-8")
    first.attach("A1", kind="dom", path=evidence, target="http://x/")
    first.set_verdict("A1", "FAIL", note="the home page was blank")

    second = workspace.retest(plan, meta={"tester": "two"})

    assert first.dir.is_dir(), "the failing run must survive a retest"
    assert evidence.is_file(), "its evidence must survive a retest"
    assert first.read()["steps"]["A1"]["verdict"] == "FAIL"
    assert second.read()["meta"]["retest_of"] == first.read()["run_id"]


def test_workspace_resolves_from_the_plan_not_the_cwd(workspace, project, monkeypatch, tmp_path):
    """The lookup bug: a run must be reachable from any working directory."""
    _root, plan = project
    run = workspace.start_run(plan, meta={"tester": "one"})

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    again = workspace_for(str(plan.source_path))
    assert again.dir == workspace.dir
    assert run.read()["run_id"] in again.run_ids()


# ------------------------------------------------------------------- sequencing


def test_runs_are_numbered_sequentially(workspace, project):
    _root, plan = project
    ids = [workspace.start_run(plan).read()["run_id"] for _ in range(3)]
    sequences = [int(name.split("-", 1)[0]) for name in ids]
    assert sequences == [1, 2, 3]


def test_current_run_is_a_pointer_not_the_newest(workspace, project):
    _root, plan = project
    first = workspace.start_run(plan)
    second = workspace.start_run(plan)
    assert workspace.current_run_id() == second.read()["run_id"]

    workspace.switch(first.read()["run_id"])
    assert workspace.current_run_id() == first.read()["run_id"]

    # And it survives a fresh read of the index.
    reopened = Workspace(workspace.dir)
    assert reopened.current_run_id() == first.read()["run_id"]


def test_switch_refuses_an_unknown_run(workspace):
    with pytest.raises(GuardrailError, match="no run"):
        workspace.switch("999-nope")


# -------------------------------------------------------------------- listing


def test_list_runs_is_newest_first_and_reports_counts(workspace, project):
    _root, plan = project
    first = workspace.start_run(plan)
    second = workspace.start_run(plan)
    # PASS requires evidence (guardrail 1), so attach a capture before judging.
    evidence = second.evidence_dir / "step-a1-dom.json"
    evidence.write_text(json.dumps({"url": "http://x/", "text": "shop"}), encoding="utf-8")
    second.attach("A1", kind="dom", path=evidence, target="http://x/")
    second.set_verdict("A1", "PASS", note="")
    workspace.refresh(second.read()["run_id"])

    rows = workspace.list_runs()
    assert [row["run_id"] for row in rows][0] == second.read()["run_id"]
    assert rows[0]["counts"]["PASS"] == 1
    assert rows[0]["pending"] == 1
    assert rows[0]["decision"] == "HOLD"  # one step still unjudged
    assert any(row["run_id"] == first.read()["run_id"] for row in rows)


def test_index_does_not_hide_a_run_it_does_not_know(workspace, project):
    """Losing a run from the list is worse than a slow list."""
    _root, plan = project
    run = workspace.start_run(plan)
    # Simulate an index that lost the entry (hand-edited, or written by an
    # older build).
    data = workspace.read()
    data["runs"] = []
    workspace._write(data)

    assert run.read()["run_id"] in [row["run_id"] for row in workspace.list_runs()]


# --------------------------------------------------------------------- delete


def test_delete_moves_to_trash_and_refuses_the_current_run(workspace, project):
    _root, plan = project
    first = workspace.start_run(plan)
    second = workspace.start_run(plan)
    first_id = first.read()["run_id"]
    second_id = second.read()["run_id"]

    with pytest.raises(GuardrailError, match="current run"):
        workspace.delete(second_id)

    target = workspace.delete(first_id)
    assert not (workspace.runs_dir / first_id).exists()
    assert (target / "run.json").is_file(), "the bytes are recoverable from trash"
    # Deleting the current run needs to be explicit.
    workspace.delete(second_id, force=True)
    assert not (workspace.runs_dir / second_id).exists()
    assert workspace.current_run_id() == ""


# ------------------------------------------------------------------ integrity


def test_a_run_made_here_is_verified_and_a_reopened_one_is_not(workspace, project):
    """Switching to an old run must not silently promote it to trusted.

    ``Run.create`` deliberately installs no ledger — ownership is taken in
    ``attach``, by the process that actually produces bytes. So a run is
    ``verified`` only once *this* process has captured something in it, and a
    run reopened without its ledger is not.
    """
    _root, plan = project
    run = workspace.start_run(plan)

    # Before this process captures anything, nothing is vouched for.
    assert run.integrity()["status"] == "unverified"

    evidence = run.evidence_dir / "step-a1-dom.json"
    evidence.write_text(json.dumps({"url": "http://x/", "text": "shop"}), encoding="utf-8")
    run.attach("A1", kind="dom", path=evidence, target="http://x/")
    assert run.integrity()["status"] == "verified"

    # A process that did not capture the run cannot vouch for it.
    from holoqa import run as run_module

    run_module.forget_ledger(run.dir)
    status = Run(run.dir).integrity(adopt=False)["status"]
    assert status in {"unverified", "attested"}


# ----------------------------------------------------------------- migration


def test_legacy_runs_are_readable_and_retestable(tmp_path, monkeypatch):
    """A run from the old flat layout stays usable; it is never moved by itself."""
    root = tmp_path / "project"
    root.mkdir()
    plan_path = root / "shop.plan.yaml"
    plan_path.write_text(PLAN, encoding="utf-8")
    plan = plan_module.load(plan_path)

    legacy_dir = root / ".holoqa" / "runs" / "20260901-1200-shop"
    Run.create(legacy_dir, plan, meta={"tester": "legacy"})

    ws = workspace_for(plan)
    legacy = ws.legacy_runs(root)
    assert [row["run_id"] for row in legacy] == ["20260901-1200-shop"]
    assert legacy[0]["legacy"] is True
    assert legacy_dir.is_dir(), "discovery must not move anything"

    # Migration is explicit, previewable, and preserves the bytes.
    preview = workspace_module.migrate(ws, root=root, dry_run=True)
    assert preview["status"] == "dry_run"
    assert legacy_dir.is_dir()

    result = workspace_module.migrate(ws, root=root)
    assert result["status"] == "migrated"
    assert not legacy_dir.exists()
    assert any("shop" in name for name in ws.run_ids())


def test_migrate_then_retest_produces_a_numbered_run(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    plan_path = root / "shop.plan.yaml"
    plan_path.write_text(PLAN, encoding="utf-8")
    plan = plan_module.load(plan_path)
    Run.create(root / ".holoqa" / "runs" / "20260901-1200-shop", plan, meta={"tester": "legacy"})

    ws = workspace_for(plan)
    workspace_module.migrate(ws, root=root)
    fresh = ws.retest(plan)
    assert fresh.dir.is_dir()
    assert fresh.dir.parent == ws.runs_dir


def test_a_legacy_only_project_is_still_listed(tmp_path):
    """A project with only old-layout runs must not read as 'no runs'.

    `holoqa runs --all` on such a checkout has to find them, or the migration
    that exists to move them has nothing to work from.
    """
    root = tmp_path / "project"
    root.mkdir()
    plan_path = root / "shop.plan.yaml"
    plan_path.write_text(PLAN, encoding="utf-8")
    plan = plan_module.load(plan_path)
    Run.create(root / ".holoqa" / "runs" / "20260901-1200-shop", plan, meta={"tester": "legacy"})

    # No workspace.json anywhere yet.
    spaces = workspace_module.find_workspaces(root=root / ".holoqa")
    apps = {ws.app for ws in spaces}
    assert "shop" in apps, "a legacy-only project must still appear"

    listed = [
        row["run_id"]
        for ws in spaces if ws.app == "shop"
        for row in ws.legacy_runs(root)
    ]
    assert "20260901-1200-shop" in listed


def test_a_monorepo_shares_one_root_across_apps(tmp_path):
    """Two apps in subdirectories must land in ONE workspace root.

    Anchoring on each plan's own directory gave every application its own
    `.holoqa`, so no picker could ever list them together — the exact thing the
    workspace picker exists to do.
    """
    root = tmp_path / "monorepo"
    root.mkdir()
    (root / ".git").mkdir()  # the project boundary
    made = {}
    for app in ("shop", "admin"):
        directory = root / app
        directory.mkdir()
        path = directory / f"{app}.plan.yaml"
        path.write_text(
            f"meta:\n  app: {app}\nsteps:\n  - id: A1\n    title: t\n"
            "    expect: [{screenshot: required}]\n",
            encoding="utf-8",
        )
        plan = plan_module.load(path)
        ws = workspace_for(plan)
        ws.start_run(plan, meta={"tester": app})
        made[app] = ws

    # Both apps resolved the same root, just different workspace subdirs.
    assert made["shop"].dir.parent == made["admin"].dir.parent
    assert made["shop"].dir.parent.name == "workspaces"

    spaces = workspace_module.find_workspaces(start=root)
    assert {ws.app for ws in spaces} >= {"shop", "admin"}


def test_a_project_without_a_boundary_stays_local(tmp_path):
    """No .git and no existing root: a plan keeps its own .holoqa.

    Anchoring everything on some distant ancestor would be worse than one root
    per directory — it would make unrelated projects share a workspace.
    """
    root = tmp_path / "loose"
    root.mkdir()
    plan_path = root / "shop.plan.yaml"
    plan_path.write_text(PLAN, encoding="utf-8")
    plan = plan_module.load(plan_path)
    ws = workspace_for(plan)
    # <root>/.holoqa/workspaces/<app> — the .holoqa stays in this directory.
    assert ws.dir == root / ".holoqa" / "workspaces" / "shop"
