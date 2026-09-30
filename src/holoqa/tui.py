"""Terminal UI for HoloQA agent runs.

The UI is deliberately a client of :mod:`holoqa.agent`: it displays provider
events, while HoloQA remains the only source of verdicts and release decisions.
"""

from __future__ import annotations

import threading
import shutil
import subprocess
import tempfile
import os
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Input, OptionList, RichLog, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option
from rich.text import Text
from rich.style import Style

from holoqa import agent as agent_module
from holoqa import plan as plan_module
from holoqa import run as run_module


def _theme_config_path() -> Path:
    configured = os.environ.get("HOLOQA_TUI_CONFIG", "").strip()
    return Path(configured) if configured else Path.home() / ".holoqa" / "tui.json"


def _load_theme_preference() -> str | None:
    try:
        data = json.loads(_theme_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    value = data.get("theme") if isinstance(data, dict) else None
    return str(value) if value else None


def _save_theme_preference(theme_name: str) -> None:
    path = _theme_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"theme": theme_name}, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Theme persistence is a convenience; a read-only home directory must
        # never prevent the TUI from starting or changing themes in memory.
        pass


@dataclass(frozen=True)
class ArtifactItem:
    """An evidence artifact associated with one HoloQA step."""

    step_id: str
    kind: str
    name: str
    path: Path | None = None
    captured_at: str = ""
    size: int = 0


@dataclass(frozen=True)
class AssertionItem:
    """A human-readable assertion result associated with a step."""

    step_id: str
    kind: str
    passed: bool | None
    detail: str


@dataclass(frozen=True)
class TranscriptEntry:
    timestamp: str
    source: str
    message: str
    step_id: str | None = None


