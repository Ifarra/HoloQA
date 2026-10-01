# Workspaces — one home per application, many runs inside it

> Status: **design, not implemented.** This supersedes the session model in
> `TUI-SESSIONS.md` §A (which answered "switch sessions" when the real problem was
> "the run layout has no room for a second run").

## 0. The three problems, stated as bugs

The user's report, each traced to a line in the current code:

**1. Moving between runs is hard.**
`run_module.find()` returns the newest run and nothing else:

```python
candidates = sorted(...); return Run(candidates[-1])   # run.py:731
```

There is no notion of "the run I am working in". Every other run is reachable
only by typing its path.

**2. The plan cannot be switched from the TUI.**
`plan_path` is fixed at construction (`tui.py:303`) and never reassigned. The
only run-related binding is `m` → run menu, which opens directories and copies
text — it cannot change *what is being tested*.

**3. Retesting requires deleting the old run.**
`Run.create` refuses an existing directory:

```python
if (run.dir / RUN_FILE).exists():
    raise GuardrailError(f"run already exists at {run.dir}; pick a new directory or resume it")
```

and the generated name has **one-minute resolution** (`%Y%m%d-%H%M`, `mcp.py:101`).
So a developer who fixes a bug and retests within the same minute collides, and
the path of least resistance — the one the tool accidentally teaches — is
**deleting the previous run**, i.e. destroying the evidence that the fix was
needed. That is the opposite of what a QA tool should encourage.

> **Reproduced, not inferred.** Ran the exact naming logic from `mcp.py:101-102`
> twice in the same minute against a temp plan:
> ```
> run 1 created: 20261001-0836-collide
> run 2 target : 20261001-0836-collide
> run 2 REFUSED: run already exists ...
> run 1 run_id = '20261001-0836-collide'  (folder = '20261001-0836-collide')
> ```
> The collision is real, and `run_id` is provably the folder name.


## 1. Why this happened (and what I got wrong before)

`run.json` derives its identity from the folder name:

```python
"run_id": run.dir.name        # run.py:173
```

So *identity* and *location* are the same thing. A run cannot be "re-run" because
re-running would mean reusing the identity, which means reusing the directory,
which is refused. The layout has exactly one slot per app per minute.

My earlier document treated this as a constraint to design around. It is not —
it is the bug. The in-process ledger (which makes an old run `unverified` in a
new process) is a *separate* concern: it governs who may judge a run, not where a
run lives. I conflated the two.

The correct analogy is not "session" — it is **Git**. A plan is a repo; a run is
a commit; retesting is a new commit, never a rewrite of the old one. You can
check out any of them, and the history is the point.

## 2. Target layout

```
.holoqa/
  workspaces/
    shop/                          ← one workspace per application (plan.meta.app)
      workspace.json               ← the index: plan, current run, run list
      plan.pinned.yaml             ← (optional) last pinned plan for reference
      runs/
        001-20261001-1430/         ← run #1 — failed, KEPT
          run.json  vars.json  plan.pinned.yaml  evidence/  out/
        002-20261001-1512/         ← retest after the fix — passed, KEPT
        003-20261001-1604/         ← another retest
  history.jsonl                    ← unchanged, cross-workspace, append-only
```

Key shifts:

| Before | After |
| --- | --- |
| Flat `.holoqa/runs/<stamp>-<app>` | `.holoqa/workspaces/<app>/runs/<NNN>-<stamp>` |
| `run_id` = folder name | `run_id` = stored, and the folder name is derived from it |
| "current run" = newest | "current run" = an explicit pointer in `workspace.json` |
| Retest = delete first | Retest = **append a new run** |

