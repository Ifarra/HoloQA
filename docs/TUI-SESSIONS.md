# TUI sessions — switching runs, retesting, and the gaps around them

> ⚠️ **Part A (the session model) is SUPERSEDED by [WORKSPACES.md](WORKSPACES.md).**
> Part A designed a picker over the *existing* flat run layout. That was a
> misdiagnosis: the reason runs are hard to move between is that the layout has
> one slot per app per minute and a same-minute retest is **refused**, which
> teaches developers to delete evidence. The fix is the workspace layout, not a
> picker over the old one. **Part B (the gap analysis) is still current** — those
> gaps remain open. Read Part A only for the research into how agentic CLIs do
> session switching, which the workspace picker still follows.

> Status: **design + research, not implemented.** This is the contract to build
> against. Nothing here changes behaviour until the code lands.

## 0. What the user asked for

Two things:

1. **The TUI should switch between runs**, the way an agentic coding CLI switches
   between sessions — so one console can own a whole testing effort: pick a run,
   retest, open the next one, without leaving the TUI or remembering directory
   names.
2. **Research and analysis: what is still missing in the TUI / HoloQA** that
   would make the user's life easier.

This document answers both. Part A is the session design. Part B is the gap
analysis, ranked, with the reasoning the user asked for.

---

## Part A — the session model

### A.1 What "session" means in the tools we are imitating

Researched against the real conventions, because copying the *concept* without
the *conventions* produces a worse picker than the ones users already know:

| Tool | Discovery | Resume | Picker | Notable |
| --- | --- | --- | --- | --- |
| **Claude Code** | `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` | `--continue` (latest), `--resume <id>`, `/resume` inside a session | interactive, filterable; `--from-pr` filters by PR | Ties a session to a **project directory**; a session resumes with its **model**, permission mode is deliberately *not* restored |
| **Codex** | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` | `codex resume <uuid>` | yes | date-sharded paths |
| **OpenCode** | SQLite (`opencode.db`) | `opencode --session <id>` | yes | storage behind the CLI |
| **fleet / showagent / cad / rejoin** | reads *all* agents' stores | Enter = resume in the right CLI | one cross-agent picker, fuzzy search, pin, fork/branch, delete (two-press) | group **by cwd**, not by folder name; auto-title from the first prompt |

The conventions worth copying, distilled:

1. **"Latest" is a first-class target.** `--continue` exists because most of the
   time you want the run you were just in.
2. **The picker is a list of rows with: name, age, status, and a one-line
   summary** — never a bare id.
3. **Enter means "open it", not "do something destructive".** Destructive
   actions (delete, rewrite) take a second, disarming confirmation.
4. **Group by the thing that groups naturally.** For those tools it is the cwd.
   For HoloQA it is the **application** (`plan.meta.app`) plus the plan path.
5. **Resume restores the conversation *and* its state**, but explicitly does
   **not** restore a stale permission/verdict mode. This maps exactly onto
   HoloQA's own rule: a resumed run continues, it never inherits a *verdict*.

### A.2 What a HoloQA "session" actually is — and the honest constraint

HoloQA already has the storage shape a session model wants: a run is a directory,
`run.json` is the record, and `history.jsonl` is a cross-run index. What it does
**not** have is a way to *see* more than one run at a time, or to move between
them without restarting.

But there is a constraint the agentic-CLI analogy does not have, and it is the
whole reason HoloQA exists:

> **HoloQA's authority is an in-process ledger.** A fresh process that opens an
> existing run cannot tell a genuine `run.json` from a forged one, so it reports
> the run `unverified` and refuses to let it decide a release (`run.py`,
> `integrity()`).

So a "session switcher" in the TUI is **not** the same thing as `claude --resume`.
It has three distinct operations and they must never be blurred:

| Operation | Meaning | Effect on integrity |
| --- | --- | --- |
| **View** | attach read-only to any run to inspect it | none — read-only never decides |
| **Continue** | keep going in a run *this process* is currently driving | unchanged — the ledger is still held |
| **Retest** | start a **new** run from the same plan | new run dir, fresh ledger, `verified` |

A switched-to old run is always **View**: it cannot be judged further from a
process that did not make it. That is not a limitation to work around — it is the
property that makes the tool worth using, and the UI must show it rather than
hide it.

### A.3 Design

#### A.3.1 A run index the TUI can read

New function in `run.py`, next to `find`:

```python
def catalogue(root: Path | None = None) -> list[dict]:
    """Every run under .holoqa/runs/, newest first, as rows a picker can show.

    Read-only. Never adopts a ledger, never decides anything.
    """
    # per run dir: run_id, app, plan_path, meta(tag/commit/tester/started_at),
    # counts{PASS,FAIL,BLOCKED,pending}, decision, integrity status,
    # verified_here (bool: does THIS process hold the ledger for it)