@dataclass
class TuiState:
    title: str = "HoloQA agent run"
    provider: str = "-"
    mode: str = "supervised"
    run_id: str = "not started"
    decision: str = "HOLD"
    safety: str = "Ready - HoloQA is authoritative"
    activity: str = "IDLE"
    activity_detail: str = "Waiting to start"
    steps: list[tuple[str, str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    artifacts: list[ArtifactItem] = field(default_factory=list)
    assertions: dict[str, list[AssertionItem]] = field(default_factory=dict)
    groups: dict[str, str] = field(default_factory=dict)
    step_groups: dict[str, str] = field(default_factory=dict)


def _demo_state() -> TuiState:
    return TuiState(
        title="web-app-smoke",
        provider="codex",
        mode="unattended",
        run_id="demo-20260920",
        decision="HOLD",
        safety="Demo mode - no provider launched",
        activity="RUNNING",
        activity_detail="current step in progress",
        steps=[
            ("01", "Open home page", "PASS"),
            ("02", "Check hero content", "PASS"),
            ("03", "Sign in with valid user", "IN PROGRESS"),
            ("04", "Verify dashboard loads", "PENDING"),
            ("05", "Create a new project", "PENDING"),
            ("06", "Verify project appears", "PENDING"),
            ("07", "Check settings page", "PENDING"),
            ("08", "Sign out", "PENDING"),
        ],
        evidence=["03: sign-in.png"],
        artifacts=[ArtifactItem("03", "screenshot", "sign-in.png")],
        assertions={
            "03": [AssertionItem("03", "visible text", None, 'Expect "Welcome back"')],
        },
        groups={"FLOW": "Purchase Flow", "ACCOUNT": "Account"},
        step_groups={
            "01": "FLOW", "02": "FLOW", "03": "FLOW", "04": "FLOW",
            "05": "FLOW", "06": "FLOW", "07": "ACCOUNT", "08": "ACCOUNT",
        },
    )


def _animated_demo_state() -> TuiState:
    """A realistic holoshop.shop playback plan with four grouped stages."""
    steps = [
        ("D01", "Open Holoshop storefront", "PENDING"),
        ("D02", "Verify campaign hero content", "PENDING"),
        ("P01", "Open featured product detail", "PENDING"),
        ("P02", "Verify product title and gallery", "PENDING"),
        ("C01", "Add a product to the cart", "PENDING"),
        ("C02", "Verify cart summary and quantity", "PENDING"),
        ("A01", "Open account entry points", "PENDING"),
        ("A02", "Return to storefront safely", "PENDING"),
    ]
    step_groups = {
        "D01": "DISCOVERY", "D02": "DISCOVERY",
        "P01": "PRODUCT", "P02": "PRODUCT",
        "C01": "CART", "C02": "CART",
        "A01": "ACCOUNT", "A02": "ACCOUNT",
    }
    return TuiState(
        title="holoshop.shop smoke",
        provider="codex",
        mode="demo playback",
        run_id="demo-holoshop",
        decision="HOLD",
        safety="Animated demo - no provider launched",
        activity="STARTING",
        activity_detail="Preparing local Holoshop evidence",
        steps=steps,
        groups={
            "DISCOVERY": "Discovery",
            "PRODUCT": "Product Detail",
            "CART": "Cart Journey",
            "ACCOUNT": "Account & Session",
        },
        step_groups=step_groups,
    )


class HoloQATui(App[None]):
    """Interactive dashboard for a real or simulated HoloQA agent run."""

    TITLE = "HoloQA Agent Console"
    CSS = """
    Screen {
        background: $background;
        color: $text;
        link-style: not underline;
        link-style-hover: bold not underline;
    }
    #shortcuts, #step-table, #verdict-title, #evidence, #assertions,
    #agent-log, #overlay {
        link-style: not underline;
        link-style-hover: bold not underline;
    }
    #topbar { height: 4; padding: 1 2; background: $surface; border-bottom: solid $panel; }
    #brand { width: 10; color: $accent; text-style: bold; }
    #shortcuts { width: 74; color: $text-muted; }
    #meta { width: 1fr; color: $text-muted; }
    #elapsed { width: 20; color: $text-muted; text-align: right; }
    #body { height: 1fr; }
    #left { width: 26%; min-width: 28; border-right: solid $panel; }
    #steps-header { height: 4; padding: 1 2; border-bottom: solid $panel; }
    #run-title { display: none; }
    #step-scroll { height: 1fr; overflow-y: auto; scrollbar-size: 1 1; }
    #step-table { height: auto; min-height: 1fr; padding: 0; }
    .step-row { height: 5; padding: 1 2; color: #afbdca; }
    .step-pass { color: #cbd7e2; }
    .step-current { background: #132a3a; color: #46e5f0; border-left: thick #46e5f0; }
    #workspace { width: 74%; }
    #run-header { height: 4; padding: 1 2; border-bottom: solid $panel; }
    #activity { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #step-clock { width: auto; height: 1; color: $text-muted; }
    #upper { height: 58%; padding: 1 2 2 2; border-bottom: solid $panel; }
    #transcript-panel { width: 1fr; }
    #agent-log { height: 1fr; background: $background; padding: 0; scrollbar-size: 1 1; }
    #transcript-search { height: 3; border: round $panel; background: $surface; color: $text; }
    .hidden { display: none; }
    #verdict-card { width: 28; min-width: 22; margin-left: 2; border: round $panel; }
    #verdict-title { height: 4; padding: 1 2; border-bottom: solid $panel; color: $text; }
    #decision { height: 4; padding: 1 2; color: $warning; text-style: bold; }
    #verdict-counts { height: auto; padding: 1 2; border-top: solid $panel; color: $text-muted; }
    #progress { height: auto; padding: 1 2; border-top: solid $panel; color: $text; }
    #detail-panel { height: 42%; padding: 0 2; }
    #detail-tabs { height: 1fr; background: $background; }
    #detail-tabs Tabs { height: 3; background: $background; border-bottom: solid $panel; }
    #detail-tabs Tab { color: $text-muted; background: $background; padding: 0 2; }
    #detail-tabs Tab.-active { color: $accent; text-style: bold; }
    #detail-tabs ContentSwitcher { background: $background; }
    #detail-tabs TabPane { padding: 1 0; }
    #detail { color: $text; }
    #evidence, #assertions { color: $text-muted; }
    #safety { width: 1fr; height: 1; color: $text-muted; }
    #version { width: auto; height: 1; color: $text-muted; }
    #statusbar { height: 4; padding: 1 2; background: $surface; border-top: solid $panel; color: $text-muted; }
    #overlay { layer: overlay; dock: top; width: 64; height: auto; max-height: 24; margin: 5 8; padding: 1 2; background: $surface; border: round $accent; color: $text; }
    #theme-menu { layer: overlay; dock: top; width: 42; height: auto; max-height: 18; margin: 5 8; padding: 1; background: $surface; border: round $accent; color: $text; overflow-y: auto; scrollbar-size: 1 1; }
    .compact #topbar { padding: 1 1; }
    .compact #brand { width: 9; }
    .compact #shortcuts { width: 1fr; }
    .compact #meta { display: none; }
    .compact #elapsed { width: 18; }
    .compact #left { width: 28; min-width: 24; }
    .compact #workspace { width: 1fr; }
    .compact #steps-header { height: 3; padding: 0 1; }
    .compact #run-header { height: 3; padding: 0 1; }
    .compact #upper { height: 5fr; padding: 0 1; }
    .compact #detail-panel { height: 2fr; padding: 0 1; }
    .compact #verdict-card { width: 24; min-width: 20; margin-left: 1; }
    .compact #verdict-title { height: 3; padding: 0 1; }
    .compact #decision { height: 2; padding: 0 1; }
    .compact #verdict-counts { height: 5; padding: 0 1; }
    .compact #progress { height: 4; padding: 0 1; }
    .compact #detail-tabs TabPane { padding: 0; }
    .compact #statusbar { height: 3; padding: 0 1; }
    """
    BINDINGS = [
        ("c", "cancel", "Cancel"),
        ("y", "copy_log", "Copy log"),
        ("r", "refresh", "Refresh"),
        ("1", "show_details", "Details"),
        ("2", "show_evidence", "Evidence"),
        ("3", "show_assertions", "Assertions"),
        Binding("up", "cursor_up", "Previous item", priority=True),
        Binding("down", "cursor_down", "Next item", priority=True),
        Binding("f", "follow_live", "Follow live", priority=True),
        Binding("enter", "activate_selected", "Open", priority=True),
        Binding("ctrl+p", "show_theme_menu", "Themes", priority=True),
        ("o", "open_selected", "Open artifact"),
        ("x", "copy_artifact_path", "Copy artifact path"),
        ("e", "reveal_artifact", "Reveal artifact"),
        Binding("p", "pause_transcript", "Pause transcript", priority=True),
        Binding("v", "cycle_transcript_filter", "Filter transcript", priority=True),
        Binding("/", "search_transcript", "Search transcript", priority=True),
        Binding("escape", "hide_search", "Close search", priority=True),
        Binding("?", "show_help", "Help", priority=True),
        ("m", "show_run_menu", "Run menu"),
        ("shift+y", "copy_last_event", "Copy event"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        state: TuiState | None = None,
        demo: bool = False,
        animated: bool = False,
        plan_path: Path | None = None,
        request: agent_module.AgentRunRequest | None = None,
        run_dir: str = "",
        tag: str = "",
        commit: str = "",
        tester: str = "",
        base_url: str = "",
        resume: bool = False,
        file_opener: Callable[[Path], None] | None = None,
        path_revealer: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__()
        self.demo_mode = demo
        self.animated_demo = animated
        self.state = state or (_animated_demo_state() if animated else (_demo_state() if demo else TuiState()))
        self.plan_path = plan_path
        self.request = request
        self.run_dir = run_dir
        self.resume = resume
        self.tag = tag
        self.commit = commit
        self.tester = tester
        self.base_url = base_url
        self._file_opener = file_opener or self._open_path
        self._path_revealer = path_revealer or self._reveal_path
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._log_lines: list[str] = []
        self._transcript_entries: list[TranscriptEntry] = []
        self.transcript_paused = False
        self.transcript_filter = "all"
        self.transcript_query = ""
        self.log_path: Path | None = None
        self._started_monotonic: float | None = (
            time.monotonic() - 207 if demo and not animated else (time.monotonic() if animated else None)
        )
        self._last_event_monotonic: float | None = (
            time.monotonic() - 12.4 if demo and not animated else (time.monotonic() if animated else None)
        )
        self._last_error = ""
        self.selected_step_id: str | None = None
        self.selected_artifact_index = 0
        self.selected_assertion_index = 0
        self.collapsed_groups: set[str] = set()
        self.follow_live = True
        self._animated_index = 0
        self._animated_phase = 0
        self._animated_timer = None
        self._theme_menu_open = False
        self._theme_before_picker = "textual-dark"
        # Keep the complete Textual catalog available. The picker is bounded
        # and scrollable, so adding a theme never silently removes another.
        self._theme_names = tuple(self.available_themes)
        saved_theme = _load_theme_preference()
        if saved_theme in self._theme_names:
            self.theme = saved_theme
        self._theme_before_picker = self.theme

    @classmethod
    def demo(cls, *, animated: bool = False) -> "HoloQATui":
        return cls(demo=True, animated=animated)

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("HoloQA", id="brand")
            yield Static(id="shortcuts")
            yield Static(id="meta")
            yield Static(id="elapsed")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Static(id="steps-header")
                yield Static(id="run-title")
                with VerticalScroll(id="step-scroll"):
                    yield Static(id="step-table")
            with Vertical(id="workspace"):
                with Horizontal(id="run-header"):
                    yield Static(id="activity")
                    yield Static(id="step-clock")
                with Horizontal(id="upper"):
                    with Vertical(id="transcript-panel"):
                        yield Input(
                            placeholder="Search transcript…", id="transcript-search",
                            classes="hidden", disabled=True,
                        )
                        yield RichLog(id="agent-log", markup=False, wrap=True)
                    with Vertical(id="verdict-card"):
                        yield Static("Run Verdict                                      ···", id="verdict-title")
                        yield Static(id="decision")
                        yield Static(id="verdict-counts")
                        yield Static(id="progress")
                with Vertical(id="detail-panel"):
                    with TabbedContent(initial="tab-details", id="detail-tabs"):
                        with TabPane("Step Details", id="tab-details"):
                            yield Static(id="detail")
                        with TabPane("Evidence", id="tab-evidence"):
                            yield Static(id="evidence")
                        with TabPane("Assertions", id="tab-assertions"):
                            yield Static(id="assertions")
        with Horizontal(id="statusbar"):
            yield Static(id="safety")
            yield Static("holoqa", id="version")
        yield Static(id="overlay", classes="hidden")
        yield OptionList(id="theme-menu", classes="hidden")

    def on_mount(self) -> None:
        self._set_compact_mode(self.size.width < 150 or self.size.height < 45)
        self.selected_step_id = self._active_step_id() or (
            self.state.steps[0][0] if self.state.steps else None
        )
        self._render_state()
        self.set_interval(1, self._refresh_activity)
        if self.demo_mode:
            if self.animated_demo:
                self._start_animated_demo()
            else:
                self._append_demo_transcript()
        if not self.demo_mode and self.plan_path and self.request:
            self._start_agent()

    def on_resize(self, event: events.Resize) -> None:
        self._set_compact_mode(event.size.width < 150 or event.size.height < 45)

    def _set_compact_mode(self, compact: bool) -> None:
        changed = compact != self.has_class("compact")
        self.set_class(compact, "compact")
        if changed and self.is_mounted:
            self._render_state()

    def _render_state(self) -> None:
        self._render_topbar()
        self.query_one("#run-title", Static).update(
            f"{self.state.title}\n{self.state.provider} / {self.state.mode} / {self.state.run_id}"
        )
        status = Text()
        status.append(f"{self.state.activity.title()}...", style="#46e5f0")
        status.append(f"    {self.state.safety}", style="#8299ad")
        if self.state.activity == "ERROR":
            status.append("    [details]", style=Style(color="#ef6464", underline=False, meta={"@click": "app.show_error"}))
        self.query_one("#safety", Static).update(status)
        self._render_activity()
        counts = self._counts()
        decision_icon = "⏸" if self.state.decision == "HOLD" else "●"
        decision_color = "#f5bd5a" if self.state.decision == "HOLD" else "#62d77b"
        self.query_one("#decision", Static).update(
            f"[bold {decision_color}]{decision_icon}  {self.state.decision}[/]"
        )
        self._render_verdict(counts)

    def _render_topbar(self) -> None:
        shortcuts = Text()
        for key, label, action in (
            ("y", "copy", "copy_log"), ("p", "pause", "pause_transcript"),
            ("c", "cancel", "cancel"), ("r", "refresh", "refresh"),
            ("^p", "theme", "show_theme_menu"),
            ("?", "help", "show_help"), ("q", "quit", "quit"),
        ):
            click = Style(underline=False, meta={"@click": f"app.{action}"})
            start = len(shortcuts)
            shortcuts.append(f"[{key}]", style="#46e5f0")
            shortcuts.append(f" {label}  ", style="#b7c6d3")
            shortcuts.stylize(click, start, len(shortcuts))
        self.query_one("#shortcuts", Static).update(shortcuts)
        self.query_one("#meta", Static).update(
            f"plan: {self.state.title}  |  provider: {self.state.provider}  |  mode: {self.state.mode}"
        )
        self.query_one("#elapsed", Static).update(
            f"elapsed: {self._elapsed_seconds()}"
        )

    def _render_verdict(self, counts: dict[str, int]) -> None:
        verdict_title = Text("Run Verdict")
        verdict_title.append("                         ")
        verdict_title.append("···", style=Style(color="#8299ad", underline=False, meta={"@click": "app.show_run_menu"}))
        self.query_one("#verdict-title", Static).update(verdict_title)
        compact = self.has_class("compact")
        separator = "\n" if compact else "\n\n"
        self.query_one("#verdict-counts", Static).update(
            f"[bold #62d77b]{counts['PASS']:>2}[/]    [#62d77b]PASS[/]{separator}"
            f"[bold #ef6464]{counts['FAIL']:>2}[/]    [#ef6464]FAIL[/]{separator}"
            f"[bold #f5bd5a]{counts['BLOCKED']:>2}[/]    BLOCKED{separator}"
            f"[bold #8da4b8]{counts['PENDING']:>2}[/]    PENDING"
        )
        done = counts["PASS"] + counts["FAIL"] + counts["BLOCKED"]
        total = len(self.state.steps)
        bar_width = 12 if compact else 16
        filled = round(bar_width * done / total) if total else 0
        bar = "━" * filled + "─" * (bar_width - filled)
        progress_label = f"Progress  {done}/{total or 0}" if compact else f"Progress                         {done}/{total or 0}"
        estimate_label = self._estimated_remaining() if compact else f"Est. remaining: {self._estimated_remaining()}"
        self.query_one("#progress", Static).update(
            f"{progress_label}\n[#46e5f0]{bar[:filled]}[/][#203543]{bar[filled:]}[/]\n{estimate_label}"
        )
        self.query_one("#steps-header", Static).update(
            f"[bold #e8eef4]Steps[/]    [#aab9c6]{done}/{total or 0}[/]"
        )
        self._render_steps()
        detail = next(
            (row for row in self.state.steps if row[0] == self.selected_step_id),
            None,
        )
        if detail is None:
            detail = next((row for row in self.state.steps if row[2] == "IN PROGRESS"), None)
        detail = detail or (self.state.steps[-1] if self.state.steps else None)
        if detail:
            self.query_one("#detail", Static).update(
                f"[bold #e8eef4]Step {detail[0]} — {detail[1]}[/]\n\n"
                f"[#8299ad]Status[/]      [bold #46e5f0]{detail[2]}[/]\n"
                f"[#8299ad]Run ID[/]      {self.state.run_id}\n"
                f"[#8299ad]Provider[/]    {self.state.provider}\n"
                f"[#8299ad]Authority[/]   HoloQA verdict engine"
            )
            self._render_artifacts(detail[0])
            self._render_assertions(detail[0], detail[2])
        else:
            self.query_one("#detail", Static).update("Waiting for the first step…")
            self.query_one("#evidence", Static).update("No evidence captured yet.")
            self.query_one("#assertions", Static).update("No assertions evaluated yet.")

    def _artifacts_for_step(self, step_id: str | None = None) -> list[tuple[int, ArtifactItem]]:
        step_id = step_id or self.selected_step_id
        return [
            (index, item) for index, item in enumerate(self.state.artifacts)
            if item.step_id == step_id
        ]

    def _render_artifacts(self, step_id: str) -> None:
        artifacts = self._artifacts_for_step(step_id)
        if not artifacts:
            legacy = [item for item in self.state.evidence if item.startswith(f"{step_id}:")]
            self.query_one("#evidence", Static).update(
                "No evidence captured for this step." if not legacy else "\n".join(legacy)
            )
            return
        valid_indices = {index for index, _ in artifacts}
        if self.selected_artifact_index not in valid_indices:
            self.selected_artifact_index = artifacts[0][0]
        output = Text()
        output.append("Captured evidence\n\n", style="bold #e8eef4")
        for index, item in artifacts:
            selected = index == self.selected_artifact_index
            marker = "›" if selected else " "
            start = len(output)
            output.append(
                f"{marker} {item.step_id}: {item.name}  [{item.kind}]\n",
                style="#46e5f0 on #132a3a" if selected else "#c8d3df",
            )
            output.stylize(Style(underline=False, meta={"@click": f"app.open_artifact({index})"}), start, len(output))
        item = self.state.artifacts[self.selected_artifact_index]
        output.append("\nEnter/click open  x copy path  e reveal\n", style="#8299ad")
        if item.path:
            output.append(str(item.path), style="#8299ad")
        self.query_one("#evidence", Static).update(output)

    def _render_assertions(self, step_id: str, verdict: str) -> None:
        assertions = self.state.assertions.get(step_id, [])
        if not assertions:
            self.query_one("#assertions", Static).update(
                f"[bold #e8eef4]Assertion status[/]\n\n"
                f"Step {step_id} is {verdict}.\n"
                "Detailed assertion results appear after HoloQA judges the step."
            )
            return
        self.selected_assertion_index = min(self.selected_assertion_index, len(assertions) - 1)
        output = Text()
        output.append("Assertions\n\n", style="bold #e8eef4")
        for index, item in enumerate(assertions):
            icon = "✓" if item.passed is True else ("×" if item.passed is False else "○")
            color = "#62d77b" if item.passed is True else ("#ef6464" if item.passed is False else "#f5bd5a")
            selected = index == self.selected_assertion_index
            start = len(output)
            output.append(
                f"{'›' if selected else ' '} {icon} {item.kind}\n",
                style=f"{color}{' on #132a3a' if selected else ''}",
            )
            output.stylize(Style(underline=False, meta={"@click": f"app.select_assertion({index})"}), start, len(output))
        current = assertions[self.selected_assertion_index]
        output.append(f"\n{current.detail}", style="#c8d3df")
        self.query_one("#assertions", Static).update(output)

    def _elapsed_seconds(self) -> str:
        seconds = int(time.monotonic() - self._started_monotonic) if self._started_monotonic else 0
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _estimated_remaining(self) -> str:
        if self.demo_mode:
            return "~00:02:18"
        counts = self._counts()
        done = counts["PASS"] + counts["FAIL"] + counts["BLOCKED"]
        remaining = len(self.state.steps) - done
        if not self._started_monotonic or not done or remaining <= 0:
            return "--:--:--"
        estimate = int((time.monotonic() - self._started_monotonic) / done * remaining)
        hours, remainder = divmod(max(0, estimate), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"~{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _render_steps(self) -> None:
        output = Text(no_wrap=True)
        last_group = object()
        for index, (step_id, title, verdict) in enumerate(self.state.steps):
            group_id = self.state.step_groups.get(step_id, "")
            if group_id != last_group:
                if output:
                    output.append("\n")
                if group_id:
                    group_title = self.state.groups.get(group_id, group_id)
                    members = [row for row in self.state.steps if self.state.step_groups.get(row[0]) == group_id]
                    done = sum(row[2] in {"PASS", "FAIL", "BLOCKED"} for row in members)
                    start = len(output)
                    output.append(f"> {group_title}  {done}/{len(members)}\n", style="bold #8299ad")
                    output.stylize(
                        Style(underline=False, meta={"@click": f"app.toggle_group('{group_id}')"}),
                        start, len(output),
                    )
                last_group = group_id
            if group_id in self.collapsed_groups:
                continue
            if self.has_class("compact"):
                title = self._short(title, 18)
            active = verdict == "IN PROGRESS"
            selected = step_id == self.selected_step_id
            marker = {"PASS": "●", "FAIL": "×", "BLOCKED": "!", "IN PROGRESS": "→"}.get(verdict, "○")
            if selected and not active:
                marker = "›"
            color = {
                "PASS": "#62d77b", "FAIL": "#ef6464", "BLOCKED": "#f5bd5a",
                "IN PROGRESS": "#46e5f0", "PENDING": "#718ba0",
            }.get(verdict, "#718ba0")
            row_style = "on #1d3b4c" if selected else ("on #132a3a" if active else "")
            click = Style(underline=False, meta={"@click": f"app.select_step('{step_id}')"})
            first = f" {marker}  {step_id:<3} {title}"
            second = f"       {verdict}"
            start = len(output)
            output.append(first.ljust(48), style=f"{color} {row_style}".strip())
            output.append("\n")
            output.append(second.ljust(48), style=f"{color} {row_style}".strip())
            output.stylize(click, start, len(output))
            if index < len(self.state.steps) - 1:
                output.append("\n\n")
        self.query_one("#step-table", Static).update(output)

    def _active_step_id(self) -> str | None:
        return next((step_id for step_id, _, verdict in self.state.steps if verdict == "IN PROGRESS"), None)

    def _select_step(self, step_id: str, *, user: bool) -> None:
        if not any(row[0] == step_id for row in self.state.steps):
            return
        self.selected_step_id = step_id
        if user:
            self.follow_live = False
        self._render_state()

    def _counts(self) -> dict[str, int]:
        counts = {"PASS": 0, "FAIL": 0, "BLOCKED": 0, "PENDING": 0}
        for _, _, verdict in self.state.steps:
            key = "PENDING" if verdict in {"PENDING", "IN PROGRESS"} else verdict
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _append_log(
        self, text: str, *, source: str = "agent", timestamp: str | None = None,
        step_id: str | None = None,
    ) -> None:
        timestamp = timestamp or datetime.now().strftime("%H:%M:%S")
        copied = f"{timestamp}  {source:<16}  {text}"
        self._log_lines.append(copied)
        self._transcript_entries.append(TranscriptEntry(timestamp, source, text, step_id))
        self._render_transcript()

    def _visible_transcript_entries(self) -> list[TranscriptEntry]:
        query = self.transcript_query.casefold().strip()
        entries = self._transcript_entries
        if self.transcript_filter == "holoqa":
            entries = [entry for entry in entries if entry.source.startswith("holoqa")]
        elif self.transcript_filter == "agent":
            entries = [entry for entry in entries if not entry.source.startswith("holoqa") and entry.source != "error"]
        elif self.transcript_filter == "errors":
            entries = [entry for entry in entries if entry.source == "error" or entry.message.startswith("ERROR:")]
        if query:
            entries = [
                entry for entry in entries
                if query in f"{entry.source} {entry.message}".casefold()
            ]
        return entries

    def _render_transcript(self) -> None:
        if not self.is_mounted:
            return
        log = self.query_one("#agent-log", RichLog)
        log.clear()
        for entry in self._visible_transcript_entries():
            rendered = Text()
            rendered.append(entry.timestamp, style="#8299ad")
            rendered.append("  ")
            rendered.append(f"{entry.source:<16}", style="#46e5f0")
            rendered.append("  ")
            rendered.append(
                entry.message,
                style="#ef6464" if entry.message.startswith("ERROR:") else "#c8d3df",
            )
            if entry.step_id:
                rendered.stylize(
                    Style(underline=False, meta={"@click": f"app.select_step('{entry.step_id}')"}),
                    0, len(rendered),
                )
            log.write(rendered, scroll_end=not self.transcript_paused)

    def _append_demo_transcript(self) -> None:
        lines = [
            ("10:14:01", "holoqa_observe", "Navigating to /login"),
            ("10:14:03", "holoqa_observe", "Page loaded (200) in 842ms"),
            ("10:14:04", "holoqa_observe", 'Filling input "email" → demo@acme.test'),
            ("10:14:05", "holoqa_observe", 'Filling input "password" → ********'),
            ("10:14:06", "holoqa_observe", 'Clicking button "Sign in"'),
            ("10:14:08", "holoqa_observe", "Waiting for navigation..."),
            ("10:14:10", "holoqa_observe", "URL changed to /app"),
            ("10:14:10", "holoqa_observe", "Checking for user avatar..."),
            ("10:14:11", "holoqa_judge", 'Expect visible text "Welcome back"'),
            ("10:14:12", "holoqa_judge", "Assertion pending..."),
        ]
        for timestamp, source, message in lines:
            self._append_log(message, source=source, timestamp=timestamp)

    def _start_animated_demo(self) -> None:
        self._prepare_animated_demo_run()
        self.state.activity = "STARTING"
        self.state.activity_detail = "Loading captured Holoshop fixtures"
        self._append_log(
            "Animated playback loaded from local Holoshop captures",
            source="holoqa_demo",
        )
        self._animated_timer = self.set_interval(1.15, self._advance_animated_demo)
        self._advance_animated_demo()

    def _prepare_animated_demo_run(self) -> None:
        asset_dir = Path(__file__).parent / "assets" / "demo"
        self.run_dir = tempfile.mkdtemp(prefix="holoqa-holoshop-demo-")
        root = Path(self.run_dir)
        evidence_dir = root / "evidence"
        out_dir = root / "out"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._animated_artifact_sources = {
            "D01": ("screenshot", "holoshop-home.png", "storefront-home.png"),
            "D02": ("screenshot", "holoshop-home.png", "campaign-hero.png"),
            "P01": ("screenshot", "holoshop-product.png", "product-detail.png"),
            "P02": ("screenshot", "holoshop-product.png", "product-gallery.png"),
            "C01": ("screenshot", "holoshop-products.png", "cart-add-product.png"),
            "C02": ("screenshot", "holoshop-products.png", "cart-summary.png"),
            "A01": ("screenshot", "holoshop-home.png", "account-entry-points.png"),
            "A02": ("screenshot", "holoshop-home.png", "return-to-storefront.png"),
        }
        for kind, source, target in self._animated_artifact_sources.values():
            source_path = asset_dir / source
            target_path = evidence_dir / target
            if source_path.is_file():
                shutil.copy2(source_path, target_path)
        (out_dir / "report.md").write_text(
            "# Holoshop animated demo\n\n"
            "Synthetic playback using locally retained screenshots captured from "
            "https://www.holoshop.shop/.\n",
            encoding="utf-8",
        )
        (out_dir / agent_module.EVENTS_FILE).write_text(
            "{\"type\":\"demo.playback\",\"provider\":\"codex\"}\n",
            encoding="utf-8",
        )
        self._write_animated_manifest()

    def _write_animated_manifest(self) -> None:
        if not self.run_dir:
            return
        root = Path(self.run_dir)
        steps: dict[str, dict[str, Any]] = {}
        for step_id, title, verdict in self.state.steps:
            observations = [
                {
                    "kind": item.kind,
                    "file": item.name,
                    "bytes": item.size,
                    "captured_at": item.captured_at,
                }
                for item in self.state.artifacts if item.step_id == step_id
            ]
            assertions = [
                {"kind": item.kind, "ok": item.passed, "detail": item.detail}
                for item in self.state.assertions.get(step_id, [])
            ]
            steps[step_id] = {
                "id": step_id,
                "title": title,
                "stage": self.state.step_groups.get(step_id, ""),
                "verdict": None if verdict in {"PENDING", "IN PROGRESS"} else verdict,
                "observations": observations,
                "assertions": assertions,
            }
        (root / "run.json").write_text(
            json.dumps({
                "run_id": self.state.run_id,
                "app": "holoshop.shop",
                "plan_path": "holoshop-demo.plan.yaml",
                "steps": steps,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _advance_animated_demo(self) -> None:
        if not self.animated_demo or self._animated_index >= len(self.state.steps):
            return
        step_id, title, verdict = self.state.steps[self._animated_index]
        if self._animated_phase == 0:
            self.state.activity = "RUNNING"
            self.state.activity_detail = title
            self._last_event_monotonic = time.monotonic()
            self.state.steps = [
                (row_id, row_title, "IN PROGRESS" if row_id == step_id else row_verdict)
                for row_id, row_title, row_verdict in self.state.steps
            ]
            self.selected_step_id = step_id
            self._append_log(
                f"Navigating to {self._animated_route(step_id)}",
                source="holoqa_observe", step_id=step_id,
            )
            self._animated_phase = 1
            self._render_state()
            return

        kind, _, filename = self._animated_artifact_sources[step_id]
        path = Path(self.run_dir) / "evidence" / filename
        item = ArtifactItem(
            step_id, kind, filename, path,
            datetime.now().isoformat(timespec="seconds"), path.stat().st_size if path.exists() else 0,
        )
        self.state.artifacts.append(item)
        self.state.evidence.append(f"{step_id}: {filename}")
        self.state.assertions[step_id] = [
            AssertionItem(step_id, "visual/content", True, self._animated_assertion(step_id))
        ]
        self.state.steps = [
            (row_id, row_title, "PASS" if row_id == step_id else row_verdict)
            for row_id, row_title, row_verdict in self.state.steps
        ]
        self._append_log(
            f"Captured {kind} evidence: {filename}",
            source="holoqa_observe", step_id=step_id,
        )
        self._append_log(
            f"Assertion passed: {self._animated_assertion(step_id)}",
            source="holoqa_judge", step_id=step_id,
        )
        self._animated_index += 1
        self._animated_phase = 0
        if self._animated_index >= len(self.state.steps):
            self.state.activity = "COMPLETE"
            self.state.activity_detail = "All Holoshop demo tasks passed"
            self.state.decision = "RELEASE"
            self.state.safety = "Animated demo complete - HoloQA RELEASE"
            if self._animated_timer is not None:
                self._animated_timer.pause()
        else:
            self.selected_step_id = self.state.steps[self._animated_index][0]
        self._write_animated_manifest()
        self._render_state()

    @staticmethod
    def _animated_route(step_id: str) -> str:
        return {
            "D01": "/",
            "D02": "/#featured",
            "P01": "/products/hololive-summerfes-full-graphic-t-shirt",
            "P02": "/products/hololive-summerfes-full-graphic-t-shirt#gallery",
            "C01": "/products",
            "C02": "/cart",
            "A01": "/login",
            "A02": "/",
        }.get(step_id, "/")

    @staticmethod
    def _animated_assertion(step_id: str) -> str:
        return {
            "D01": "Storefront loads with HOLOSHOP navigation",
            "D02": "Featured campaign heading is visible",
            "P01": "Product detail page opens successfully",
            "P02": "Product title and image gallery are present",
            "C01": "Featured product can be selected for cart flow",
            "C02": "Cart route is reachable without a crash",
            "A01": "Login and registration entry points are visible",
            "A02": "User can return to the storefront",
        }.get(step_id, "Expected Holoshop content is visible")

    @staticmethod
    def _short(value: object, limit: int = 180) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @classmethod
    def _human_event(cls, channel: str, line: str) -> str:
        """Turn provider protocol JSON into a compact, user-facing transcript.

        Two very different wire formats arrive here. Codex emits
        ``item.started`` / ``item.completed`` with a nested ``item``. Claude
        Code's ``--output-format stream-json`` emits ``assistant`` / ``user`` /
        ``system`` / ``result`` envelopes carrying content blocks. Only the
        first was understood, so a Claude run rendered every single line as
        "Provider event: assistant" — technically true and useless to watch.
        """
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return f"Provider: {cls._short(line)}" if channel == "stderr" else cls._short(line)
        if not isinstance(event, dict):
            return cls._short(event)
        if event.get("type") == "error":
            return f"ERROR: {cls._short(event.get('message', event))}"

        event_type = event.get("type", "")

        # ---- Claude Code stream-json -------------------------------------
        if event_type in {"assistant", "user", "system", "result"}:
            rendered = cls._claude_event(event)
            if rendered is not None:
                return rendered
        if event_type == "rate_limit_event":
            info = event.get("rate_limit_info")
            if isinstance(info, dict):
                status = info.get("status", "unknown")
                kind = str(info.get("rateLimitType", "")).replace("_", " ")
                return f"Rate limit {status}{f' ({kind})' if kind else ''}"
            return "Rate limit update"

        # ---- Codex item events -------------------------------------------
        simple = {
            "thread.started": "Session started",
            "turn.started": "Agent is thinking",
            "turn.completed": "Agent turn completed",
        }
        if event_type in simple:
            return simple[event_type]
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = item.get("type", "")
        status = item.get("status", event.get("status", ""))
        if item_type == "agent_message":
            return f"Agent: {cls._short(item.get('text', ''))}"
        if item_type == "command_execution":
            command = cls._short(item.get("command", "command"), 100)
            if event_type == "item.started":
                return f"Running command: {command}"
            code = item.get("exit_code")
            outcome = "finished" if status not in {"failed", "declined"} else status
            suffix = f" (exit {code})" if code is not None else ""
            return f"Command {outcome}{suffix}"
        if item_type == "mcp_tool_call":
            tool = cls._short(item.get("name", item.get("tool", "tool")), 100)
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            step_id = arguments.get("step_id")
            if event_type == "item.started":
                return cls._tool_call_text(tool, arguments, step_id)
            return f"{tool} {('failed' if status == 'failed' else 'completed')}"
        return f"Provider event: {event_type or 'update'}"

    @classmethod
    def _tool_call_text(cls, tool: str, arguments: dict, step_id: object) -> str:
        """One readable line for a tool invocation, HoloQA's tools first."""
        short = tool.rsplit("__", 1)[-1] if "__" in tool else tool
        if short == "holoqa_observe":
            kind = arguments.get("kind", "evidence")
            target = arguments.get("path") or arguments.get("selector") or ""
            where = f" {target}" if target else ""
            return f"Capturing {kind}{where} for step {step_id}"
        if short == "holoqa_judge":
            return f"Evaluating assertions for step {step_id}"
        if short == "holoqa_run_status":
            return "Reading HoloQA run status"
        if short == "holoqa_block":
            return f"Recording BLOCKED for step {step_id}"
        if short == "holoqa_note":
            return f"Annotating step {step_id}"
        if short == "holoqa_run_start":
            return "Starting a HoloQA run"
        if short == "holoqa_run_package":
            return "Packaging the run"
        return f"Calling {short}"

    @classmethod
    def _claude_event(cls, event: dict) -> str | None:
        """Render one Claude stream-json envelope, or ``None`` to fall through."""
        kind = event.get("type")
        if kind == "system":
            subtype = event.get("subtype", "")
            if subtype == "init":
                model = event.get("model") or "unknown model"
                tools = event.get("tools")
                count = f", {len(tools)} tools" if isinstance(tools, list) else ""
                return f"Session started ({model}{count})"
            if subtype:
                return f"Session {subtype.replace('_', ' ')}"
            return "Session update"

        if kind == "result":
            subtype = event.get("subtype", "")
            duration = event.get("duration_ms")
            cost = event.get("total_cost_usd")
            parts = [f"Run {subtype}" if subtype else "Run finished"]
            if isinstance(duration, (int, float)):
                parts.append(f"{duration / 1000:.1f}s")
            if isinstance(cost, (int, float)):
                parts.append(f"${cost:.4f}")
            summary = event.get("result")
            if isinstance(summary, str) and summary.strip():
                parts.append(cls._short(summary))
            return " · ".join(parts)

        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        blocks = message.get("content")
        if not isinstance(blocks, list):
            return None

        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = cls._short(block.get("text", ""))
                if text:
                    return f"Agent: {text}"
            elif block_type == "thinking":
                thought = cls._short(block.get("thinking", ""), 120)
                if thought:
                    return f"Agent is thinking: {thought}"
            elif block_type == "tool_use":
                tool = str(block.get("name", "tool"))
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                return cls._tool_call_text(tool, arguments, arguments.get("step_id"))
            elif block_type == "tool_result":
                text = cls._block_text(block)
                if text:
                    return f"Result: {cls._short(text)}"
                return "Tool result received"
        return None

    @staticmethod
    def _block_text(block: dict) -> str:
        """Flatten a Claude tool_result block's content into plain text."""
        content = block.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return " ".join(part for part in parts if part)
        return ""

    def _render_activity(self) -> None:
        current_index = next(
            (index for index, row in enumerate(self.state.steps) if row[2] == "IN PROGRESS"),
            None,
        )
        if current_index is not None:
            step = self.state.steps[current_index]
            step_label = f"Step {current_index + 1:02d}/{len(self.state.steps):02d}    {step[1]}"
        else:
            step_label = self.state.activity_detail
        color = "#ef6464" if self.state.activity == "ERROR" else "#46e5f0"
        self.query_one("#activity", Static).update(
            f"[bold {color}]●  {self.state.activity}[/]    [#8299ad]{step_label}[/]"
        )
        step_elapsed = (
            time.monotonic() - self._last_event_monotonic
            if self._last_event_monotonic is not None else 0
        )
        self.query_one("#step-clock", Static).update(f"step elapsed: {step_elapsed:0.1f}s")

    def _refresh_activity(self) -> None:
        # The 1-second interval can fire after the app has exited, when the
        # widgets are already gone: `query_one` then raises `NoMatches` from
        # inside the timer, which surfaces as an error on the way out. Exiting is
        # a normal outcome, so a timer tick during teardown is a no-op.
        if not self.is_mounted:
            return
        try:
            self._render_topbar()
            self._render_activity()
            # The interval that already exists for the clock is also the right
            # place to re-read the authoritative run record. Provider protocol is
            # a hint; run.json is what HoloQA actually decided. Without this the
            # step list only updated when the agent stopped, so a finished step
            # showed as PENDING for the whole run.
            self._sync_from_run()
        except NoMatches:
            # A widget was removed between the guard and the query.
            return

    def _sync_from_run(self) -> None:
        """Re-read run.json and reflect it in the step list and verdict card.

        Cheap by design — a small JSON file, once a second — and it makes the
        dashboard correct for any provider, including one whose event stream the
        transcript does not understand. The verdicts it shows are HoloQA's own,
        never a guess made from provider output.
        """
        if not self.run_dir or self.demo_mode:
            return
        try:
            run = run_module.find(self.run_dir)
            data = run.read()
        except (OSError, ValueError, run_module.GuardrailError):
            return

        stored = data.get("steps", {})
        changed = False
        updated: list[tuple[str, str, str]] = []
        for step_id, title, verdict in self.state.steps:
            # A step the agent is actively working on shows as IN PROGRESS until
            # HoloQA records a verdict; run.json is the tiebreaker.
            recorded = stored.get(step_id, {}).get("verdict")
            resolved = recorded or verdict
            updated.append((step_id, title, resolved))
            if resolved != verdict:
                changed = True
        if changed:
            self.state.steps = updated

        counts = {"PASS": 0, "FAIL": 0, "BLOCKED": 0}
        for _sid, _title, verdict in self.state.steps:
            if verdict in counts:
                counts[verdict] += 1
        total = len(self.state.steps)
        done = sum(counts.values())
        # An incomplete run is not releasable, whatever the counts say.
        decision = "HOLD" if done < total else (
            "HOLD" if counts["FAIL"] or counts["BLOCKED"] else "RELEASE"
        )
        if self.state.decision != decision:
            self.state.decision = decision
            changed = True

        # Evidence and assertions, so the detail panes fill in during the run
        # rather than only at the end.
        evidence: list[str] = []
        artifacts: list[ArtifactItem] = []
        assertions: dict[str, list[AssertionItem]] = {}
        for step_id, step in stored.items():
            for item in step.get("observations", []):
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("file", "evidence"))
                evidence.append(f"{step_id}: {filename}")
                artifacts.append(
                    ArtifactItem(
                        str(step_id), str(item.get("kind", "evidence")), filename,
                        Path(self.run_dir) / "evidence" / filename,
                        str(item.get("captured_at", "")), int(item.get("bytes", 0) or 0),
                    )
                )
            if step.get("assertions"):
                assertions[str(step_id)] = [
                    AssertionItem(
                        str(step_id), str(item.get("kind", "assertion")),
                        item.get("ok") if item.get("ok") in {True, False, None} else None,
                        str(item.get("detail", "No detail supplied")),
                    )
                    for item in step["assertions"] if isinstance(item, dict)
                ]
        if len(evidence) != len(self.state.evidence):
            self.state.evidence = evidence
            changed = True
        if len(artifacts) != len(self.state.artifacts):
            self.state.artifacts = artifacts
            changed = True
        if assertions:
            self.state.assertions = assertions
            changed = True

        if changed and self.is_mounted:
            self._render_state()

    def _handle_agent_line(self, channel: str, line: str) -> None:
        summary = self._human_event(channel, line)
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            event = {}
        item = event.get("item", {}) if isinstance(event, dict) else {}
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call":
            source = str(item.get("tool", item.get("name", "holoqa")))
        elif summary.startswith(("Running command:", "Command ")):
            source = self.state.provider
        elif summary.startswith("ERROR:"):
            source = "error"
        else:
            source = "agent"
        step_id = self._event_step_id(event)
        self._append_log(summary, source=source, step_id=step_id)
        self._last_event_monotonic = time.monotonic()
        self.state.activity = "ERROR" if summary.startswith("ERROR:") else "RUNNING"
        self.state.activity_detail = summary
        self._apply_protocol_state(event)
        self._render_state()

    @staticmethod
    def _event_step_id(event: object) -> str | None:
        """The step a provider event refers to, in either wire format."""
        if not isinstance(event, dict):
            return None
        # Codex: item.arguments.step_id
        item = event.get("item")
        if isinstance(item, dict):
            arguments = item.get("arguments")
            if isinstance(arguments, dict) and arguments.get("step_id") is not None:
                return str(arguments["step_id"])
        # Claude: message.content[].input.step_id on a tool_use block
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    arguments = block.get("input")
                    if isinstance(arguments, dict) and arguments.get("step_id") is not None:
                        return str(arguments["step_id"])
        return None

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "transcript-search":
            return
        self.transcript_query = event.value
        self._render_transcript()

    def _apply_protocol_state(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            return
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        step_id = arguments.get("step_id")
        if step_id and event.get("type") == "item.started":
            self.state.steps = [
                (row_id, title, "IN PROGRESS" if row_id == step_id else verdict)
                for row_id, title, verdict in self.state.steps
            ]
            if self.follow_live:
                self.selected_step_id = str(step_id)
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        structured = result.get("structured_content") if isinstance(result.get("structured_content"), dict) else {}
        tool = item.get("tool", item.get("name"))
        if tool == "holoqa_judge" and structured.get("verdict") and step_id:
            verdict = str(structured["verdict"])
            self.state.steps = [
                (row_id, title, verdict if row_id == step_id else state)
                for row_id, title, state in self.state.steps
            ]
            raw_assertions = structured.get("assertions", [])
            if isinstance(raw_assertions, list):
                self.state.assertions[str(step_id)] = [
                    AssertionItem(
                        str(step_id), str(value.get("kind", "assertion")),
                        value.get("ok") if value.get("ok") in {True, False, None} else None,
                        str(value.get("detail", "No detail supplied")),
                    )
                    for value in raw_assertions if isinstance(value, dict)
                ]
        if tool == "holoqa_observe" and structured.get("file") and step_id:
            evidence = f"{step_id}: {structured['file']}"
            if evidence not in self.state.evidence:
                self.state.evidence.append(evidence)
            filename = str(structured["file"])
            path = Path(self.run_dir) / "evidence" / filename if self.run_dir else None
            if not any(item.step_id == str(step_id) and item.name == filename for item in self.state.artifacts):
                self.state.artifacts.append(
                    ArtifactItem(
                        str(step_id), str(structured.get("kind", arguments.get("kind", "evidence"))),
                        filename, path, size=int(structured.get("bytes", 0) or 0),
                    )
                )

    def _copy_log(self) -> None:
        payload = "\n".join(self._log_lines) + ("\n" if self._log_lines else "")
        self._persist_log(payload)
        copied = self._copy_text(payload)
        suffix = " and clipboard" if copied else ""
        self._set_safety(f"Log copied to {self.log_path}{suffix}")

    @staticmethod
    def _copy_text(payload: str, platform: str | None = None) -> bool:
        """Copy Unicode text without invoking a shell.

        ``platform`` defaults to :data:`os.name` and exists so the Windows branch
        can be tested on Linux. A test that patched ``os.name`` instead would
        mutate a process-global that `pathlib` reads at instantiation time: on
        Linux every later `Path(...)` became a `WindowsPath` and raised from
        inside pytest's own reporting, turning one assertion into a run-wide
        INTERNALERROR. Passing the platform keeps the branch testable and the
        process intact.
        """
        clip = shutil.which("clip") if (platform or os.name) == "nt" else None
        if clip:
            try:
                # Do not let subprocess inherit cp1252 for stdin: provider
                # transcripts commonly contain Unicode glyphs such as ✓.
                result = subprocess.run(
                    [clip], input=payload.encode("utf-8"), timeout=3,
                    check=False, capture_output=True,
                )
                return result.returncode == 0
            except (OSError, subprocess.SubprocessError, UnicodeError):
                pass
        return False

    @staticmethod
    def _open_path(path: Path) -> None:
        if os.name == "nt":
            getattr(os, "startfile")(str(path))
            return
        command = ["open", str(path)] if os.name == "posix" and shutil.which("open") else ["xdg-open", str(path)]
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _reveal_path(path: Path) -> None:
        if os.name == "nt":
            subprocess.Popen(
                ["explorer.exe", f"/select,{path}"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        command = ["open", "-R", str(path)] if shutil.which("open") else ["xdg-open", str(path.parent)]
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _resolved_artifact(self, index: int | None = None) -> Path | None:
        index = self.selected_artifact_index if index is None else index
        if not 0 <= index < len(self.state.artifacts):
            self._set_safety("No artifact is selected")
            return None
        item = self.state.artifacts[index]
        candidate = item.path
        if candidate is None and self.run_dir:
            candidate = Path(self.run_dir) / "evidence" / item.name
        if candidate is None:
            self._set_safety(f"Artifact path is unavailable: {item.name}")
            return None
        candidate = candidate.resolve()
        if not self.run_dir:
            self._set_safety("Cannot validate artifact without a run directory")
            return None
        root = Path(self.run_dir).resolve()
        if not candidate.is_relative_to(root):
            self._set_safety(f"Refused artifact outside the run directory: {candidate}")
            return None
        if not candidate.exists():
            self._set_safety(f"Artifact does not exist: {candidate}")
            return None
        self.selected_artifact_index = index
        return candidate

    def _persist_log(self, payload: str | None = None) -> None:
        if self.log_path is None:
            if self.run_dir:
                self.log_path = Path(self.run_dir) / "out" / "agent-log.txt"
            else:
                self.log_path = Path(tempfile.gettempdir()) / "holoqa-tui-demo-agent-log.txt"
        if payload is None:
            payload = "\n".join(self._log_lines) + ("\n" if self._log_lines else "")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(payload, encoding="utf-8")

    def on_unmount(self) -> None:
        """Leave a copy even when the user exits before pressing the copy key."""
        try:
            self._persist_log()
        except OSError:
            pass

    def _set_safety(self, value: str) -> None:
        self.state.safety = value
        if self.is_mounted:
            self.query_one("#safety", Static).update(value)

    def _start_agent(self) -> None:
        assert self.plan_path is not None
        assert self.request is not None
        try:
            plan = plan_module.load(self.plan_path)
            self.state.title = f"{plan.meta.app} release run"
            self.state.provider = self.request.provider
            self.state.mode = self.request.mode
            self.state.steps = [(step.id, step.title, "PENDING") for step in plan.steps]
            self.state.groups = {stage.id: stage.title or stage.id for stage in plan.stages}
            self.state.step_groups = {step.id: step.stage for step in plan.steps if step.stage}
            self.selected_step_id = self.state.steps[0][0] if self.state.steps else None
            # Resolve the run directory now, not at the end. The wrapper creates
            # this exact path, and knowing it up front is what lets the dashboard
            # read verdicts as they land instead of only when the agent exits.
            if not self.resume and not self.run_dir:
                self.run_dir = str(
                    self.request.cwd.resolve() / ".holoqa" / "runs" /
                    f"{datetime.now().strftime('%Y%m%d-%H%M')}-{plan.meta.app}"
                )
            self._render_state()
        except Exception as error:
            self._set_safety(f"Cannot load plan: {error}")
            self._set_activity("ERROR", f"Cannot load plan: {error}")
            return
        self._set_safety("Starting agent - HoloQA remains authoritative")
        self.state.activity = "STARTING"
        self.state.activity_detail = "Launching provider"
        self._started_monotonic = time.monotonic()
        self._render_activity()
        self._worker = threading.Thread(target=self._run_agent, daemon=True)
        self._worker.start()

    def _run_agent(self) -> None:
        assert self.request is not None
        assert self.plan_path is not None

        def callback(channel: str, line: str) -> None:
            try:
                self.call_from_thread(self._handle_agent_line, channel, line)
            except RuntimeError:
                pass

        request = agent_module.AgentRunRequest(
            provider=self.request.provider,
            cwd=self.request.cwd,
            mode=self.request.mode,
            model=self.request.model,
            retain_events=self.request.retain_events,
            output_callback=callback,
            cancel_event=self._cancel_event,
        )
        try:
            if self.resume and self.run_dir:
                # Continue the run the user pointed at. `create_and_launch`
                # would call Run.create and fail with "run already exists",
                # which is why the TUI could not be opened on a live run.
                run = run_module.find(self.run_dir)
                result = agent_module.launch(request, run, resume=True)
            else:
                result = agent_module.create_and_launch(
                    self.plan_path, request, tag=self.tag, commit=self.commit,
                    tester=self.tester, base_url=self.base_url, run_dir=self.run_dir,
                )
            self.call_from_thread(self._finish_agent, result)
        except Exception as error:
            self.call_from_thread(self._handle_worker_error, str(error))

    def _handle_worker_error(self, error: str) -> None:
        self._last_error = error
        self._append_log(f"ERROR: wrapper: {error}", source="error")
        self.state.safety = f"Agent error: {error}"
        self.state.activity = "ERROR"
        self.state.activity_detail = error
        self._render_state()

    def _set_activity(self, activity: str, detail: str) -> None:
        self.state.activity = activity
        self.state.activity_detail = detail
        self._render_activity()

    def _finish_agent(self, result: dict[str, Any]) -> None:
        holoqa = result.get("holoqa", {})
        self.state.run_id = holoqa.get("run_id", self.state.run_id)
        self.state.decision = holoqa.get("decision", "HOLD")
        self.state.safety = f"Agent finished - HoloQA decision: {self.state.decision}"
        self.state.activity = "COMPLETE"
        self.state.activity_detail = f"HoloQA decision: {self.state.decision}"
        counts = holoqa.get("counts", {})
        run_dir = holoqa.get("run_dir")
        if run_dir:
            self.run_dir = str(run_dir)
            try:
                run = run_module.find(run_dir)
                raw = run.read()
                plan = plan_module.load(raw["plan_path"])
                stored_steps = raw.get("steps", {})
                self.state.steps = [
                    (step.id, step.title, stored_steps.get(step.id, {}).get("verdict", "PENDING"))
                    for step in plan.steps
                ]
                self.state.groups = {stage.id: stage.title or stage.id for stage in plan.stages}
                self.state.step_groups = {step.id: step.stage for step in plan.steps if step.stage}
                self.state.evidence = [
                    f"{step_id}: {item.get('file', item.get('kind', 'evidence'))}"
                    for step_id, item_step in stored_steps.items()
                    for item in item_step.get("observations", [])
                ]
                self.state.artifacts = [
                    ArtifactItem(
                        str(step_id), str(item.get("kind", "evidence")),
                        str(item.get("file", "evidence")),
                        Path(run_dir) / "evidence" / str(item.get("file", "evidence")),
                        str(item.get("captured_at", "")), int(item.get("bytes", 0) or 0),
                    )
                    for step_id, item_step in stored_steps.items()
                    for item in item_step.get("observations", [])
                    if isinstance(item, dict)
                ]
                self.state.assertions = {
                    str(step_id): [
                        AssertionItem(
                            str(step_id), str(item.get("kind", "assertion")),
                            item.get("ok") if item.get("ok") in {True, False, None} else None,
                            str(item.get("detail", "No detail supplied")),
                        )
                        for item in item_step.get("assertions", []) if isinstance(item, dict)
                    ]
                    for step_id, item_step in stored_steps.items()
                }
            except (OSError, KeyError, plan_module.PlanError, run_module.GuardrailError):
                # The summary remains useful even if a run was interrupted
                # before its final run.json could be read.
                pass
        self._append_log(
            "HoloQA finished: " + ", ".join(
                f"{key} {value}" for key, value in counts.items()
            )
        )
        self._render_state()

    def action_cancel(self) -> None:
        self._cancel_event.set()
        self._set_safety("Cancel requested - run will remain HOLD")
        self._set_activity("CANCELLING", "Waiting for provider to stop")

    def action_copy_log(self) -> None:
        self._copy_log()

    def action_refresh(self) -> None:
        if not self.run_dir:
            self._set_safety("No run directory is available to refresh")
            return
        try:
            run = run_module.find(self.run_dir)
            raw = run.read()
            plan = plan_module.load(raw["plan_path"])
            status = run.status(plan)
            stored_steps = raw.get("steps", {})
            self.state.run_id = str(status["run_id"])
            self.state.decision = str(status["decision"])
            self.state.steps = [
                (step.id, step.title, stored_steps.get(step.id, {}).get("verdict") or "PENDING")
                for step in plan.steps
            ]
            self.state.groups = {stage.id: stage.title or stage.id for stage in plan.stages}
            self.state.step_groups = {step.id: step.stage for step in plan.steps if step.stage}
            self.state.artifacts = [
                ArtifactItem(
                    str(step_id), str(item.get("kind", "evidence")), str(item.get("file", "evidence")),
                    Path(self.run_dir) / "evidence" / str(item.get("file", "evidence")),
                    str(item.get("captured_at", "")), int(item.get("bytes", 0) or 0),
                )
                for step_id, step in stored_steps.items()
                for item in step.get("observations", []) if isinstance(item, dict)
            ]
            self.state.evidence = [f"{item.step_id}: {item.name}" for item in self.state.artifacts]
            self.state.assertions = {
                str(step_id): [
                    AssertionItem(
                        str(step_id), str(item.get("kind", "assertion")),
                        item.get("ok") if item.get("ok") in {True, False, None} else None,
                        str(item.get("detail", "No detail supplied")),
                    )
                    for item in step.get("assertions", []) if isinstance(item, dict)
                ]
                for step_id, step in stored_steps.items()
            }
        except (OSError, KeyError, ValueError, plan_module.PlanError, run_module.GuardrailError) as error:
            self._set_safety(f"Refresh failed: {error}")
            return
        self._render_state()
        self._set_safety("Refreshed from HoloQA run state")

    def action_show_details(self) -> None:
        self.query_one("#detail-tabs", TabbedContent).active = "tab-details"

    def action_show_evidence(self) -> None:
        self.query_one("#detail-tabs", TabbedContent).active = "tab-evidence"

    def action_show_assertions(self) -> None:
        self.query_one("#detail-tabs", TabbedContent).active = "tab-assertions"

    def action_select_step(self, step_id: str) -> None:
        self._select_step(step_id, user=True)

    def action_toggle_group(self, group_id: str) -> None:
        if group_id not in self.state.groups:
            return
        if group_id in self.collapsed_groups:
            self.collapsed_groups.remove(group_id)
        else:
            self.collapsed_groups.add(group_id)
        self._render_state()

    def action_cursor_up(self) -> None:
        if self._theme_menu_open:
            self._move_theme(-1)
            return
        active_tab = self.query_one("#detail-tabs", TabbedContent).active
        if active_tab == "tab-evidence":
            artifacts = self._artifacts_for_step()
            indices = [index for index, _ in artifacts]
            if indices:
                current = indices.index(self.selected_artifact_index) if self.selected_artifact_index in indices else 0
                self.selected_artifact_index = indices[max(0, current - 1)]
                self._render_state()
            return
        if active_tab == "tab-assertions":
            self.selected_assertion_index = max(0, self.selected_assertion_index - 1)
            self._render_state()
            return
        if not self.state.steps:
            return
        ids = [row[0] for row in self.state.steps]
        index = ids.index(self.selected_step_id) if self.selected_step_id in ids else 0
        self._select_step(ids[max(0, index - 1)], user=True)

    def action_cursor_down(self) -> None:
        if self._theme_menu_open:
            self._move_theme(1)
            return
        active_tab = self.query_one("#detail-tabs", TabbedContent).active
        if active_tab == "tab-evidence":
            indices = [index for index, _ in self._artifacts_for_step()]
            if indices:
                current = indices.index(self.selected_artifact_index) if self.selected_artifact_index in indices else -1
                self.selected_artifact_index = indices[min(len(indices) - 1, current + 1)]
                self._render_state()
            return
        if active_tab == "tab-assertions":
            assertions = self.state.assertions.get(self.selected_step_id or "", [])
            if assertions:
                self.selected_assertion_index = min(len(assertions) - 1, self.selected_assertion_index + 1)
                self._render_state()
            return
        if not self.state.steps:
            return
        ids = [row[0] for row in self.state.steps]
        index = ids.index(self.selected_step_id) if self.selected_step_id in ids else -1
        self._select_step(ids[min(len(ids) - 1, index + 1)], user=True)

    def action_follow_live(self) -> None:
        self.follow_live = True
        active = self._active_step_id()
        if active:
            self._select_step(active, user=False)
        self._set_safety("Following the live HoloQA step")

    def action_select_assertion(self, index: int) -> None:
        assertions = self.state.assertions.get(self.selected_step_id or "", [])
        if 0 <= index < len(assertions):
            self.selected_assertion_index = index
            self._render_state()

    def action_open_artifact(self, index: int) -> None:
        path = self._resolved_artifact(index)
        if path is None:
            return
        try:
            self._file_opener(path)
        except OSError as error:
            self._set_safety(f"Cannot open artifact: {error}")
            return
        self._set_safety(f"Opened artifact: {path.name}")

    def action_open_selected(self) -> None:
        if self.query_one("#detail-tabs", TabbedContent).active == "tab-evidence":
            self.action_open_artifact(self.selected_artifact_index)

    def action_activate_selected(self) -> None:
        if self._theme_menu_open:
            self._commit_theme_preview()
            return
        self.action_open_selected()

    def action_copy_artifact_path(self) -> None:
        path = self._resolved_artifact()
        if path is not None:
            copied = self._copy_text(str(path))
            self._set_safety(f"Artifact path {'copied' if copied else 'ready'}: {path}")

    def action_reveal_artifact(self) -> None:
        path = self._resolved_artifact()
        if path is None:
            return
        try:
            self._path_revealer(path)
        except OSError as error:
            self._set_safety(f"Cannot reveal artifact: {error}")
            return
        self._set_safety(f"Revealed artifact: {path.name}")

    def action_pause_transcript(self) -> None:
        self.transcript_paused = not self.transcript_paused
        state = "paused" if self.transcript_paused else "following live output"
        self._set_safety(f"Transcript {state}")
        if not self.transcript_paused:
            self.query_one("#agent-log", RichLog).scroll_end(animate=False)

    def action_cycle_transcript_filter(self) -> None:
        filters = ("all", "holoqa", "agent", "errors")
        index = filters.index(self.transcript_filter)
        self.transcript_filter = filters[(index + 1) % len(filters)]
        self._render_transcript()
        self._set_safety(f"Transcript filter: {self.transcript_filter}")

    def action_search_transcript(self) -> None:
        search = self.query_one("#transcript-search", Input)
        search.remove_class("hidden")
        search.display = True
        search.disabled = False
        search.focus()

    def action_hide_search(self) -> None:
        if self._theme_menu_open:
            self._cancel_theme_preview()
            return
        overlay = self.query_one("#overlay", Static)
        if overlay.display:
            overlay.add_class("hidden")
            overlay.display = False
            return
        search = self.query_one("#transcript-search", Input)
        if search.display:
            search.disabled = True
            search.add_class("hidden")
            search.display = False
            self.query_one("#agent-log", RichLog).focus()

    def action_copy_last_event(self) -> None:
        entries = self._visible_transcript_entries()
        if not entries:
            self._set_safety("No visible transcript event to copy")
            return
        entry = entries[-1]
        line = f"{entry.timestamp}  {entry.source:<16}  {entry.message}"
        copied = self._copy_text(line)
        self._set_safety("Transcript event copied" if copied else f"Transcript event: {line}")

    @staticmethod
    def _overlay_action(output: Text, label: str, action: str) -> None:
        start = len(output)
        output.append(f"  {label}\n", style="#46e5f0")
        output.stylize(Style(underline=False, meta={"@click": f"app.{action}"}), start, len(output))

    def _show_overlay(self, content: Text) -> None:
        overlay = self.query_one("#overlay", Static)
        overlay.update(content)
        overlay.remove_class("hidden")
        overlay.display = True

    def action_show_theme_menu(self) -> None:
        """Open a keyboard-first theme picker with live preview."""
        if self._theme_menu_open:
            return
        self.action_close_overlay()
        self._theme_before_picker = self.theme
        menu = self.query_one("#theme-menu", OptionList)
        menu.set_options([Option(name, id=name) for name in self._theme_names])
        try:
            index = self._theme_names.index(self.theme)
        except ValueError:
            index = 0
        menu.highlighted = index
        menu.remove_class("hidden")
        menu.display = True
        self._theme_menu_open = True
        menu.focus()
        self._preview_theme(self._theme_names[index])

    def _move_theme(self, delta: int) -> None:
        if not self._theme_menu_open:
            return
        menu = self.query_one("#theme-menu", OptionList)
        current = menu.highlighted if menu.highlighted is not None else 0
        index = max(0, min(len(self._theme_names) - 1, current + delta))
        menu.highlighted = index
        self._preview_theme(self._theme_names[index])

    def _preview_theme(self, theme_name: str) -> None:
        self.theme = theme_name
        self._set_safety(f"Previewing theme: {theme_name}  (Enter apply, Esc cancel)")

    def _close_theme_menu(self) -> None:
        menu = self.query_one("#theme-menu", OptionList)
        menu.add_class("hidden")
        menu.display = False
        self._theme_menu_open = False
        self.query_one("#agent-log", RichLog).focus()

    def _commit_theme_preview(self) -> None:
        selected = self.theme
        _save_theme_preference(selected)
        self._close_theme_menu()
        self._set_safety(f"Theme applied: {selected}")

    def _cancel_theme_preview(self) -> None:
        self.theme = self._theme_before_picker
        self._close_theme_menu()
        self._set_safety(f"Theme preview cancelled: {self.theme}")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._theme_menu_open and event.option.id:
            self._preview_theme(str(event.option.id))

    def action_show_help(self) -> None:
        content = Text()
        content.append("Keyboard & mouse\n\n", style="bold #e8eef4")
        content.append(
            "↑/↓ select item   Enter open   f follow live\n"
            "1 details   2 evidence   3 assertions\n"
            "p pause output   v filter   / search   Shift+Y copy event\n"
            "y copy full log   c cancel   r refresh   m run menu\n"
            "Click steps, artifacts, assertions, header actions, or verdict ···\n\n",
            style="#c8d3df",
        )
        self._overlay_action(content, "Close", "close_overlay")
        self._show_overlay(content)

    def action_show_run_menu(self) -> None:
        content = Text()
        content.append("Run actions\n\n", style="bold #e8eef4")
        self._overlay_action(content, "Open run directory", "open_run_dir")
        self._overlay_action(content, "Copy run summary", "copy_run_summary")
        self._overlay_action(content, "Open report", "open_report")
        self._overlay_action(content, "Export readable log", "copy_log")
        self._overlay_action(content, "Open raw events", "open_raw_events")
        content.append("\n")
        self._overlay_action(content, "Close", "close_overlay")
        self._show_overlay(content)

    def action_show_error(self) -> None:
        content = Text()
        content.append("Agent error\n\n", style="bold #ef6464")
        content.append(self._last_error or self.state.activity_detail, style="#c8d3df")
        content.append("\n\n")
        self._overlay_action(content, "Copy diagnostics", "copy_diagnostics")
        self._overlay_action(content, "Retry provider startup", "retry")
        self._overlay_action(content, "Close", "close_overlay")
        self._show_overlay(content)

    def action_close_overlay(self) -> None:
        overlay = self.query_one("#overlay", Static)
        overlay.add_class("hidden")
        overlay.display = False

    def _safe_run_target(self, relative: str = "") -> Path | None:
        if not self.run_dir:
            self._set_safety("No run directory is available")
            return None
        root = Path(self.run_dir).resolve()
        target = (root / relative).resolve() if relative else root
        if not target.is_relative_to(root):
            self._set_safety("Refused path outside the run directory")
            return None
        if not target.exists():
            self._set_safety(f"Run file does not exist: {target}")
            return None
        return target

    def _open_run_target(self, relative: str = "") -> None:
        target = self._safe_run_target(relative)
        if target is None:
            return
        try:
            self._file_opener(target)
        except OSError as error:
            self._set_safety(f"Cannot open run file: {error}")
            return
        self.action_close_overlay()
        self._set_safety(f"Opened: {target}")

    def action_open_run_dir(self) -> None:
        self._open_run_target()

    def action_open_report(self) -> None:
        self._open_run_target("out/report.md")

    def action_open_raw_events(self) -> None:
        self._open_run_target(f"out/{agent_module.EVENTS_FILE}")

    def action_copy_run_summary(self) -> None:
        counts = self._counts()
        summary = (
            f"HoloQA run {self.state.run_id}\n"
            f"Plan: {self.state.title}\nProvider: {self.state.provider}\n"
            f"Decision: {self.state.decision}\n"
            + "\n".join(f"{key}: {value}" for key, value in counts.items())
        )
        copied = self._copy_text(summary)
        self.action_close_overlay()
        self._set_safety("Run summary copied" if copied else "Run summary prepared; clipboard unavailable")

    def action_copy_diagnostics(self) -> None:
        diagnostics = (
            f"activity: {self.state.activity}\n"
            f"detail: {self.state.activity_detail}\n"
            f"error: {self._last_error}\n"
            f"run_dir: {self.run_dir or '-'}\n"
            f"log: {self.log_path or '-'}\n"
        )
        copied = self._copy_text(diagnostics)
        self._set_safety("Diagnostics copied" if copied else "Diagnostics ready; clipboard unavailable")

    def action_retry(self) -> None:
        if self.state.activity != "ERROR":
            self._set_safety("Retry is only available after an agent error")
            return
        if self._worker is not None and self._worker.is_alive():
            self._set_safety("Waiting for the failed provider process to stop")
            return
        if not self.plan_path or not self.request:
            self._set_safety("This run has no provider request to retry")
            return
        self.action_close_overlay()
        self._cancel_event = threading.Event()
        self._last_error = ""
        self._start_agent()


def run_demo() -> None:
    HoloQATui.demo().run()
