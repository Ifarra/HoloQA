# HoloQA Terminal UI

The TUI is a human-facing console for a HoloQA run. It displays provider
activity, but it never treats provider prose as a verdict. `run.json` and the
HoloQA tools remain authoritative.

## Start safely

Use demo mode to inspect the layout without launching a provider:

```bash
holoqa tui --demo
holoqa tui --demo animated
```

The plain demo is a static layout preview. `--demo animated` replays a local
eight-step `holoshop.shop` storefront journey split into four stages:
Discovery, Product Detail, Cart Journey, and Account & Session. Each stage has
two tasks. The replay advances automatically, produces readable observe/judge
events, ends with a synthetic RELEASE, and exposes locally retained storefront,
product, and catalog screenshots as evidence. It does not launch Codex (or any
other provider) and does not require network access during playback.

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
| `s` | Open the session picker: switch between runs of this workspace |
| `w` | Open the workspace picker: switch which application/plan is shown |
| `t` | Retest — start a new run from the same plan, keeping this one |
| `g` | Continue the highlighted run (only if this process captured it) |
| `?` / `m` | Open help / run actions |
| `Ctrl+P` (`^p`) | Open the theme picker |
| `q` | Exit while preserving run artifacts |

## Sessions — switching between runs

`s` opens a picker over every run of the workspace this console is showing. Each
row carries the run id, its decision, per-verdict counts, how many steps are
still unjudged, and the evidence integrity, so the row text is enough to choose
from without opening anything.

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move through runs |
| `Enter` | Open the highlighted run (bind the console to it) |
| `g` | Continue it — only offered when this process captured it |
| `t` | Retest: a **new** run from the same plan, previous runs untouched |
| `Esc` | Close the picker without switching |

Switching runs rebinds the console: the step list, evidence and assertions all
reload from the chosen run directory.

A run this process did not capture is shown as `[unverified]` and `g` refuses it
with the exact CLI command to resume it elsewhere. That is deliberate — HoloQA's
authority is per-process, so the console reports what it can prove rather than
pretending a run it never saw is trustworthy. It remains fully viewable.

`t` is the operation the flat layout made impossible. Previously a retest in the
same minute collided with the previous run and the tool's own refusal taught you
to delete it — destroying the evidence the fix was needed. A retest now appends a
numbered run, keeps the old one (with its evidence), and records `retest_of` so
`holoqa_run_compare` uses the right baseline.

## Workspaces — switching which application is tested

`w` opens a picker over every workspace under the project: one row per
application, showing its run count, the last decision, and the plan file.

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move through applications |
| `Enter` | Switch the console to that workspace |
| `Esc` | Close without switching |

Selecting a workspace **rebinds the plan** as well as the runs — `plan_path` was
previously fixed at construction, so the console could only ever show the plan it
was launched with. It now lands on that workspace's current run, and `s` then
lists that application's runs.

A workspace whose first run has not started yet is still listed, so an
application cannot silently disappear from the picker.

The theme picker is keyboard-first and scrollable: `↑`/`↓` move through the
complete Textual theme catalog, and each highlighted theme is applied immediately as a temporary live preview.
Press `Enter` to keep the preview or `Esc` to restore the previous theme.

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