```

`verified_here` is the load-bearing field: it is what lets the picker say
"this one you can keep working in; the rest are read-only".

#### A.3.2 A session picker in the TUI

A new overlay (`#session-menu`), openable with **`s`** (sessions) and from the
run menu. Same visual grammar as the existing theme picker, which already
implements a scrollable, keyboard-first, live-preview overlay — so the
interaction pattern is not new.

```
┌ Sessions ─ shop ──────────────────────────────────┐
│ ▸ ● 20260918-1430-shop   running   12/31  HOLD    │  ← held here (continue)
│   ○ 20260918-1033-shop   RELEASE   31/31  verified│  ← read-only (view)
│   ○ 20260917-2200-shop   HOLD       8/31  verified│
│   ○ 20260916-0915-shop   HOLd       4/31  unverified│ ← attested?
│                                                    │
│   filter: /        app: ←/→     [n] new run        │
└────────────────────────────────────────────────────┘
```

Bindings inside the picker: `↑/↓` or `j/k` move · `/` fuzzy filter · `Enter`
open (View) · `g` Continue (only if held here) · `t` Retest · `d` delete
(two-press) · `o` open run dir · `Esc` close.

Row anatomy, following the conventions above — never a bare id:

- **status glyph**: `●` running now · `◐` halted mid-run (pending steps) ·
  `✓` RELEASE · `✕` FAIL/BLOCKED · `○` finished HOLD
- **run id** (the directory name, which is already `YYYYMMDD-HHMM-app`)
- **age** (relative, like every picker in the research)
- **`done/total`** and **decision**
- **integrity** (`verified` / `unverified` / `attested` / `tampered`) — colour
  coded, because it changes what you may do with the row
- **one-line summary**: the app, and `tester`/`tag` if present

#### A.3.3 Retest — the operation the user actually wants most

"Retest dengan mudah" is the real ask behind session switching. Design:

```
[t] on a run  →  confirm  →  a NEW run is created from that run's pinned plan
```

- Source plan is the **pinned copy** (`plan.pinned.yaml`), not the author's file
  — so a retest runs the contract the old run was judged against, which is the
  only comparison that means anything. If the author's plan has changed since,
  the picker says so and offers the current file instead.
- A new run dir is created with `tag`/`commit`/`tester` carried over and a
  `retest_of: <old run id>` field recorded in `run.json` (new meta key), so
  history can line the two up.
- The provider is relaunched with the same request the TUI already holds. If the
  TUI has no provider (opened purely as a viewer), `t` offers to launch one.
- `run_compare` then answers "what regressed since the run I retested" with no
  extra input, because the baseline is now explicit.

This is where the session model pays off: **retest + compare is the actual
workflow**, and today it requires remembering a directory path and retyping five
flags.

#### A.3.4 What is refused, on purpose

- **No judging into a run this process did not capture.** Selecting an old run
  opens it read-only; `holoqa_judge` on it still reports `unverified`. The picker
  surfaces that instead of letting the user discover it three screens later.
- **No merging two runs into one decision.** Comparison exists; a composite
  verdict does not, and should not.
- **No deleting evidence as a side effect of anything else.** Delete is its own
  explicit two-press action, on an unattested run only, and it moves the run to
  a `.holoqa/trash/` sibling rather than unlinking it.
- **Continue is offered only for the run this process holds.** Everything else
  hands off to `holoqa agent resume --run-dir <dir>` in a fresh process, with the
  picker stating that the new process will report it `unverified` until attested.

#### A.3.5 CLI parity

