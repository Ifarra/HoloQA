"""TUI session picker: switching runs, continuing, and retesting.

The TUI is a *client* of the workspace. It must be able to point at another run
without restarting, and it must never present a run it did not capture as
something it can trust — the picker shows the integrity state instead of hiding
it, and refuses Continue for a run this process cannot vouch for.
"""

from __future__ import annotations

import json

import pytest

from holoqa import plan as plan_module
from holoqa import run as run_module
from holoqa import workspace as workspace_module
from holoqa.tui import HoloQATui, row_is_legacy
from holoqa.workspace import workspace_for

PLAN = """
meta:
  app: shop
  base_url: http://127.0.0.1:8099
steps:
  - id: A1
    title: the home page loads
    expect:
      - screenshot: required
  - id: A2
    title: the cart accepts an item
    expect:
      - screenshot: required
"""


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = root / "shop.plan.yaml"
    path.write_text(PLAN, encoding="utf-8")
    return root, plan_module.load(path)


@pytest.fixture()
def seeded(project, tmp_path):
    """A workspace with two runs, the second failing."""
    _root, plan = project
    ws = workspace_for(plan, root=tmp_path / ".holoqa")
    ws.dir.mkdir(parents=True, exist_ok=True)
    runs = []
    for n, verdict in enumerate(("PASS", "FAIL")):
        run = ws.start_run(plan, meta={"tester": f"r{n}"})
        ev = run.evidence_dir / "step-a1-dom.json"
        ev.write_text(json.dumps({"url": "http://127.0.0.1:8099/", "text": "shop"}), encoding="utf-8")
        run.attach("A1", kind="dom", path=ev, target="http://127.0.0.1:8099/")
        run.set_verdict("A1", verdict, note="" if verdict == "PASS" else "the cart broke")
        ws.refresh(run.read()["run_id"])
        runs.append(run)
    return ws, plan, runs


def test_row_is_legacy_distinguishes_layouts(tmp_path, seeded):
    ws, _plan, runs = seeded
    assert row_is_legacy(runs[0].dir, ws) is False
    legacy = tmp_path / "old" / ".holoqa" / "runs" / "20260101-0000-shop"
    assert row_is_legacy(legacy, ws) is True


@pytest.mark.anyio
async def test_session_picker_lists_runs_and_switches(seeded):
    """`s` opens the picker; Enter binds the console to the highlighted run."""
    ws, plan, runs = seeded
    first_id = runs[0].read()["run_id"]
    second_id = runs[1].read()["run_id"]
    # Point the workspace at the second run, then switch back to the first.
    ws.switch(second_id)

    app = HoloQATui(
        plan_path=plan.source_path,
        run_dir=str(runs[1].dir),
    )
    app.workspace = ws
    async with app.run_test() as pilot:
        app.action_show_sessions()
        await pilot.pause()
        assert app._session_menu_open is True
        listed = {row["run_id"] for row in app._session_rows}
        assert {first_id, second_id} <= listed

        # Move to the first run and open it. `highlighted` is a Textual reactive,
        # so the assignment settles on the next message-pump cycle.
        index = [row["run_id"] for row in app._session_rows].index(first_id)
        app.query_one("#session-menu").highlighted = index
        await pilot.pause()
        app.action_open_session()
        await pilot.pause()

        assert app._session_menu_open is False
        assert app.run_dir == str(runs[0].dir)
        assert str(runs[0].dir) in app.state.run_id or app.state.run_id == first_id
        assert ws.current_run_id() == first_id


@pytest.mark.anyio
async def test_picker_shows_integrity_for_untrusted_runs(seeded):
    """A run this process did not capture is labelled, never silently opened."""
    ws, plan, runs = seeded
    # Drop the ledger, the way a fresh process would see the run.
    run_module.forget_ledger(runs[0].dir)
    ws.refresh(runs[0].read()["run_id"])

    app = HoloQATui(plan_path=plan.source_path, run_dir=str(runs[0].dir))
    app.workspace = ws
    async with app.run_test() as pilot:
        app.action_show_sessions()
        await pilot.pause()
        row = next(r for r in app._session_rows if r["run_id"] == runs[0].read()["run_id"])
        assert row["integrity"] != "verified"
        label = app._session_label(row, ws.current_run_id())
        assert row["integrity"] in label  # the row says so, not just the data


@pytest.mark.anyio
async def test_continue_refuses_a_run_this_process_did_not_capture(seeded):
    ws, plan, runs = seeded
    run_module.forget_ledger(runs[0].dir)
    ws.refresh(runs[0].read()["run_id"])

    app = HoloQATui(plan_path=plan.source_path, run_dir=str(runs[0].dir))
    app.workspace = ws
    async with app.run_test() as pilot:
        app.action_show_sessions()
        await pilot.pause()
        app.query_one("#session-menu").highlighted = [
            r["run_id"] for r in app._session_rows
        ].index(runs[0].read()["run_id"])
        app.action_continue_run()
        await pilot.pause()
        assert "cannot be continued here" in app.state.safety


@pytest.mark.anyio
async def test_retest_adds_a_run_and_keeps_the_previous(seeded):
    """The behaviour the flat layout forbade: retesting without deleting."""
    ws, plan, runs = seeded
    before = ws.run_ids()
    before_counts = {
        rid: len(list((ws.run_dir(rid) / "evidence").iterdir())) for rid in before
    }

    app = HoloQATui(plan_path=plan.source_path)
    app.workspace = ws
    async with app.run_test() as pilot:
        app.action_retest()
        await pilot.pause()

    after = ws.run_ids()
    assert len(after) == len(before) + 1
    for run_id in before:
        assert run_id in after, f"{run_id} was removed by a retest"
        assert len(list((ws.run_dir(run_id) / "evidence").iterdir())) == before_counts[run_id]

    fresh = ws.run_dir(after[-1])
    assert run_module.Run(fresh).read()["meta"]["retest_of"] in before


@pytest.mark.anyio
async def test_retest_without_a_plan_reports_instead_of_crashing():
    app = HoloQATui(plan_path=None)
    async with app.run_test() as pilot:
        app.action_retest()
        await pilot.pause()
        assert "needs a plan" in app.state.safety


@pytest.mark.anyio
async def test_sessions_without_a_workspace_reports_instead_of_crashing():
    app = HoloQATui(plan_path=None)
    async with app.run_test() as pilot:
        app.action_show_sessions()
        await pilot.pause()
        assert "No workspace" in app.state.safety or "no runs" in app.state.safety.lower()
