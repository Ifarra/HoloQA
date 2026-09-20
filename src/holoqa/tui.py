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
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static, TabbedContent, TabPane
from rich.text import Text
from rich.style import Style

from holoqa import agent as agent_module
from holoqa import plan as plan_module
from holoqa import run as run_module


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


class HoloQATui(App[None]):
    """Interactive dashboard for a real or simulated HoloQA agent run."""

    TITLE = "HoloQA Agent Console"
    CSS = """
    Screen {
        background: #061018;
        color: #c8d3df;
        link-style: not underline;
        link-style-hover: bold not underline;
    }
    #shortcuts, #step-table, #verdict-title, #evidence, #assertions,
    #agent-log, #overlay {
        link-style: not underline;
        link-style-hover: bold not underline;
    }
    #topbar { height: 4; padding: 1 2; background: #07131c; border-bottom: solid #2a4556; }
    #brand { width: 10; color: #46e5f0; text-style: bold; }
    #shortcuts { width: 62; color: #b7c6d3; }
    #meta { width: 1fr; color: #b7c6d3; }
    #elapsed { width: 20; color: #8299ad; text-align: right; }
    #body { height: 1fr; }
    #left { width: 26%; min-width: 28; border-right: solid #2a4556; }
    #steps-header { height: 4; padding: 1 2; border-bottom: solid #172b39; }
    #run-title { display: none; }
    #step-table { height: 1fr; padding: 0; overflow-y: auto; scrollbar-size: 1 1; }
    .step-row { height: 5; padding: 1 2; color: #afbdca; }
    .step-pass { color: #cbd7e2; }
    .step-current { background: #132a3a; color: #46e5f0; border-left: thick #46e5f0; }
    #workspace { width: 74%; }
    #run-header { height: 4; padding: 1 2; border-bottom: solid #172b39; }
    #activity { width: 1fr; height: 1; color: #46e5f0; text-style: bold; }
    #step-clock { width: auto; height: 1; color: #8299ad; }
    #upper { height: 58%; padding: 1 2 2 2; border-bottom: solid #2a4556; }
    #transcript-panel { width: 1fr; }
    #agent-log { height: 1fr; background: #061018; padding: 0; scrollbar-size: 1 1; }
    #transcript-search { height: 3; border: round #2a4556; background: #07131c; color: #c8d3df; }
    .hidden { display: none; }
    #verdict-card { width: 28; min-width: 22; margin-left: 2; border: round #2a4556; }
    #verdict-title { height: 4; padding: 1 2; border-bottom: solid #172b39; color: #d8e1e9; }
    #decision { height: 4; padding: 1 2; color: #f5bd5a; text-style: bold; }
    #verdict-counts { height: auto; padding: 1 2; border-top: solid #172b39; color: #8ca0b2; }
    #progress { height: auto; padding: 1 2; border-top: solid #172b39; color: #c8d3df; }
    #detail-panel { height: 42%; padding: 0 2; }
    #detail-tabs { height: 1fr; background: #061018; }
    #detail-tabs Tabs { height: 3; background: #061018; border-bottom: solid #2a4556; }
    #detail-tabs Tab { color: #8299ad; background: #061018; padding: 0 2; }
    #detail-tabs Tab.-active { color: #46e5f0; text-style: bold; }
    #detail-tabs ContentSwitcher { background: #061018; }
    #detail-tabs TabPane { padding: 1 0; }
    #detail { color: #c8d3df; }
    #evidence, #assertions { color: #8299ad; }
    #safety { width: 1fr; height: 1; color: #8299ad; }
    #version { width: auto; height: 1; color: #8299ad; }
    #statusbar { height: 4; padding: 1 2; background: #07131c; border-top: solid #2a4556; color: #8299ad; }
    #overlay { layer: overlay; dock: top; width: 64; height: auto; max-height: 24; margin: 5 8; padding: 1 2; background: #0a1924; border: round #46e5f0; color: #c8d3df; }
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
        plan_path: Path | None = None,
        request: agent_module.AgentRunRequest | None = None,
        run_dir: str = "",
        tag: str = "",
        commit: str = "",
        tester: str = "",
        base_url: str = "",
        file_opener: Callable[[Path], None] | None = None,
        path_revealer: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__()
        self.demo_mode = demo
        self.state = state or (_demo_state() if demo else TuiState())
        self.plan_path = plan_path
        self.request = request
        self.run_dir = run_dir
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
        self._started_monotonic: float | None = time.monotonic() - 207 if demo else None
        self._last_event_monotonic: float | None = time.monotonic() - 12.4 if demo else None
        self._last_error = ""
        self.selected_step_id: str | None = None
        self.selected_artifact_index = 0
        self.selected_assertion_index = 0
        self.collapsed_groups: set[str] = set()
        self.follow_live = True

    @classmethod
    def demo(cls) -> "HoloQATui":
        return cls(demo=True)

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

    def on_mount(self) -> None:
        self._set_compact_mode(self.size.width < 150 or self.size.height < 45)
        self.selected_step_id = self._active_step_id() or (
            self.state.steps[0][0] if self.state.steps else None
        )
        self._render_state()
        self.set_interval(1, self._refresh_activity)
        if self.demo_mode:
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

    @staticmethod
    def _short(value: object, limit: int = 180) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @classmethod
    def _human_event(cls, channel: str, line: str) -> str:
        """Turn provider protocol JSON into a compact, user-facing transcript."""
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return f"Provider: {cls._short(line)}" if channel == "stderr" else cls._short(line)
        if not isinstance(event, dict):
            return cls._short(event)
        if event.get("type") == "error":
            return f"ERROR: {cls._short(event.get('message', event))}"
        event_type = event.get("type", "")
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
                if tool == "holoqa_observe":
                    return f"Capturing {arguments.get('kind', 'evidence')} for step {step_id}"
                if tool == "holoqa_judge":
                    return f"Evaluating assertions for step {step_id}"
                if tool == "holoqa_run_status":
                    return "Reading HoloQA run status"
                return f"Calling {tool}"
            return f"{tool} {('failed' if status == 'failed' else 'completed')}"
        return f"Provider event: {event_type or 'update'}"

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
        self._render_topbar()
        self._render_activity()

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
        arguments = item.get("arguments") if isinstance(item, dict) and isinstance(item.get("arguments"), dict) else {}
        step_id = str(arguments["step_id"]) if arguments.get("step_id") is not None else None
        self._append_log(summary, source=source, step_id=step_id)
        self._last_event_monotonic = time.monotonic()
        self.state.activity = "ERROR" if summary.startswith("ERROR:") else "RUNNING"
        self.state.activity_detail = summary
        self._apply_protocol_state(event)
        self._render_state()

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
    def _copy_text(payload: str) -> bool:
        """Copy Unicode text without invoking a shell."""
        clip = shutil.which("clip") if os.name == "nt" else None
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