The `NNN` sequence is what makes runs orderable and human-referable ("run 2
passed") without parsing timestamps, and it is what guarantees two retests in the
same minute still get distinct directories.

## 3. `workspace.json`

```json
{
  "app": "shop",
  "plan_path": "/abs/path/to/shop.plan.yaml",
  "created_at": "2026-10-01T07:40:00Z",
  "current_run": "002-20261001-1512",
  "runs": [
    { "run_id": "001-20261001-1430", "at": "...", "decision": "HOLD",
      "counts": {"PASS": 22, "FAIL": 3, "BLOCKED": 6}, "retest_of": null,
      "integrity": "verified" },
    { "run_id": "002-20261001-1512", "at": "...", "decision": "RELEASE",
      "counts": {"PASS": 31, "FAIL": 0, "BLOCKED": 0}, "retest_of": "001-20261001-1430",
      "integrity": "verified" }
  ]
}
```

The index is a **cache for the picker** — cheap to read without walking every run
directory — but `run.json` remains the authority for each run. If the index and a
rund disagreement, `run.json` wins and the index is rebuilt for that entry.

`retest_of` is what makes `run_compare` answer the right question by default: the
baseline of a retest is the run it was retested from, not "the last green one".

## 4. Operations

### 4.1 `workspace()` — resolve or create

```python
def workspace_for(plan_or_app, *, root=None) -> Workspace
```

- Resolved from the **plan's directory**, not the process cwd. This fixes the
  three-way mismatch found in `TUI-SESSIONS.md`: the TUI
  (`tui.py:1391`), the wrapper (`agent.py:458`) and `run.find()`
  (`run.py:729`, process-relative) each computed a different root, so a
  catalogue built on one would miss runs made by another. One resolver, used
  everywhere, is the fix.

  > **Reproduced, not inferred.** Created a run at
  > `<project>/.holoqa/runs/20261001-0900-roots`, then called `run_module.find()`
  > from two different working directories:
  > ```
  > find() from project cwd -> ...\project\.holoqa\runs\20261001-0900-roots
  > find() from other cwd   -> FAILED: no active run; pass run_dir, ...
  > ```
  > The run exists in both cases; only the cwd changed. Any consumer that is not
  > standing in the project directory silently sees "no active run".

- Created lazily on first run.

### 4.2 Starting a run — `Workspace.start_run()`

```python
ws = workspace_for(plan)
run = ws.start_run(plan, meta=..., retest_of=current)   # allocates 003-...
```

Never raises "already exists" for the normal case, because the name is allocated
by the workspace rather than guessed by the caller. `Run.create` keeps its
refusal for an *explicitly named* directory — that guardrail is still correct for
a caller that names a path itself.

### 4.3 Switching — `ws.switch(run_id)` and the picker

- `ws.current_run` is a pointer. `run.find()` gains a workspace-aware path: an
  explicit run, else the workspace's current run, else the newest.
- TUI picker (`s`) lists `ws.runs` with decision, counts, age, integrity, and
  whether it is `current`. `Enter` switches; `g` continues (only the run this
  process holds); `t` retests; `d` moves to trash (two-press).
- Switching runs reassigns `self.run_dir` and re-runs the existing
  `action_refresh` path — no new rendering machinery.

### 4.4 Switching plan — the missing capability

`W` (or `p`) opens a **workspace/plan picker**: every workspace under the root,
each showing its plan path, last run, and decision. Selecting one rebinds the
TUI to that workspace. This is what makes the TUI able to own a whole testing
effort rather than one plan.

It also needs the ability to **add** a plan (point at a new `*.plan.yaml`), which
creates a workspace.

### 4.5 Retest — never destructive

```
[t] → new run in the same workspace, numbered, with retest_of = current
    → provider relaunched with the same request
    → run_compare baselines against retest_of automatically
```

The old run is untouched. Its evidence stays on disk, in the same workspace, one
row up in the picker. A failing run is a **record**, not garbage to be cleared.

### 4.6 Delete — explicit, reversible, never required

`d` moves a run to `.holoqa/workspaces/<app>/.trash/<run id>/` (never unlinks),
requires two presses, and refuses if the run is `current` or `verified`-and-unattested.
Deleting is a housekeeping choice a human makes, never a prerequisite for
retesting.

## 5. Migration

Old layout `.holoqa/runs/<stamp>-<app>` must keep working. Options, in order of
preference:

1. **Read both, write new.** `workspace_for` discovers legacy runs by scanning
   `.holoqa/runs/*` for a matching `app` and presents them in the same picker,
   marked `legacy`. They can be viewed and retested from (a retest creates a
   workspace and a new run), but they are not moved.
2. **Explicit `holoqa workspace migrate`** — moves legacy runs into the workspace
   layout, preserving every byte, recording what it moved. Opt-in only.
3. Never silently rewrite the user's evidence directories. Migration is a command
   a human runs, with a printed plan.

`run_id` for a legacy run stays its folder name; for a new run it is
`<NNN>-<stamp>`. Both are opaque strings to every consumer, so nothing downstream
needs to care which shape it is holding.

## 6. Files touched

| File | Change |
| --- | --- |
| `run.py` | `Workspace` class (`workspace.json`, `start_run`, `switch`, `retest`, trash); `workspace_for()`; `find()` works workspace-aware; `Run.create` keeps its explicit-path refusal |
| `workspace.py` (new) | the workspace index: load, save, rebuild-from-disk, list |
| `cli.py` | `holoqa workspaces` (list), `holoqa runs` (runs in current workspace), `holoqa retest`, `workspace migrate` |
| `tui.py` | run picker (`s`), workspace/plan picker (`W`), retest (`t`), `_open_run`/`_open_workspace` rebinding |
| `history.py` | record `workspace` + `retest_of`; `compare` prefers the retest baseline |
| `report.py` | archive named from `run_id`; report states the workspace and run position |
| `mcp.py` | `holoqa_run_start` resolves through `workspace_for` instead of composing a path |
| `docs/`, `README.md` | the layout, migration, and the picker |

## 7. What survives, deliberately

- **The ledger.** Authority is still per-process and per-run. Switching to an old
  run still shows `unverified` until attested — the workspace makes that
  *visible and navigable* instead of making it impossible to reach.
- **The pinned plan.** A retest pins the plan as it is *then*, so each run's
  contract is frozen beside it. Retesting after a plan fix produces a run whose
  `contract` differs from its parent's, and the report says so.
- **`Run.create`'s refusal** for an explicitly named directory — a caller that
  names a path still gets the guardrail. Loosening that would let a run silently
  reuse another's directory.
- **`history.jsonl`.** Append-only, one line per completed run, now carrying
  `workspace` and `retest_of`.

## 8. Tests

| Test | Proves |
| --- | --- |
| `test_workspace_allocates_sequential_runs` | two runs in the same minute get `001`/`002`, neither is refused |
| `test_retest_keeps_the_previous_run` | the old run dir and its evidence are intact after a retest |
| `test_current_run_pointer_survives_restart` | `ws.current_run` is read back, not inferred as newest |
| `test_legacy_runs_are_listed_and_readable` | an old-layout run appears in the picker and can be viewed |
| `test_retest_of_sets_compare_baseline` | `run_compare` uses the parent, not the last green |
| `test_delete_moves_to_trash_and_refuses_current` | destructive action is reversible and guarded |
| `test_workspace_resolved_from_plan_not_cwd` | the three-way root mismatch is closed: same workspace from any cwd |
| `test_mcp_run_start_uses_workspace` | `holoqa_run_start` no longer composes `stamp-app` itself |

## 9. Build order

| # | Item | Why first |
| --- | --- | --- |
| 1 | `Workspace` + `workspace_for()` + `start_run` + legacy read | the foundation; nothing else can be built on the old layout |
| 2 | `run.find()` workspace-aware + `current_run` | makes "the run I am in" real |
| 3 | TUI picker (`s`) + switch | the user's #1 complaint |
| 4 | Retest (`t`) + `retest_of` + compare baseline | the user's #3 complaint |
| 5 | Workspace/plan picker (`W`) + add plan | the user's #2 complaint |
| 6 | `holoqa runs` / `workspaces` / `retest` CLI + `migrate` | parity and migration |
| 7 | Delete-to-trash | housekeeping, last |

Items 1–4 are one coherent PR and together they answer all three complaints.
Item 5 is small once 1–3 exist. Item 6 is required before 1 can ship (migration
must exist when the layout changes).

## 10. What this is not

- Not a rewrite of the verdict model. The ledger, pinned plan, hash checks and
  the "agent cannot write a PASS" property are untouched.
- Not a return to a server/database. `workspace.json` is a plain file beside the
  runs it indexes, diffable and inspectable, in line with "no database".
- Not a claim that a switched-to run can be judged. It cannot, and the picker
  says so.
