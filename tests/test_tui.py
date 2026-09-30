from __future__ import annotations

from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input, TabbedContent

from holoqa import agent as agent_module
from holoqa import tui as tui_module
from holoqa.tui import ArtifactItem, AssertionItem, HoloQATui, TuiState


@pytest.mark.anyio
async def test_demo_renders_the_release_dashboard() -> None:
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        assert "web-app-smoke" in str(app.query_one("#run-title").render())
        assert app.query_one("#step-table")
        assert app.query_one("#agent-log")
        assert "RUNNING" in str(app.query_one("#activity").render())
        assert "Sign in with valid user" in str(app.query_one("#activity").render())
        assert "03: sign-in.png" in str(app.query_one("#evidence").render())
        assert "2/8" in str(app.query_one("#steps-header").render())
        await pilot.pause()


@pytest.mark.anyio
async def test_animated_holoshop_demo_replays_four_realistic_groups() -> None:
    app = HoloQATui.demo(animated=True)
    async with app.run_test(size=(140, 42)) as pilot:
        assert len(app.state.groups) == 4
        assert all(
            sum(group_id == app.state.step_groups.get(step_id) for step_id, _, _ in app.state.steps) >= 2
            for group_id in app.state.groups
        )
        assert app._animated_timer is not None
        app._animated_timer.pause()
        for _ in range(len(app.state.steps) * 2):
            app._advance_animated_demo()
        await pilot.pause()
        assert app.state.activity == "COMPLETE"
        assert app.state.decision == "RELEASE"
        assert all(verdict == "PASS" for _, _, verdict in app.state.steps)
        assert len(app.state.artifacts) == 8
        assert Path(app.run_dir, "out", "report.md").is_file()
        assert "holoshop.shop" in str(app.query_one("#run-title").render())


@pytest.mark.anyio
async def test_compact_terminal_keeps_header_and_verdict_visible() -> None:
    app = HoloQATui.demo()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        assert app.has_class("compact")
        assert "cancel" in str(app.query_one("#shortcuts").render()).lower()
        assert app.query_one("#progress").region.bottom < app.query_one("#verdict-card").region.bottom
        assert app.query_one("#workspace").region.right == app.query_one("#body").region.right


@pytest.mark.anyio
async def test_theme_picker_has_ctrl_p_header_action_and_live_preview() -> None:
    app = HoloQATui.demo()
    async with app.run_test(size=(140, 42)) as pilot:
        shortcuts = str(app.query_one("#shortcuts").render())
        assert "^p" in shortcuts
        original = app.theme
        await pilot.press("ctrl+p")
        assert app._theme_menu_open is True
        assert app.query_one("#theme-menu").display is True
        assert app.query_one("#theme-menu").option_count == len(app.available_themes)
        assert app.query_one("#theme-menu").option_count > 7
        selected_before = app.selected_step_id
        # Move to a theme that is definitely not the current one. The active
        # theme can sit at either end of the catalogue, where a `down` is a
        # no-op — that is a property of the picker, not what this test is about.
        menu = app.query_one("#theme-menu")
        current = menu.highlighted if menu.highlighted is not None else 0
        direction = "up" if current >= menu.option_count - 1 else "down"
        await pilot.press(direction)
        assert app.theme != original
        assert app.selected_step_id == selected_before
        preview = app.theme
        await pilot.press("enter")
        assert app._theme_menu_open is False
        assert app.theme == preview
        await pilot.press("ctrl+p")
        moved = app.theme
        await pilot.press("escape")
        assert app._theme_menu_open is False
        # Esc restores whatever was active when the picker opened.
        assert app.theme == preview


