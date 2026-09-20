# HoloQA Terminal UI

The TUI is a human-facing console for a HoloQA run. It displays provider
activity, but it never treats provider prose as a verdict. `run.json` and the
HoloQA tools remain authoritative.

## Start safely

Use demo mode to inspect the layout without launching a provider:

```bash
holoqa tui --demo
```

For an end-to-end disposable workspace, use the local sandbox:

```bash
holoqa tui --sandbox --provider codex
```

The sandbox binds its fixture server to `127.0.0.1`, uses an isolated
workspace, and is retained after exit unless `--cleanup-sandbox` is supplied.
The TUI cannot answer interactive provider approval prompts, so unattended mode
is intended only for this isolated scenario.

## Navigation and actions

| Input | Action |
| --- | --- |
| `↑` / `↓` | Select a step, evidence item, or assertion depending on the active tab |
| `f` | Follow the currently running HoloQA step |
| `Enter` / click | Open the selected artifact or select a visible item |
| `1`, `2`, `3` | Open Step Details, Evidence, or Assertions |
| Click a group heading | Collapse or expand its tasks |
| `p` | Pause or resume transcript auto-follow |
| `v` | Cycle transcript filters: all, HoloQA, agent, errors |
| `/` | Search transcript; `Esc` closes the search field |
| `y` | Persist and copy the readable transcript |
| `Shift+Y` | Copy the last visible transcript event |
| `x` / `e` | Copy the selected artifact path / reveal it in the file manager |
| `c` | Request provider cancellation; the run remains HOLD |
| `r` | Refresh from the authoritative run directory |
| `?` / `m` | Open help / run actions |
| `q` | Exit while preserving run artifacts |

Header shortcuts, the verdict `···` menu, group headings, step rows, evidence,
assertions, and transcript events are also clickable. Clickable styling is
deliberately subtle: it uses color, selection backgrounds, and hover emphasis
instead of persistent underlines.

## Artifact safety

An artifact can be opened only when its resolved path exists inside the active
run directory. Paths outside that directory, missing files, and unavailable
run roots are refused and reported in the safety banner. The same boundary is
used for opening the run directory, report, and retained raw provider events.

## Lifecycle and recovery

The activity indicator follows the wrapper lifecycle: `STARTING`, `RUNNING`,
`CANCELLING`, `COMPLETE`, or `ERROR`. On an error, open `[details]` in the
safety bar to view the full message, copy diagnostics, or retry provider
startup. Retry is disabled while the failed worker is still alive and never
changes a HoloQA verdict directly.
