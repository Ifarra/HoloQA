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

- [Why](#why) · [Install](#install) · [Updating](#updating) · [Install with an AI agent](#install-with-an-ai-agent)
- [First run](#first-run)
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
holoqa selftest     # 30 guardrail checks, offline, no browser, no network
```

`doctor` prints the exact JSON to paste, and tells you what is missing:

```
holoqa           2.0.0
agent-browser    agent-browser 0.27.0

MCP client entry:
  { "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }
```

### Updating

```bash
holoqa upgrade --check     # is there a newer build?  (0 = current, 1 = update, 2 = unreachable)
holoqa upgrade             # install it
```

`upgrade` compares the commit uv recorded when HoloQA was installed
(`direct_url.json`, PEP 610) against the commit on `main`, then runs the install
for you. It downloads nothing itself — `uv` does the fetching.

**Close your MCP client first.** On Windows a running client holds files inside
the tool's environment and the update fails part-way, leaving HoloQA broken
until it is repaired. `upgrade` detects that and prints the recovery command
rather than leaving you to retry blindly:

```bash
uv tool install --reinstall git+https://github.com/Ifarra/HoloQA
```

`--reinstall` is not optional there: a failed attempt can leave the environment
incomplete, and a plain retry may report success without repairing it.

Then restart the client and confirm the build:

```bash
holoqa selftest        # the count is the version marker
```

The count changes as guardrails are added — 25 before the integrity work, 30
after — so a `selftest` that prints 25 means the update did not take.

<details>
<summary>Doing it by hand instead</summary>

`holoqa upgrade` is a thin wrapper around this. Useful when uv is not on the
PATH HoloQA sees, or when you want to pin a specific commit:

```bash
uv tool install git+https://github.com/Ifarra/HoloQA          # latest main
uv tool install "git+https://github.com/Ifarra/HoloQA@<sha>"  # a specific commit
```

Note that `uv tool upgrade holoqa` does **not** work: HoloQA is installed from a
git ref rather than a versioned index, so there is no version for uv to resolve
and it answers **"Nothing to upgrade"**. On a OneDrive- or cloud-synced checkout,
add `UV_LINK_MODE=copy` (see Troubleshooting).

</details>

**Your existing runs are not migrated, and that is deliberate.** A run created
before this version has no `plan.pinned.yaml` and no ledger, so HoloQA cannot
establish what it judged:

- `holoqa verify` reports it `unverified` and exits 2.
- Judging it is refused with *"this run has no pinned plan"* rather than guessed.
- `holoqa attest --run-dir <dir> --by <you> --reason <what you checked>` records
  that you read the evidence yourself, after which the run packages with
  `integrity: attested` and can decide again.
- Starting a **new** run with the updated tool gets the pinned plan, the ledger,
  and the hash checks.

There is nothing to migrate: the run directory is plain files, and the old ones
stay readable. Only their authority changes.

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

## Install with an AI agent

If you already have a coding agent (Claude Code, Cursor, Codex, OpenCode), it
can do the whole install for you. Paste one prompt and answer its questions.

### The prompt

```
Install HoloQA by running this exact command:
uv tool install git+https://github.com/Ifarra/HoloQA

Then install the browser driver it depends on:
npm install -g agent-browser

Verify both with: holoqa doctor

Then add this entry to your own MCP client config and tell me to restart you:
{ "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }

After restarting, work in this project and do the following:
1. Run `holoqa init --app <name of this project> --url <its local or staging URL>`
2. Read @README.md, @package.json and @docs/ (or whatever describes this app)
   to learn what the application actually does.
3. Open the application in a browser and explore it enough to know its real
   user journeys.
4. Fill in holoqa.plan.yaml: one step per user-visible behaviour, each with
   assertions from the format reference you get by calling holoqa_plan_validate
   with no arguments.
5. Run `holoqa validate holoqa.plan.yaml` until it reports no errors.
6. Show me the plan and explain every step you wrote before running it.
```

Replace `<name of this project>` and `<its local or staging URL>` with real
values, and point the `@`-mentions at the files that actually describe your app.

### What each part does

| Part | What it does, and why it is there |
|---|---|
| `Install HoloQA by running this exact command:` | Agents summarise and improvise. `exact` is the instruction that stops yours from rewriting the URL or reaching for PyPI, where HoloQA is not published. |
| `uv tool install git+https://github.com/Ifarra/HoloQA` | Installs the `holoqa` command. `uv tool install` puts it on your `PATH` in its own isolated environment, so it cannot disturb any project's dependencies. `git+https://…` means "install from this Git repo" — there is no `pip install holoqa`. |
| `npm install -g agent-browser` | HoloQA does not drive a browser itself. This CLI does, and HoloQA calls it as a subprocess. Without it, `holoqa doctor` fails and no capture can happen. |
| `Verify both with: holoqa doctor` | Makes the agent check its own work instead of assuming the install succeeded. `doctor` also prints the exact MCP JSON for *your* machine, which is more reliable than the copy in this README. |
| `add this entry to your own MCP client config` | An MCP server is not usable until the client knows about it. Only the agent knows where its own config lives (`~/.claude.json`, Cursor settings, `~/.codex/config.toml`), so it edits it rather than you hunting for the file. |
| `and tell me to restart you` | MCP servers are read at client startup. The agent cannot use HoloQA in the session that installed it, so it has to ask you to restart. |
| `{ "mcpServers": { "holoqa": … } }` | The entry itself. `"command": "holoqa"` is enough because `uv tool install` put it on `PATH`; the empty `"args": []` is the stdio default — no port, no auth, no network. |
| `work in this project` | Scopes the agent to the repository under test, so the plan lands beside the code it describes. |
| `holoqa init --app <name> --url <URL>` | Scaffolds `holoqa.plan.yaml` with one working step and commented examples. `--app` names the plan (it appears in the report title); `--url` is the base URL the plan's relative `path:` assertions resolve against. It refuses to overwrite an existing plan. |
| `Read @README.md, @package.json and @docs/` | The agent cannot write a plan for an application it has not understood. The `@`-mentions pull those files into its context; point them at whatever actually describes *your* app. |
| `Open the application in a browser and explore it` | A plan derived only from docs describes the app the docs claim, not the app that exists. Exploring first is what makes the steps match real screens and real routes. |
| `one step per user-visible behaviour` | The unit of a plan is a behaviour worth holding a release for — not a click. This is the sentence that keeps the plan from becoming a script. |
| `assertions from the format reference` | `holoqa_plan_validate` with **no arguments** returns the whole plan format: the 11 assertion kinds, `scope`, `requires`, `blocked_if`, actors, and a starter template. The agent reads the schema instead of guessing at it. |
| `holoqa validate holoqa.plan.yaml` | Lints the plan statically. It catches unknown assertion kinds, a missing `status`, forward references to variables no earlier step captures, and the YAML brace trap — all before anything runs. |
| `Show me the plan and explain every step` | The plan is the contract that decides every verdict, which is why a human owns it. This is the review gate; **do not skip it.** An agent-authored plan you have not read is an agent grading its own homework. |

### What you should check afterwards

- `holoqa validate holoqa.plan.yaml` → `"status": "ok"`, and read the warnings.
  A step whose only assertion is `screenshot: required` passes on the existence
  of a file, so the linter flags it.
- Every step's `expect:` should assert something a machine can check. A plan of
  screenshots is a photo album, not a checklist.
- Commit `holoqa.plan.yaml`. It is the reviewed artifact; the run output is not.

---

## First run

By hand, without an agent:

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
| `requires` | preconditions checked **before** the assertions, against captures that already exist. Unmet → `BLOCKED` with the plan's own cause, and the report groups those steps by root cause |
| `actor` | the browser identity this step runs in (see `meta.actors`) |
| `strength` / `weak_reason` | `weak` marks a PASS that still needs a human to read the evidence; the report counts them |
| `blocked_if` | when an assertion is **violated**, report BLOCKED carrying this cause instead of FAIL |

`blocked_if` is evaluated, not decorative. A step that declares one and then
fails its assertions is BLOCKED, because the plan itself says the environment
may not be able to reach that step — a feature flag that is off is not a defect
in the product. The violation is not hidden: the assertion's real detail is kept
in the report, after the plan's cause. A step with no `blocked_if` still reports
FAIL, so the downgrade is per-step and deliberate.

It can also be written on a single assertion, which is the narrower form and
the one to prefer when only part of the step depends on that flag:

```yaml
expect:
  - { api: { method: GET, path: /api/orders, status: 200 },
      blocked_if: the orders service is disabled in this environment }
  - text_contains: Checkout
```

Here a disabled orders service is BLOCKED, but a checkout page that failed to
render is still FAIL — a session that expired mid-step is a defect, not the
feature flag the plan named. A cause on the step covers every assertion that
does not override it.

### Multi-actor runs

A plan that tests ownership needs more than one identity, so a step declares
which one it runs in:

```yaml
meta:
  actors:
    admin:   { as: admin@example.test,   password: ${ADMIN_PASSWORD} }
    proctor: { as: proctor@example.test, password: ${PROCTOR_PASSWORD} }
    anon:    {}                       # no `as` means a clean, logged-out session
steps:
  - id: B1
    actor: proctor
    title: A proctor cannot see another user's findings
    expect: ...
```

Each actor gets its own isolated browser session, so an admin step and a proctor
step no longer share one cookie jar. Credentials must come from the environment
— a literal password in a plan is **refused**, because the plan is committed
beside the code. Evidence is tagged with the session that produced it, and a
step whose evidence was captured in the wrong session is `BLOCKED`, never
counted as a pass. The agent cannot choose the session: passing an `actor` that
the plan did not ask for is an error.

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
| `api` | `{method, path, status}` — `status` is **required**, and may be an int or a list | an `api` capture |
| `json` | subset or JSONPath match against the last body | an `api` capture |
| `header` | a response header: a name alone (must be present) or `{name, contains\|equals\|matches}` | an `api` capture |
| `file` | the contents of a download: `{name_matches, size_gt, size_lt, contains, magic}` | a `download` capture |
| `screenshot` | `required` — a non-empty image exists | a `screenshot` capture |
| `changed` | `dom` or `api` — two captures, taken apart, must differ | two captures |
| `capture` | `{name: $.json.path}` — binds a value for later steps | an `api` capture |

**A typo cannot weaken an assertion.** Unknown keys inside `api`, `file`, and
`header` assertions are refused at load, `api` requires a `status`, and every
regex is compiled when the plan is read. `{file: {size_gtt: 1000000}}` and
`{header: {name: cache-control, contain: no-store}}` used to load, silently
ignore the misspelled key, and report PASS.

**`api` path matching.** `path` is matched on path *segments*, so `/api/orders`
matches `/api/orders`, `/api/orders?page=2` and `/api/orders/88`, but never
`/api/orders-archive`. The query string is ignored. When that is still not
strict enough:

```yaml
- api: { method: GET, path: /api/orders, path_exact: true, status: 200 }
- api: { method: GET, path_regex: "^/api/orders/[0-9]+$", status: 200 }
```

**`scope`** pins an assertion to one capture when a step makes more than one
call. The name must match the capture's file stem, its target, or the target's
URL path **exactly** — `scope: orders` does not match `orders-archive`, because
a decoy whose name merely contains the wanted one used to decide the verdict.
A scope that matches more than one capture is `BLOCKED` rather than guessed:

```yaml
expect:
  - { api: { method: POST, path: /api/orders, status: 201 }, scope: create }
  - { json: { status: CONFIRMED }, scope: "/api/orders/88" }
```

**Negative tests** use a status list:

```yaml
- api: { method: POST, path: /api/orders, status: [400, 403, 409] }
```

**`changed`** proves something actually moved. Two byte-identical captures FAIL;
one capture is BLOCKED, not PASS. Add `scope` to compare the captures of one
subject rather than every capture of that kind — without it, a second call to an
unrelated endpoint counted as a change. Use it for progress bars, regenerated
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

Eleven. None accepts a verdict parameter, and none accepts a path to an
evidence file — both absences are enforced by tests.

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
| `holoqa_verify` | — | is this run's evidence trustworthy? `verified` / `unverified` / `tampered` |

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
holoqa [mcp|selftest|doctor|init|validate|status|verify|attest|upgrade|agent]
```

| Command | What |
|---|---|
| *(none)* / `mcp` | serve the MCP server over stdio |
| `verify` | report whether a run's evidence can be trusted (exit 0 / 1 / 2) |
| `upgrade` | update HoloQA from the repository (`--check` to only report) |
| `attest` | record that a human reviewed a run HoloQA cannot verify |
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

Ten rules, all covered by `holoqa selftest`, which runs 25 checks in total.

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

Rule 10 is where a typo stops being silent. Refused at load: an `api`
assertion with no `status` (it used to pass on any response, including a 500 on
a broken endpoint), an unknown key in an `api` / `file` / `header` assertion, an
invalid regex anywhere (it used to raise an uncaught `re.error` mid-judgement),
a `changed` naming a kind that does not exist, and a plan with **no steps** —
which used to validate, package, and decide `RELEASE`.

Plus the one that is not negotiable: **an agent can only assert `BLOCKED`.**

### Who owns the evidence

`run.json` is a *projection* of a ledger HoloQA holds in its own memory, not the
source of truth. That distinction is the whole reason a hand-written record
cannot pass:

- **The plan is pinned.** `holoqa_run_start` copies the plan into the run
  directory and judges against that copy. Editing the plan mid-run — the
  cheapest way to turn a FAIL into a PASS — is detected and refused.
- **Hashes are verified, not just recorded.** Every capture is re-hashed before
  it is read and before it is packaged. A file edited after capture is `BLOCKED`
  at judge time and refuses to package, instead of being judged on new bytes.
- **Only captures this process made are evidence.** A record appended to
  `run.json` by hand has no entry in the ledger, so the step blocks rather than
  reading it.
- **A verdict written into `run.json` is ignored.** The file is a projection, so
  editing `verdict: FAIL` to `PASS` — the shortest forgery there is, and one that
  leaves the evidence untouched — never reaches a decision.

A run read by a process that did not create it is reported as `unverified`
rather than trusted or rejected: nobody can tell a genuine `run.json` from a
forged one, so HoloQA says so and refuses to decide a release from it. A human
resolves that with `holoqa verify` and, having read the evidence, `holoqa
attest --by <you> --reason <what you checked>`.

Rule 6 matters more than it looks. Evidence ships in a ZIP, so a captured
`POST /login` has its password stripped before the file is written — headers
alone were not enough. Browsers also refuse to expose `Set-Cookie` to `fetch`,
so an in-page capture cannot leak a session cookie even before redaction runs.

Rule 5 exists because a release checklist is used to hold back a release, so
"when and why did this verdict change" is itself under review.

---

## Run directory

One workspace per application, and every run of it inside:

```
.holoqa/
  workspaces/
    shop/
      workspace.json          the index: plan, current run, run list
      runs/
        001-20260918-1430/    run #1 — kept, even after it failed
          run.json              verdicts, notes, evidence index with SHA-256
          plan.pinned.yaml      the plan as it was when this run started
          attestation.json      present only if a human accepted a run
          vars.json             captured bindings: {"order_id": "ord_88"}
          evidence/
            step-a1-screenshot.png
            step-a2-api-orders.json
          out/
            report.md
            integrity.json      how far this run's evidence can be trusted
            001-20260918-1430.zip   the deliverable
        002-20260918-1512/    a retest — a NEW run, never an overwrite
      .trash/                 deleted runs, moved here rather than unlinked
  history.jsonl               one line per completed run
```

Plain files. Inspect with `cat`, diff in git, no database, no migrations.

### Why runs are numbered instead of timestamped

A run used to *be* a directory named `<stamp>-<app>`, and HoloQA refused to reuse
a name. Two consequences, both bad:

* two runs in the same minute **collided**, and the second was refused;
* fixing a bug and retesting meant **deleting the failing run** — destroying the
  evidence that the fix was needed.

So the workspace now **allocates** a run id (`001`, `002`, …) and derives the
folder from it. A retest appends; nothing is overwritten and nothing has to be
deleted. A failing run is a record, not garbage — it is the baseline the next run
is compared against.

`workspace.json` holds a `current_run` pointer, so "the run I am in" is what you
switched to, not merely the newest.

```bash
holoqa runs                    # every run in this workspace, newest first
holoqa runs --all              # include runs from the old flat layout
holoqa runs --json             # the same rows, machine-readable
holoqa workspaces              # every workspace, with its current run
holoqa retest --plan shop.plan.yaml   # new run; the previous one is kept
holoqa workspace migrate --plan shop.plan.yaml --dry-run   # old layout, opt-in
```

Runs in the old `.holoqa/runs/` layout stay readable and retestable; nothing is
moved unless you run `workspace migrate`, which prints its plan first.

`history.jsonl` is what `holoqa_run_compare` reads to answer *what regressed
since the last green run* — plus flaky-step detection once a few runs exist.

### Switching runs in the TUI

Press `s` for the session picker: every run of the workspace, with its decision,
counts, age and evidence integrity. `Enter` opens one (`View`), `g` continues the
one **this process** is driving, `t` retests, `Esc` closes.

Press `w` for the **workspace picker** — one row per application, each with its
run count, last decision and plan file. Selecting one switches both the runs
*and* the plan, which is what makes a single console able to own more than one
application.

Switching to an older run is read-only by design — a process that did not capture
a run cannot vouch for it, so it reports `unverified` rather than pretending
otherwise. The picker shows that state instead of hiding it.


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
- **Websocket frames cannot be captured yet.** `observe` supports `screenshot`,
  `dom`, `api`, `sse` and `download`. A step whose evidence is a pushed socket
  frame has to block with a cause. `docs/SETUP-TIER2.md` designs the capture;
  it is not built.
- **Download contents can be asserted, page contents cannot be rendered.** A
  `file:` assertion checks a downloaded file's name, size, magic bytes and text
  contents, so an exported CSV can be proven non-empty and correct. A PDF's
  *rendered pages* are still a human's job — mark such steps `strength: weak`.
- **Plans are per-application and hand-written.** That is a deliberate cost:
  the spec is what stops the agent grading its own homework.
- **`text_contains` and `text_not_contains` on a truncated page return BLOCKED**,
  not FAIL and not PASS. Page text is captured up to 20k characters; beyond that
  neither presence nor absence can be proven, and a guess in either direction
  would be the exact failure this tool exists to prevent.
- **Single local run at a time.** No concurrency, no queue, no shared state.
- **Integrity is enforced against a process, not against the operating
  system.** HoloQA's guarantee is that *an agent cannot make HoloQA write a
  PASS*, and the ledger plus the pinned plan plus the hash check deliver that.
  It is not a defence against an actor with your user account and the intent to
  forge a whole run: the same permissions that let an agent write a file let it
  write a consistent one. What such an actor cannot do is make the forged run
  look `verified` — a run nobody watched over is reported as `unverified`, and
  an unverified run cannot decide a release.
- **A run whose server restarted is `unverified` until a human attests it.**
  The ledger lives in the process, so a fresh server cannot tell a real
  `run.json` from a forged one. `holoqa verify` says which of the three states a
  run is in; `holoqa attest` records a human's review.

---

## Development

```bash
uv sync --dev
uv run pytest -q              # 244 tests (3 real-browser tests are opt-in)
uv run holoqa selftest        # 30 guardrail checks
uv run holoqa validate examples/wolvesight.plan.yaml
# Optional: run the three real-browser tests against an isolated session.
$env:HOLOQA_RUN_REAL_BROWSER="1"; uv run pytest -q tests/test_e2e_browser.py
```

Browser end-to-end tests skip automatically when `agent-browser` is absent, so
the offline suite stays green in CI.

```
src/holoqa/
  plan.py       load, validate, interpolate bindings
  run.py        run directory, evidence index, guardrails, ledger
  verdict.py    the assertion evaluator
  observe.py    agent-browser capture and redaction
  workspace.py  one workspace per app: run ids, current run, retest, migrate
  report.py     markdown, packaging, ZIP
  workbook.py   optional XLSX annotation
  history.py    cross-run comparison
  mcp.py        the MCP tools
  cli.py        init / doctor / validate / status / runs / retest / selftest
```

`docs/DESIGN.md` covers the architecture, what was deleted from the previous
server-hosted version and why, and the decisions that changed during
implementation.
