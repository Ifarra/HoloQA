# HoloQA

<p align="center">
  <a href="https://github.com/Ifarra/HoloQA"><img src="https://img.shields.io/github/stars/Ifarra/HoloQA?style=for-the-badge&logo=github&label=stars" alt="GitHub stars"></a>
  <a href="https://github.com/Ifarra/HoloQA/commits/main"><img src="https://img.shields.io/github/last-commit/Ifarra/HoloQA?style=for-the-badge&logo=git&label=updated" alt="Last commit"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11 or newer">
  <img src="https://img.shields.io/badge/interface-MCP%20%2B%20TUI-46E5F0?style=for-the-badge" alt="MCP and TUI interfaces">
</p>



https://github.com/user-attachments/assets/52d74fa0-aefa-46ae-b56d-5d6b938bbb9b



<p align="center">
  <strong>Evidence-backed browser QA for AI agents</strong><br>
  <sub>Observe · Judge · Referee · Release</sub>
</p>

> An agentic QA execution framework with a built-in referee.

A local MCP server that turns a checked-in test plan into an evidence-backed
release checklist.

You drive the browser. HoloQA captures the evidence, evaluates the plan, and
computes the verdict — **you cannot record a pass.**

That constraint is the whole product. A filled checklist is only worth
something if the thing being tested did not also write the results.

No API key. No server, no Docker, no database, no port. HoloQA never calls a
model.

---

## Contents