@pytest.mark.anyio
async def test_theme_choice_persists_between_tui_instances(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOLOQA_TUI_CONFIG", str(tmp_path / "tui.json"))
    first = HoloQATui.demo()
    async with first.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.press("down")
        chosen = first.theme
        await pilot.press("enter")
    second = HoloQATui.demo()
    assert second.theme == chosen


@pytest.mark.anyio
async def test_steps_sidebar_is_a_real_scroll_container() -> None:
    """The sidebar must be a scroll container that actually moves.

    Uses the static demo on purpose. The animated one rewrites the step list on
    a timer, so between `scroll_end` and the assertion the content can be
    re-laid-out and the scroll position reset to 0 — the widget was fine, the
    test was racing its own fixture. A fixed list makes the property under test
    (this widget scrolls) deterministic.
    """
    app = HoloQATui.demo()
    async with app.run_test(size=(100, 30)) as pilot:
        sidebar = app.query_one("#step-scroll", VerticalScroll)
        assert sidebar.max_scroll_y > 0, "the step list should overflow this size"
        sidebar.scroll_end(animate=False)
        await pilot.pause()
        assert sidebar.scroll_y == sidebar.max_scroll_y


@pytest.mark.anyio
async def test_detail_tabs_switch_content() -> None:
    app = HoloQATui.demo()
    async with app.run_test(size=(120, 35)) as pilot:
        tabs = app.query_one("#detail-tabs", TabbedContent)
        await pilot.press("2")
        assert tabs.active == "tab-evidence"
        assert "sign-in.png" in str(app.query_one("#evidence").render())
        await pilot.press("3")
        assert tabs.active == "tab-assertions"


@pytest.mark.anyio
async def test_grouped_steps_are_navigable_without_losing_live_step() -> None:
    app = HoloQATui.demo()
    async with app.run_test(size=(120, 35)) as pilot:
        assert "> Purchase Flow" in str(app.query_one("#step-table").render())
        await pilot.click("#step-table", offset=(10, 1))
        assert app.selected_step_id == "01"
        app.action_follow_live()
        await pilot.press("up")
        assert app.selected_step_id == "02"
        assert app.follow_live is False
        assert "Check hero content" in str(app.query_one("#detail").render())
        await pilot.press("f")
        assert app.selected_step_id == "03"
        assert app.follow_live is True
        app.action_toggle_group("ACCOUNT")
        assert "Sign out" not in str(app.query_one("#step-table").render())
        assert "> Account" in str(app.query_one("#step-table").render())


@pytest.mark.anyio
async def test_artifact_can_be_selected_and_safely_opened(tmp_path) -> None:
    run_dir = tmp_path / "run"
    artifact_path = run_dir / "evidence" / "step-01.png"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"fixture")
    opened = []
    state = TuiState(
        steps=[("01", "Capture page", "PASS")],
        artifacts=[ArtifactItem("01", "screenshot", "step-01.png", artifact_path)],
    )
    app = HoloQATui(
        state=state, run_dir=str(run_dir), file_opener=opened.append,
    )
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.press("2")
        await pilot.click("#evidence", offset=(5, 2))
        await pilot.pause()
        assert opened == [artifact_path.resolve()]
        assert "step-01.png" in str(app.query_one("#evidence").render())