Anything the TUI can do, the CLI can do, because the TUI is a client of the same
functions (this is already the project's pattern):

```
holoqa runs                     # the same catalogue, plain table / --json
holoqa runs --app shop          # filter
holoqa tui --pick               # open on the picker
holoqa tui --run-dir <dir>      # view/continue a specific run (exists)
holoqa retest --run-dir <dir>   # new run from the pinned plan, records retest_of
```

`holoqa runs --json` must be a stable contract (the research tooling treats
`list --json` as one, and it is what makes the index scriptable).

#### A.3.6 Files touched

| File | Change |
| --- | --- |
| `run.py` | `catalogue()`; `retest_of` meta key; trash-on-delete helper |
| `tui.py` | `#session-menu` overlay + `action_show_sessions`; `_open_run(run_dir)` to rebind the TUI to another run; `action_retest`; `action_continue_run` |
| `cli.py` | `runs` and `retest` subcommands; `tui --pick` |
| `history.py` | surface `retest_of` in `compare` so a retest pair is a first-class baseline |
| `README.md`, `docs/TUI.md` | document the picker and the three operations |

#### A.3.7 Tests

| Test | Proves |
| --- | --- |
| `test_catalogue_lists_newest_first` | ordering and row fields |
| `test_catalogue_marks_only_held_run_continuable` | `verified_here` is true for exactly the run this process captured |
| `test_view_does_not_adopt_a_ledger` | opening another run read-only leaves integrity untouched |
| `test_retest_uses_pinned_plan_and_records_retest_of` | the comparison contract |
| `test_retest_refuses_when_pinned_plan_missing` | an old-format run cannot be silently retested |
| `test_delete_moves_to_trash_and_asks_twice` | destructive action is reversible and confirmed |
| `test_tui_session_picker_binding` | `s` opens, `Enter` views, `g` continues, `t` retests |

---

## Part B — gap analysis: what HoloQA / the TUI still lacks

Ranked by *user pain removed per unit of work*, with the reasoning stated, since
that is what was asked for. Each item says who it helps and what it costs.

### B.1 — Tier 1: real friction, do these

**1. No way to see your own history without a shell.** `history.jsonl` exists but
nothing renders it. `holoqa runs` + the picker (§A) fixes it. *Cost: small.*
*This is the same feature as the session model — do them together.*

**2. Retest + compare is not a single motion.** Today: retype `holoqa agent run`
with five flags, wait, then run `holoqa_run_compare`. §A.3.3 makes it one key.
*Cost: small, once the catalogue exists.*

**3. The TUI cannot start a run.** It can only be launched *with* a run
(`--plan --provider`) or in demo mode. A user who opens `holoqa tui` to look
around has nothing to do. Add: open with no args → the picker; `n` → new run,
prompting for plan/provider/tag. *Cost: medium. Note the current default run dir
is derived from `request.cwd/.holoqa/runs/<stamp>-<app>` in the TUI while the CLI
uses a relative `.holoqa/runs`; a catalogue must resolve one canonical root or the
picker will miss runs. Fix that first.*

**4. Nothing aggregates *across* runs in the TUI.** HoloQA's best idea is
per-run; its weakest view is over time. A `Trends` tab — pass rate, flaky steps,
blocked-cause frequency over the last N runs of an app — is read-only, cheap
(history.jsonl already has the verdicts), and is what a release reviewer actually
asks ("is this better or worse than last week?"). *Cost: medium.*

**5. `strength: weak` and `blocked_cause` exist but barely surface in the TUI.**
`report.py` groups blocked by root cause and counts weak passes; the TUI shows a
step list and a transcript. The two things that make a green run honest are
invisible in the console. Add weak badges on step rows and a root-cause rollup in
the verdict card. *Cost: small, high honesty payoff.*

### B.2 — Tier 2: worth it, but after Tier 1

**6. The TUI cannot judge or block.** By design the agent drives, but a *human*
console that cannot record "I looked at this, it is blocked because X" is
half a tool — and `holoqa_block` already exists with a mandatory cause. Offer it
as an explicit human action, recorded `by: human`, never on a PASS. *Cost:
medium; needs care not to create a second author of verdicts.*

**7. No keyboard-driven step navigation by status.** "Show me only the FAILs" is
a filter the step list lacks; the transcript has filters (`v`) but the step list
does not. *Cost: small.*

**8. No diff view between two runs in the TUI.** `run_compare` returns
regressions/fixes/flaky as data and nothing renders it. Selecting two rows in
the picker (or retest + auto-baseline) and showing the delta is the payoff of the
whole session model. *Cost: medium.*

**9. Transcript is not searchable across runs.** `/` searches the current
transcript only. Across-runs search is what `showagent`/`rejoin` users value
most ("where did I see that error"). *Cost: medium; needs an index.*

### B.3 — Tier 3: nice, low urgency

10. **No notifications when a long run finishes.** A run can take many minutes;
the TUI can only be watched. A terminal bell / OS notification on completion is
small and removes a real annoyance.
11. **No per-run notes/tags settable from the TUI.** `tag/commit/tester` are set
at launch only. Editing them later (for a run started without them) is a
one-field form.
12. **Evidence viewing is file-open only.** No inline preview of a JSON evidence
file or a screenshot inside the TUI. Previewing JSON in the detail pane is
cheap and avoids a context switch to an editor.
13. **No export of a run as a shareable artifact from the TUI.** `holoqa_run_package`
exists; the TUI's run menu does not expose it. One menu item.
14. **No "what changed since this run" for the plan file itself.** If the plan
was edited after a run, nothing says so in the console (`plan_for_judging` knows).

### B.4 — The one thing that should NOT be added

A **dashboard that decides**. Every gap above is about *seeing* and *moving
between* runs. The moment the TUI computes a verdict, aggregates runs into a
score, or lets a human assert PASS, it stops being the console for a tool whose
whole value is "the agent cannot write a pass" — and starts being that tool.
Tier 1 item 4 (Trends) is safe precisely because it *reports* history; it must
never *decide* from it.

---

## Build order

| # | Item | Pain removed | Cost |
| --- | --- | --- | --- |
| 1 | `run.catalogue()` + `holoqa runs` + canonical run root | the foundation for everything | small |
| 2 | Session picker (`s`) with View / Continue / Retest | the user's core ask | medium |
| 3 | `retest` + `retest_of` + compare baseline | retest becomes one motion | small |
| 4 | Weak/blocked-cause surfacing in the TUI | honesty of a green run | small |
| 5 | Start a run from the TUI (`n`) | the TUI stops being read-only | medium |
| 6 | Trends tab over `history.jsonl` | the cross-run question | medium |
| 7 | Diff two runs in the TUI | the payoff of comparing | medium |
| 8 | Human block/judge action | the console becomes usable alone | medium, careful |

Items 1–3 are one coherent PR and are the direct answer to the request. Items
4–5 are small and should ride along. Everything else is a follow-up.

## Verified facts this design rests on

- The TUI already has a **scrollable, keyboard-first overlay pattern** with a
  full lifecycle — `#theme-menu` is an `OptionList` driven by
  `action_show_theme_menu` / `_move_theme` / `_preview_theme` /
  `_commit_theme_preview` / `_cancel_theme_preview`. The session picker is the
  same component with different rows — not a new interaction to invent.

  **One deliberate difference: no live preview.** The theme picker previews
  instantly because a theme is a one-line change. A session row is a whole run
  directory, and previewing it would re-read `run.json`, the plan, the evidence
  index and the assertions on every arrow key. The session picker therefore shows
  a *cached summary row* and only binds the run on `Enter` — the picker's own
  text must be enough to choose from, which is why the row anatomy in §A.3.2
  carries decision, counts, age and integrity rather than just an id.

- The TUI already **rebinds data from a run directory on a timer**
  (`_sync_from_run`) and on demand (`action_refresh`, which reloads plan, steps,
  groups, artifacts, assertions). Opening a different run is that same path with
  a different `run_dir` — the machinery exists.
- `run_dir` is currently resolved **three different ways**, verified in the code:
  the TUI uses `request.cwd.resolve()/.holoqa/runs/<stamp>-<app>`
  (`tui.py:1391`), the wrapper uses `request.cwd/.holoqa/runs/...`
  (`agent.py:458`), and `run.find()` falls back to a **process-relative**
  `Path(".holoqa/runs")` (`run.py:729`). A catalogue built on any single one of
  them will silently miss the runs the others created — so item 1 must define one
  canonical root (resolved from the plan's directory, recorded in `run.json`)
  before the picker can be trusted.

- Integrity is **per-process** (`run.py::integrity`), so View/Continue/Retest is
  not a UI nicety — it is the only honest way to present more than one run at
  once.