- [Why](#why) · [Install](#install) · [First run](#first-run)
- [The plan file](#the-plan-file) · [Assertions](#assertions) · [Verdicts](#verdicts)
- [Worked example](#worked-example) · [MCP tools](#mcp-tools) · [CLI](#cli)
- [Guardrails](#guardrails) · [Run directory](#run-directory)
- [Troubleshooting](#troubleshooting) · [Limitations](#limitations)

---

## Why

Most AI-driven testing ends with the agent reporting its own results. The agent
clicks around, decides it went well, and writes `PASS`. The checklist that comes
out looks rigorous and certifies nothing, because the only witness is the party
with an interest in the outcome.

HoloQA moves the two jobs apart:

| Job | Who | Why |
|---|---|---|
| Understand intent, drive the UI, explain results | your AI client | needs judgement |
| Capture evidence, evaluate assertions, decide | HoloQA | must not need judgement |

The agent says *what* to capture. HoloQA performs the capture, hashes the file,
and derives the verdict from a plan a human wrote. `BLOCKED` is the only verdict
an agent may assert, and it requires a written cause.

```
Claude Code / Cursor        the AI. Reads pages, decides what to click,
        │                   writes notes. You already pay for it.
        │ MCP (stdio, local, no auth, no network)
        ▼
    HoloQA                  no intelligence. Subprocess, hash, match.
        │                   Never makes a model call.
        │ subprocess
        ▼
  agent-browser             deterministic CLI (Playwright underneath)
```

---

## Install

HoloQA is not on PyPI. Install it from this repository.

```bash
uv tool install git+https://github.com/Ifarra/HoloQA      # provides `holoqa`
npm install -g agent-browser                            # the browser driver
```

`agent-browser` is a deterministic automation CLI. It is not an AI agent and
needs no API key.

Add one entry to your MCP client:

```jsonc
// ~/.claude.json, or Cursor MCP settings
{ "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }
```

Check it before trusting it:

```bash
holoqa doctor       # is agent-browser present? what MCP entry do I need?
holoqa selftest     # 18 guardrail checks, offline, no browser, no network
```

`doctor` prints the exact JSON to paste, and tells you what is missing:

```
holoqa           2.0.0
agent-browser    agent-browser 0.27.0

MCP client entry:
  { "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }
```

<details>
<summary>Running from a clone instead</summary>

```bash
git clone https://github.com/Ifarra/HoloQA && cd HoloQA && uv sync
uv run holoqa doctor
```

MCP entry for a clone — absolute path to the repo:

```jsonc
{ "mcpServers": { "holoqa": {
    "command": "uv",
    "args": ["run", "--directory", "/absolute/path/to/holoqa", "holoqa"] } } }
```

</details>

---

## First run

```bash
cd ~/code/your-app
holoqa init --app your-app --url https://staging.your-app.test
```

That writes `holoqa.plan.yaml` with one working step and commented examples. It
validates as-is and refuses to overwrite an existing file.

```bash
holoqa validate holoqa.plan.yaml
```

Then, in your AI client:

> run the checklist in holoqa.plan.yaml using holoqa

Writing the plan is the one part that is yours. It is the contract that decides
every verdict, which is why a human owns it. Your agent can draft it — it calls
`holoqa_plan_validate` with no argument to get the format, then explores the
app — but review what it writes and commit it.

---

## The plan file

One YAML file per application, committed beside the code. It is the only
per-app artifact; HoloQA's own source is never edited.

```yaml
meta:
  app: shop
  base_url: ${STAGING_URL}        # ${VAR} expands from the environment
  language: en
  workbook: ./Checklist.xlsx      # optional: annotate an existing checklist

known_behaviors:                  # quirks that look like bugs but are known
  - id: KB-001
    title: search is eventually consistent for a few seconds
    applies_to: [A2]

stages:
  - id: A
    title: Checkout

steps:
  - id: A1
    stage: A
    title: Checkout page loads with a ready cart
    route: /checkout
    do: Open the checkout page.          # instruction for the agent, not code
    expect:
      - url_contains: /checkout
      - text_contains: Checkout
      - screenshot: required

  - id: A2
    stage: A
    title: Placing an order returns 201 PENDING
    depends_on: [A1]
    do: Fill the cart and submit.
    expect:
      - api: { method: POST, path: /api/orders, status: 201 }
      - json: { status: PENDING }
      - capture: { order_id: $.id }

  - id: A3
    stage: A
    title: The order reads back as CONFIRMED
    depends_on: [A2]
    expect:
      - api: { method: GET, path: "/api/orders/{order_id}", status: 200 }
      - json: { status: CONFIRMED }
    blocked_if: the orders service is disabled in this environment
```

### Fields

| Field | Meaning |
|---|---|
| `id` | step identifier, also the key for XLSX row matching |
| `depends_on` | steps that must PASS first; must appear earlier in the file |
| `route` / `do` | human-readable context for the agent — never executed |
| `expect` | the assertions HoloQA evaluates |
| `blocked_if` | when an assertion is **violated**, report BLOCKED carrying this cause instead of FAIL |

`blocked_if` is evaluated, not decorative. A step that declares one and then
fails its assertions is BLOCKED, because the plan itself says the environment
may not be able to reach that step — a feature flag that is off is not a defect
in the product. The violation is not hidden: the assertion's real detail is kept
in the report, after the plan's cause. A step with no `blocked_if` still reports
FAIL, so the downgrade is per-step and deliberate.

### Variables

`capture:` binds a value from a response body. `{order_id}` then interpolates
into any later `path`. The loader **refuses a plan** that references a variable
no earlier step captures — before anything runs.

> **Quoting.** Inside a flow mapping `{ }`, values containing `{braces}` or
> `[brackets]` must be quoted:
> ```yaml
> - api: { method: GET, path: "/api/orders/{order_id}", status: 200 }
> - capture: { finding_id: "$.items[0].id" }
> ```
> HoloQA detects this specific mistake and says so.

---

## Assertions

A closed set, deliberately. HoloQA adjudicates; it is not a browser scripting
language.

| Kind | Checks | Needs |
|---|---|---|
| `url_contains` | substring of the captured URL | a `dom` capture |
| `url_matches` | regex against the captured URL | a `dom` capture |
| `text_contains` | substring of visible page text | a `dom` capture |
| `text_not_contains` | text that must be absent | a `dom` capture |
| `api` | `{method, path, status}` — status may be an int or a list | an `api` capture |
| `json` | subset or JSONPath match against the last body | an `api` capture |
| `screenshot` | `required` — a non-empty image exists | a `screenshot` capture |
| `changed` | `dom` or `api` — two captures, taken apart, must differ | two captures |
| `capture` | `{name: $.json.path}` — binds a value for later steps | an `api` capture |

**`api` path matching.** `path` is matched on path *segments*, so `/api/orders`
matches `/api/orders`, `/api/orders?page=2` and `/api/orders/88`, but never
`/api/orders-archive`. The query string is ignored. When that is still not
strict enough:

```yaml
- api: { method: GET, path: /api/orders, path_exact: true, status: 200 }
- api: { method: GET, path_regex: "^/api/orders/[0-9]+$", status: 200 }
```

An unrecognised key inside an `api` assertion is refused at load time, so a
typo cannot silently run with a default you never asked for.

**Negative tests** use a status list:

```yaml
- api: { method: POST, path: /api/orders, status: [400, 403, 409] }
```

**`changed`** proves something actually moved. Two byte-identical captures FAIL;
one capture is BLOCKED, not PASS. Use it for progress bars, regenerated
documents, and before/after state.

**`json`** matches a subset, so extra keys are fine:

```yaml
- json: { status: PENDING }             # passes against {"id":"x","status":"PENDING"}
- json: { "$.items[0].cvss": 7.5 }      # JSONPath for nested values
```

Anything inexpressible here is `BLOCKED` with a cause — never a soft pass.

---

## Verdicts

| Verdict | Meaning | Who decides |
|---|---|---|
| `PASS` | every assertion satisfied | HoloQA |
| `FAIL` | ran, an assertion was violated — the system is wrong | HoloQA |
| `BLOCKED` | could not run or could not be verified | you, with a cause |

How they combine: any assertion **false** → `FAIL`. Otherwise any assertion
**unevaluable** → `BLOCKED`. Otherwise `PASS`. A definite violation outranks an
unknown.

**When torn between passing and blocking, block.** A checklist that is too loose
is more dangerous than one that is too strict; it exists to hold back a release.
One failed or blocked step means `HOLD`.

`FAIL` means the system is wrong. `BLOCKED` means the test never arrived —
missing prerequisite, disabled feature flag, environment limit. Keeping them
apart is what makes the report actionable.

---

## Worked example

Real output, abridged.

```
$ holoqa validate shop.plan.yaml
{ "status": "ok", "plan": "shop", "steps": 4, "stages": ["A"] }
```

The agent opens the page, then asks HoloQA to capture:

```
>>> holoqa_observe("A1", "screenshot")
{ "file": "step-a1-screenshot.png", "sha256": "28887cd95416fbf1", "bytes": 7044 }

>>> holoqa_judge("A1")
verdict: PASS
  [  ok] url_contains     url is 'http://127.0.0.1:8799/checkout'
  [  ok] text_contains    found in page text
  [  ok] screenshot       1 screenshot(s) captured
```

A capture binds a variable, and the next step consumes it:

```
>>> holoqa_judge("A2")   -> PASS, captured {'order_id': 'ord_88'}
>>> holoqa_observe("A3", "api", method="GET", path="/api/orders/{order_id}")
    url: http://127.0.0.1:8799/api/orders/ord_88        ← interpolated
```

`changed` refuses to pass on one capture, and refuses again when nothing moved:

```
one dom capture      [??] needs two dom captures taken apart, found 1
after clicking Pay   [ok] dom changed between 10:33:49 and 10:33:50
```

Packaging produces the deliverable:

```
>>> holoqa_run_package()
{ "decision": "RELEASE", "counts": {"PASS": 4, "FAIL": 0, "BLOCKED": 0},
  "archive": ".../out/20260918-1033-shop.zip" }
```

```markdown
# Release checklist — shop
- Run: `20260918-1033-shop`   Tag: `v1.2.0`   Commit: `abc1234`   Tester: `Fauzan`

**4 passed · 0 failed · 0 blocked — decision: RELEASE**

| Step | Title | Verdict | Decided by | Evidence | Note |
| A2 | Placing an order returns 201 PENDING | PASS | holoqa | `step-a2-api-orders.json` | — |

## Revised verdicts
- `A4` FAIL → PASS at 2026-09-18T10:33:50Z (was: not found in page text)
```

`examples/wolvesight.plan.yaml` is a real 31-step release checklist ported from
a different application, with negative tests, known behaviours, and captured
variables threading through five stages.

---

## MCP tools

Ten. None accepts a verdict parameter — that absence is enforced by a test.

| Tool | Required | Purpose |
|---|---|---|
| `holoqa_plan_validate` | — | lint a plan; **with no path, returns the format reference** |
| `holoqa_run_start` | `plan_path` | create a run directory |
| `holoqa_run_status` | — | next step, counts, captured variables |
| `holoqa_observe` | `step_id`, `kind` | HoloQA captures evidence |
| `holoqa_judge` | `step_id` | compute the verdict from that evidence |
| `holoqa_block` | `step_id`, `reason` | the one verdict you may assert |
| `holoqa_note` | `step_id`, `note` | attach an observation, cite a KB id |
| `holoqa_kb_add` | `kb_id`, `title` | propose a known behaviour found mid-run |
| `holoqa_run_package` | — | validate → report → optional XLSX → ZIP |
| `holoqa_run_compare` | — | diff against the last green run |

### `holoqa_observe(step_id, kind, ...)`

| `kind` | Extra arguments | Captures |
|---|---|---|
| `screenshot` | `selector` (optional) | PNG, full page by default |
| `dom` | — | URL, title, visible text |
| `api` | `method`, `path`, `body` | in-page fetch, using the browser's session |
| `sse` | `path`, `seconds` | event count, types, how the stream ended |
| `download` | `selector` | arms a blob interceptor, clicks, saves the file |

You get a compact summary; the full capture goes to disk. That is deliberate —
returning whole page snapshots floods the caller's context.

`api` captures run as an **in-page fetch**, so they inherit the browser's own
session including HttpOnly cookies. There is no cookie to copy and no session
file to manage.

### `holoqa_kb_add`

Writes a proposal to `out/kb-proposals.yaml` rather than editing your plan.
HoloQA never edits a plan — plans are human-owned and reviewed.

---

## CLI

```
holoqa [mcp|selftest|doctor|init|validate|status|agent]
```

| Command | What |
|---|---|
| *(none)* / `mcp` | serve the MCP server over stdio |
| `doctor` | check `agent-browser`, print the MCP client entry |
| `init` | scaffold `holoqa.plan.yaml` (`--app`, `--url`, `-o`) |
| `validate <plan>` | lint a plan, print warnings |
| `status` | show the active run (`--run-dir`, or `$HOLOQA_RUN_DIR`) |
| `selftest` | verify every guardrail, offline |
| `agent` | launch a supported coding agent through HoloQA |

`holoqa selftest` is the descendant of a previous tool's `dry-run.mjs`. It
proves the guardrails still bite, using only temporary files. If it goes green
while a guardrail is broken, the checklists this tool produces stop meaning
anything — so it runs with no staging, no browser, and no network, which means
it runs in CI.

### Coding-agent wrapper

HoloQA can launch a coding agent without making that agent the authority on a
release result. The wrapper starts a run, injects HoloQA as a temporary local
MCP server, streams the agent process, and reports the decision calculated from
the HoloQA run when it exits.

```bash
holoqa agent doctor
holoqa agent providers
holoqa agent run holoqa.plan.yaml --provider codex --cwd .
```

Supported providers are `codex`, `claude`, and `opencode`. `supervised` is the
default mode. `unattended` is explicit and intended only for an isolated staging
environment. The wrapper does not change global provider configuration or write
provider configuration into the project. Use `--dry-run` to inspect a launch
without creating a run or starting an agent, and `--retain-events` only when it
is acceptable to persist the raw agent transcript in the run directory.
For Codex, unattended mode uses its automatic-approval workspace sandbox and
ignores exec-policy rules only for that explicit isolated-staging mode; it never
uses Codex's unrestricted bypass flag.

```bash
holoqa agent run holoqa.plan.yaml --provider codex --dry-run
holoqa agent status --run-dir .holoqa/runs/20260919-1200-shop
holoqa agent resume --run-dir .holoqa/runs/20260919-1200-shop --provider claude
```

The wrapper never accepts an agent's prose claim of success. A pending,
interrupted, failed, or blocked run is `HOLD`; only fully judged HoloQA evidence
can yield a release decision.

### Terminal UI

The wrapper also has a Textual dashboard. Explore the layout without launching
an agent or touching a project:

```bash
holoqa tui --demo
holoqa tui --demo animated
```

The plain demo is a static layout preview. The animated demo replays a
realistic `holoshop.shop` storefront journey with four grouped stages
(Discovery, Product Detail, Cart Journey, and Account & Session), two tasks per
stage, readable provider events, timed verdict changes, and local screenshot
artifacts captured from the public storefront. Both demos are safe to exit
with `q`; `c` shows the cancellation/HOLD behavior. No provider is launched.
For a real run, provide the same isolated workspace and plan used by the agent
CLI:

```bash
holoqa tui --plan holoqa.plan.yaml --provider codex --cwd . --mode supervised
```

For a complete disposable localhost scenario, let HoloQA create the workspace,
Git repository, fixture server, and plan:

```bash
holoqa tui --sandbox --provider codex
```

The sandbox binds only to `127.0.0.1`, defaults to Codex's isolated unattended
workspace mode (the TUI cannot answer interactive provider approvals), and is
retained in the system temporary directory after exit so you can inspect the
run. Add `--mode supervised` only when your provider can receive approvals, or
`--cleanup-sandbox` when you want the generated directory removed.

The dashboard displays the agent transcript, HoloQA step state, evidence, and
the HoloQA-computed decision. It automatically switches to a compact layout in
terminals narrower than 150 columns or shorter than 45 rows. `c` terminates the
provider process and leaves the run interrupted/HOLD. Press `y` to copy the
visible transcript, or use `1`, `2`, and `3` to open Step Details, Evidence,
and Assertions. `q` exits without deleting run artifacts.

---

## Guardrails

Ten rules, all covered by `holoqa selftest`.

| # | Rule |
|---|---|
| 1 | `PASS` requires a non-empty evidence file on disk |
| 2 | `FAIL` / `BLOCKED` require a written cause |
| 3 | Verdicts outside the three are rejected |
| 4 | An evidence reference to a missing file is rejected |
| 5 | A changed verdict keeps the superseded one in `revisions` |
| 6 | Credential-bearing headers **and bodies** are redacted |
| 7 | A step with unmet `depends_on` cannot be judged |
| 8 | A known-behaviour citation must reference an id in the plan |
| 9 | An uncaptured assertion yields `BLOCKED`, never `FAIL` |
| 10 | The plan is validated statically before anything runs |

Plus the one that is not negotiable: **an agent can only assert `BLOCKED`.**

Rule 6 matters more than it looks. Evidence ships in a ZIP, so a captured
`POST /login` has its password stripped before the file is written — headers
alone were not enough. Browsers also refuse to expose `Set-Cookie` to `fetch`,
so an in-page capture cannot leak a session cookie even before redaction runs.

Rule 5 exists because a release checklist is used to hold back a release, so
"when and why did this verdict change" is itself under review.

---

## Run directory

```
.holoqa/
  runs/20260918-1430-shop/
    run.json        verdicts, notes, evidence index with SHA-256, revisions
    vars.json       captured bindings: {"order_id": "ord_88"}
    evidence/
      step-a1-screenshot.png
      step-a2-api-orders.json
    out/
      report.md
      checklist.xlsx            if meta.workbook is set
      20260918-1430-shop.zip    the deliverable
  history.jsonl     one line per completed run
```

Plain files. Inspect with `cat`, diff in git, no database, no migrations.

`history.jsonl` is what `holoqa_run_compare` reads to answer *what regressed
since the last green run* — plus flaky-step detection once a few runs exist.

### XLSX annotation

If `meta.workbook` is set, packaging copies that workbook and appends
**Status / Actual Result / Evidence** columns, matching rows by a step-id column
(`Step ID`, `Test ID`, `ID`, `Langkah`, or `No`). The original is never
modified.

---

## Troubleshooting

**`agent-browser is not on PATH`**
Install it: `npm install -g agent-browser`. HoloQA captures evidence through it
and cannot judge a step without captures. Run `holoqa doctor` to confirm.

**`plan is not valid YAML ... expected ',' or '}'`**
A value with `{braces}` or `[brackets]` inside a flow mapping needs quotes. The
error includes the fix.

**`step X uses {name} but no earlier step captures it`**
A `capture:` must come before the step that interpolates it, and `depends_on`
must point earlier in the file. Both are checked at load time.

**A step is `BLOCKED` and you expected `FAIL`**
An assertion could not be evaluated — usually a missing capture. Observe first,
then judge. This is rule 9 and it is intentional: absence of evidence is not
evidence of a defect.

**Captures return empty data and nothing errors**
Check for a `401` warning on the capture summary. Sessions often expire well
before their cookie does, and the symptom disguises itself as empty results
rather than an error. Re-authenticate in the browser before treating empty
responses as a product defect.

**`no active run`**
Pass `run_dir`, set `HOLOQA_RUN_DIR`, or start a run. With none of those,
HoloQA picks the newest directory under `.holoqa/runs/`.

**Windows:** `agent-browser` is a `.cmd` shim and is invoked through `cmd /c`.
JavaScript payloads are base64-encoded so no quoting survives to reach the
shell. If you shell out to HoloQA from Git Bash, note that MSYS rewrites
`/api/...` arguments into Windows paths — prefix with `MSYS_NO_PATHCONV=1`.

**Windows: `uv tool install` fails with a hardlink error (`os error 396`).**
On a OneDrive- or cloud-synced checkout, uv cannot hardlink across the synced
filesystem. Set the link mode to copy:

```bash
UV_LINK_MODE=copy uv tool install git+https://github.com/Ifarra/HoloQA
```

**`agent-browser` appears to hang under a pipe.** Its daemon inherits the write
end of a piped stdout, so a named session can print its result and never exit
under `| tail`. HoloQA is not affected — it captures CLI output through a
temporary file rather than a pipe — but if you are driving `agent-browser`
yourself, redirect to a file instead of piping.

---

## TUI activity and transcript

The activity line is authoritative for wrapper lifecycle: `STARTING`,
`RUNNING`, `CANCELLING`, `COMPLETE`, or `ERROR`. The transcript summarizes
provider events into readable messages such as “Agent is thinking” and
“Running command …”. Original provider events remain in `agent-events.jsonl`
when retained, while `y` copies the readable transcript to the clipboard and
`out/agent-log.txt`.

The TUI is interactive throughout: click a step or use `↑`/`↓` to inspect
earlier steps, press `f` to return to the live step, and click a group heading
to collapse or expand its tasks. Evidence rows open their files only after
HoloQA validates that they are inside the run directory; `x` copies a path and
`e` reveals it in the file manager. The Evidence and Assertions tabs support
keyboard selection and click-to-detail. Use `p`, `v`, and `/` to pause, filter,
and search the transcript. The header shortcuts, verdict `···` menu, and `?`
help panel are clickable as well as keyboard-driven. An error exposes readable
diagnostics and a safe provider retry action.

`Ctrl+P` (shown as `^p` in the header) opens the scrollable theme picker. Use
the arrow keys to move through the complete Textual theme catalog: the
highlighted theme is applied immediately as a temporary preview, `Enter` keeps
it, and `Esc` restores the previous theme.

For the complete interaction map and artifact-safety model, see
[docs/TUI.md](docs/TUI.md).

## Limitations

Stated plainly, because a testing tool that overstates itself is worse than
useless.

- **`sse` and `download` captures are unit-tested only.** The browser
  end-to-end suite covers `screenshot`, `dom`, and `api` against a real server.
  The other two need a real app with a stream and a blob export.
- **No file-content assertions.** You can require a download exists, but not
  that a PDF has non-empty pages. Steps like that need a human to read the
  evidence; mark them clearly in the plan.
- **Plans are per-application and hand-written.** That is a deliberate cost:
  the spec is what stops the agent grading its own homework.
- **`text_contains` and `text_not_contains` on a truncated page return BLOCKED**,
  not FAIL and not PASS. Page text is captured up to 20k characters; beyond that
  neither presence nor absence can be proven, and a guess in either direction
  would be the exact failure this tool exists to prevent.
- **Single local run at a time.** No concurrency, no queue, no shared state.

---

## Development

```bash
uv sync --dev
uv run pytest -q              # 94 tests (3 real-browser tests are opt-in)
uv run holoqa selftest        # 18 guardrail checks
uv run holoqa validate examples/wolvesight.plan.yaml
# Optional: run the three real-browser tests against an isolated session.
$env:HOLOQA_RUN_REAL_BROWSER="1"; uv run pytest -q tests/test_e2e_browser.py
```

Browser end-to-end tests skip automatically when `agent-browser` is absent, so
the offline suite stays green in CI.

```
src/holoqa/
  plan.py       load, validate, interpolate bindings
  run.py        run directory, evidence index, guardrails
  verdict.py    the assertion evaluator
  observe.py    agent-browser capture and redaction
  report.py     markdown, packaging, ZIP
  workbook.py   optional XLSX annotation
  history.py    cross-run comparison
  mcp.py        the ten tools
  cli.py        init / doctor / validate / status / selftest
```

`docs/DESIGN.md` covers the architecture, what was deleted from the previous
server-hosted version and why, and the decisions that changed during
implementation.