@pytest.mark.anyio
async def test_artifact_outside_run_directory_is_refused(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("not run evidence", encoding="utf-8")
    opened = []
    state = TuiState(
        steps=[("01", "Capture page", "PASS")],
        artifacts=[ArtifactItem("01", "text", "outside.txt", outside)],
    )
    app = HoloQATui(
        state=state, run_dir=str(tmp_path / "run"), file_opener=opened.append,
    )
    async with app.run_test() as pilot:
        app.action_open_artifact(0)
        await pilot.pause()
        assert not opened
        assert "outside the run directory" in app.state.safety


@pytest.mark.anyio
async def test_assertion_selection_shows_structured_detail() -> None:
    state = TuiState(
        steps=[("01", "Check heading", "FAIL")],
        assertions={
            "01": [AssertionItem("01", "text", False, "Expected Welcome, got Sign in")]
        },
    )
    app = HoloQATui(state=state)
    async with app.run_test() as pilot:
        await pilot.press("3")
        assert "Expected Welcome" in str(app.query_one("#assertions").render())


@pytest.mark.anyio
async def test_transcript_can_pause_filter_and_search() -> None:
    app = HoloQATui.demo()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.press("p")
        assert app.transcript_paused is True
        await pilot.press("v")
        assert app.transcript_filter == "holoqa"
        await pilot.press("/")
        search = app.query_one("#transcript-search", Input)
        assert search.display is True
        await pilot.press("a", "v", "a", "t", "a", "r")
        await pilot.pause()
        assert app.transcript_query == "avatar"
        assert all("avatar" in entry.message.lower() for entry in app._visible_transcript_entries())


@pytest.mark.anyio
async def test_provider_step_event_links_transcript_to_step() -> None:
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        app._handle_agent_line(
            "stdout",
            '{"type":"item.started","item":{"type":"mcp_tool_call",'
            '"tool":"holoqa_observe","arguments":{"step_id":"02","kind":"dom"}}}',
        )
        await pilot.pause()
        assert app._transcript_entries[-1].step_id == "02"
        assert app.selected_step_id == "02"


@pytest.mark.anyio
async def test_header_actions_and_help_are_discoverable() -> None:
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        shortcuts = app.query_one("#shortcuts").render()
        assert any(
            span.style.meta and "@click" in span.style.meta
            for span in shortcuts.spans
        )
        await pilot.press("?")
        overlay = app.query_one("#overlay")
        assert overlay.display is True
        assert "Keyboard & mouse" in str(overlay.render())
        await pilot.press("escape")
        assert overlay.display is False


@pytest.mark.anyio
async def test_verdict_menu_exposes_run_actions() -> None:
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        app.action_show_run_menu()
        await pilot.pause()
        rendered = str(app.query_one("#overlay").render())
        assert "Open run directory" in rendered
        assert "Copy run summary" in rendered
        assert "Open raw events" in rendered


@pytest.mark.anyio
async def test_demo_controls_update_the_safety_banner() -> None:
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        await pilot.press("c")
        assert "cancel" in str(app.query_one("#safety").render()).lower()
        await pilot.press("q")


@pytest.mark.anyio
async def test_copy_log_writes_a_plain_text_debug_file(tmp_path, monkeypatch) -> None:
    app = HoloQATui.demo()
    app.log_path = tmp_path / "agent-log.txt"
    async with app.run_test() as pilot:
        await pilot.press("y")
        copied = app.log_path.read_text(encoding="utf-8")
        assert "10:14:01  holoqa_observe" in copied
        assert "Navigating to /login" in copied
        assert "Assertion pending" in copied
        assert "Log copied to" in app.state.safety


@pytest.mark.anyio
async def test_copy_log_handles_unicode_windows_output(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return type("Completed", (), {"returncode": 0})()

    # The platform is passed in rather than patched onto `os.name`. Patching the
    # global makes `pathlib` build WindowsPath on Linux, which raises from inside
    # pytest's own failure reporting — one assertion became an INTERNALERROR that
    # aborted the run. `clip` is still stubbed because the point here is the
    # Unicode encoding of stdin, not whether Windows is present.
    real_copy_text = HoloQATui._copy_text
    monkeypatch.setattr(
        HoloQATui, "_copy_text",
        staticmethod(lambda payload, platform=None: real_copy_text(payload, "nt")),
    )
    monkeypatch.setattr(tui_module.shutil, "which", lambda name: "clip.exe")
    monkeypatch.setattr(tui_module.subprocess, "run", fake_run)
    app = HoloQATui.demo()
    app.log_path = tmp_path / "agent-log.txt"
    app._log_lines.append("provider ✓ complete")
    async with app.run_test() as pilot:
        await pilot.press("y")
    copied = captured["input"].decode("utf-8")
    assert copied.startswith("provider ✓ complete\n")
    assert "holoqa_observe" in copied


@pytest.mark.anyio
async def test_real_mode_uses_wrapper_result_without_starting_a_provider(tmp_path, monkeypatch) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "meta: {app: tui-test, base_url: http://127.0.0.1:1}\n"
        "steps:\n  - id: A1\n    title: fixture\n    expect:\n      - screenshot: required\n",
        encoding="utf-8",
    )

    def fake_launch(plan, request, **kwargs):
        assert request.output_callback is not None
        request.output_callback("stdout", "simulated provider event")
        return {
            "agent": {"exit_code": 0, "state": "completed"},
            "holoqa": {
                "run_id": "tui-test-run", "decision": "RELEASE",
                "counts": {"PASS": 1, "FAIL": 0, "BLOCKED": 0},
            },
        }

    monkeypatch.setattr(agent_module, "create_and_launch", fake_launch)
    request = agent_module.AgentRunRequest(provider="codex", cwd=tmp_path)
    app = HoloQATui(plan_path=plan_path, request=request)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert app.state.run_id == "tui-test-run"
        assert app.state.decision == "RELEASE"
        assert "HoloQA decision: RELEASE" in app.state.safety
        assert app.state.activity == "COMPLETE"
        assert any("simulated provider event" in line for line in app._log_lines)


def test_provider_events_are_summarized_for_humans() -> None:
    assert HoloQATui._human_event("stdout", '{"type":"turn.started"}') == "Agent is thinking"
    assert "Running command: uv run pytest" in HoloQATui._human_event(
        "stdout",
        '{"type":"item.started","item":{"type":"command_execution","command":"uv run pytest"}}',
    )


@pytest.mark.anyio
async def test_holoqa_events_update_live_step_and_evidence() -> None:
    app = HoloQATui.demo()
    async with app.run_test() as pilot:
        app._handle_agent_line(
            "stdout",
            '{"type":"item.completed","item":{"type":"mcp_tool_call",'
            '"tool":"holoqa_observe","arguments":{"step_id":"03","kind":"dom"},'
            '"result":{"structured_content":{"file":"step-03-dom.json"}}}}',
        )
        app._handle_agent_line(
            "stdout",
            '{"type":"item.completed","item":{"type":"mcp_tool_call",'
            '"tool":"holoqa_judge","arguments":{"step_id":"03"},'
            '"result":{"structured_content":{"verdict":"PASS"}}}}',
        )
        await pilot.pause()
        assert next(row for row in app.state.steps if row[0] == "03")[2] == "PASS"
        assert "03: step-03-dom.json" in app.state.evidence
    assert "check completed" in HoloQATui._human_event(
        "stdout",
        '{"type":"item.completed","item":{"type":"mcp_tool_call","name":"check"}}',
    )


@pytest.mark.anyio
async def test_provider_startup_error_stays_visible_and_copyable(tmp_path, monkeypatch) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "meta: {app: tui-error, base_url: http://127.0.0.1:1}\n"
        "steps:\n  - id: A1\n    title: fixture\n    expect:\n      - screenshot: required\n",
        encoding="utf-8",
    )

    calls = 0

    def failed_then_succeeds(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated provider startup failure")
        return {
            "agent": {"exit_code": 0, "state": "completed"},
            "holoqa": {
                "run_id": "retry-run", "decision": "HOLD",
                "counts": {"PASS": 0, "FAIL": 0, "BLOCKED": 0},
            },
        }

    monkeypatch.setattr(agent_module, "create_and_launch", failed_then_succeeds)
    app = HoloQATui(
        plan_path=plan_path,
        request=agent_module.AgentRunRequest(provider="codex", cwd=tmp_path),
        run_dir=str(tmp_path / "run"),
    )
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert "simulated provider startup failure" in app.state.safety
        await pilot.press("y")
        assert "ERROR: wrapper:" in (tmp_path / "run" / "out" / "agent-log.txt").read_text(encoding="utf-8")
        assert app.state.activity == "ERROR"
        app.action_retry()
        await pilot.pause(0.2)
        assert app.state.activity == "COMPLETE"
        assert app.state.run_id == "retry-run"
