"""TUI workspace picker: switching which application/plan the console shows.

`plan_path` used to be fixed at construction, so the console could only ever
show the plan it was launched with. A workspace is one application, so picking a
workspace is picking the plan being tested.
"""

from __future__ import annotations

import json

import pytest

from holoqa import plan as plan_module
from holoqa.tui import HoloQATui
from holoqa.workspace import Workspace, workspace_for

PLAN_SHOP = """
meta:
  app: shop
  base_url: http://127.0.0.1:8099
steps:
  - id: A1
    title: the home page loads
    expect:
      - screenshot: required
"""

PLAN_ADMIN = """
meta:
  app: admin
  base_url: http://127.0.0.1:8098
steps:
  - id: B1
    title: the admin console loads
    expect:
      - screenshot: required
"""


def _seed(root, plan_text: str, app: str, verdicts=("PASS",)):
    """Create a plan and a run for it, inside one shared .holoqa root."""
    project = root / app
    project.mkdir(exist_ok=True)
    plan_path = project / f"{app}.plan.yaml"
    plan_path.write_text(plan_text, encoding="utf-8")
    plan = plan_module.load(plan_path)
    ws = workspace_for(plan, root=root / ".holoqa")
    ws.dir.mkdir(parents=True, exist_ok=True)
    runs = []
    for index, verdict in enumerate(verdicts):
        run = ws.start_run(plan, meta={"tester": f"{app}{index}"})
        ev = run.evidence_dir / "step-a1-dom.json"
        ev.write_text(json.dumps({"url": "http://x/", "text": app}), encoding="utf-8")
        step_id = plan.steps[0].id
        run.attach(step_id, kind="dom", path=ev, target="http://x/")
        run.set_verdict(step_id, verdict, note="" if verdict == "PASS" else "broke")
        ws.refresh(run.read()["run_id"])
        runs.append(run)
    return plan, ws, runs


@pytest.fixture()
def two_apps(tmp_path):
    """Two applications, each with its own workspace, side by side."""
    root = tmp_path / "project"
    root.mkdir()
    shop_plan, shop_ws, shop_runs = _seed(root, PLAN_SHOP, "shop", ("PASS", "FAIL"))
    admin_plan, admin_ws, admin_runs = _seed(root, PLAN_ADMIN, "admin", ("PASS",))
    return {
        "root": root,
        "shop": (shop_plan, shop_ws, shop_runs),
        "admin": (admin_plan, admin_ws, admin_runs),
    }


@pytest.mark.anyio
async def test_workspace_picker_lists_every_application(two_apps):
    """`w` shows both workspaces, not just the one that was launched."""
    shop_plan, shop_ws, _runs = two_apps["shop"]
    app = HoloQATui(plan_path=shop_plan.source_path)
    app.workspace = shop_ws
    async with app.run_test() as pilot:
        app.action_show_workspaces()
        await pilot.pause()
        assert app._workspace_menu_open is True
        apps = {row["app"] for row in app._workspace_rows}
        assert {"shop", "admin"} <= apps
        row = next(r for r in app._workspace_rows if r["app"] == "admin")
        assert row["runs"] == 1
        assert row["plan_path"].endswith("admin.plan.yaml")


@pytest.mark.anyio
async def test_opening_a_workspace_rebinds_the_plan_and_current_run(two_apps):
    """The capability the console lacked: change what is being tested."""
    shop_plan, shop_ws, _runs = two_apps["shop"]
    admin_plan, admin_ws, admin_runs = two_apps["admin"]

    app = HoloQATui(plan_path=shop_plan.source_path)
    app.workspace = shop_ws
    async with app.run_test() as pilot:
        app.action_show_workspaces()
        await pilot.pause()
        index = [row["app"] for row in app._workspace_rows].index("admin")
        app.query_one("#workspace-menu").highlighted = index
        await pilot.pause()

        app.action_open_workspace()
        await pilot.pause()

        assert app._workspace_menu_open is False
        assert app.workspace.app == "admin"
        assert str(app.plan_path) == admin_plan.source_path
        assert app.run_dir == str(admin_runs[0].dir)
        assert app.state.run_id == admin_runs[0].read()["run_id"]


@pytest.mark.anyio
async def test_switching_back_returns_to_the_first_workspace(two_apps):
    shop_plan, shop_ws, shop_runs = two_apps["shop"]
    app = HoloQATui(plan_path=shop_plan.source_path)
    app.workspace = shop_ws
    async with app.run_test() as pilot:
        # shop -> admin
        app.action_show_workspaces()
        await pilot.pause()
        app.query_one("#workspace-menu").highlighted = [
            r["app"] for r in app._workspace_rows
        ].index("admin")
        await pilot.pause()
        app.action_open_workspace()
        await pilot.pause()
        assert app.workspace.app == "admin"

        # admin -> shop
        app.action_show_workspaces()
        await pilot.pause()
        app.query_one("#workspace-menu").highlighted = [
            r["app"] for r in app._workspace_rows
        ].index("shop")
        await pilot.pause()
        app.action_open_workspace()
        await pilot.pause()

        assert app.workspace.app == "shop"
        assert app.run_dir == str(shop_runs[-1].dir)


@pytest.mark.anyio
async def test_workspace_row_shows_run_count_and_last_decision(two_apps):
    """A row must carry enough to choose from without opening it."""
    shop_plan, shop_ws, _runs = two_apps["shop"]
    app = HoloQATui(plan_path=shop_plan.source_path)
    app.workspace = shop_ws
    async with app.run_test() as pilot:
        app.action_show_workspaces()
        await pilot.pause()
        row = next(r for r in app._workspace_rows if r["app"] == "shop")
        label = app._workspace_label(row, row["app"])
        assert "2 run(s)" in label
        assert "shop.plan.yaml" in label
        assert label.startswith("●")  # the current workspace is marked


@pytest.mark.anyio
async def test_opening_a_workspace_with_no_runs_reports_instead_of_crashing(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    plan_path = root / "shop.plan.yaml"
    plan_path.write_text(PLAN_SHOP, encoding="utf-8")
    plan = plan_module.load(plan_path)
    ws = workspace_for(plan, root=root / ".holoqa")
    ws.dir.mkdir(parents=True, exist_ok=True)
    ws.runs_dir.mkdir(parents=True, exist_ok=True)  # a workspace with no runs yet

    app = HoloQATui(plan_path=plan.source_path)
    app.workspace = ws
    async with app.run_test() as pilot:
        app.action_show_workspaces()
        await pilot.pause()
        assert app._workspace_menu_open is True, "an empty workspace must still be listed"
        app.query_one("#workspace-menu").highlighted = 0
        await pilot.pause()
        app.action_open_workspace()
        await pilot.pause()
        assert "no runs" in app.state.safety.lower()


@pytest.mark.anyio
async def test_workspace_picker_with_nothing_to_show_reports():
    app = HoloQATui(plan_path=None)
    async with app.run_test() as pilot:
        app.action_show_workspaces()
        await pilot.pause()
        assert app._workspace_menu_open is False
        assert "No workspaces" in app.state.safety


@pytest.mark.anyio
async def test_escape_closes_the_workspace_picker(two_apps):
    shop_plan, shop_ws, _runs = two_apps["shop"]
    app = HoloQATui(plan_path=shop_plan.source_path)
    app.workspace = shop_ws
    async with app.run_test() as pilot:
        app.action_show_workspaces()
        await pilot.pause()
        assert app._workspace_menu_open is True
        app.action_hide_search()
        await pilot.pause()
        assert app._workspace_menu_open is False
