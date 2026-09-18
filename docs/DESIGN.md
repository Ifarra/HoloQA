# HoloQA — design

> Supersedes `ARCHITECTURE.md`, `PRODUCT_PLAN.md`, and `MILESTONES.md`.
> Those describe a server-hosted platform that is being removed.

## What HoloQA is

A **local stdio MCP server** that turns a checked-in test plan into an
evidence-backed release checklist. The AI client drives the browser through
HoloQA; HoloQA captures the evidence, evaluates the plan's assertions, and
computes the verdict.

HoloQA contains no intelligence. It runs subprocesses, hashes files, matches
JSON, and writes a ZIP. Every judgement it makes is a comparison against a spec
a human wrote.

**Prior art:** `automated-pentest-platform/scripts/e2e-checklist/` — a working
31-step release checklist runner for Wolvesight. HoloQA generalizes it. Its
guardrails, verdict semantics, known-behaviour registry, and ZIP deliverable are
preserved deliberately; they were learned the expensive way.

## The one property that matters

> **The agent cannot write a passing verdict.**

The agent decides *what* to look at and writes the prose. HoloQA performs the
capture, owns the bytes, and derives PASS/FAIL from the plan. `BLOCKED` is the
only verdict the agent may assert, and it requires a written cause.

This is the single upgrade over the prior tool, whose `record.mjs` guardrail is
presence-based: it requires *a file*, not a file that supports the claim.

## Boundary

```
Claude Code / Cursor        the AI. Already paid for. Reads pages, decides
        |                   what to click, writes notes, cites KB entries.
        | MCP (stdio, local, no auth, no network)
        v
    HoloQA                  zero intelligence. Subprocess + hash + match.
        |                   Never makes a model call.
        | subprocess / fetch
        v
  agent-browser             deterministic CLI (Playwright underneath).
```

No API key. No provider config. No server, no Docker, no database, no port.
Install is one MCP entry plus `agent-browser` on PATH:

```jsonc
{ "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }
```

## The plan file

One YAML file per application, committed beside the code. This is the only
per-app artifact; the tool itself is never edited.

```yaml
meta:
  app: wolvesight
  base_url: ${STAGING_URL}
  language: id                 # language for generated notes and report
  workbook: ./Checklist_E2E_Staging.xlsx   # optional output template

known_behaviors:
  - id: KB-001
    title: SSE putus ~6s pada scan yang sudah selesai
    applies_to: [A5]
    verdict_hint: not_a_failure

stages:
  - id: A
    title: Mulai scan

steps:
  - id: A3
    stage: A
    title: Buat scan blackbox profil Standard
    depends_on: [A2]
    route: /new
    do: Pilih mode blackbox, profil Standard, submit.
    expect:
      - api:  { method: POST, path: /api/scans, status: 201 }
      - json: { scan_profile_id: 2, status: PENDING }
      - capture: { scan_id: $.id }
      - screenshot: required
    blocked_if: preflight blockers non-empty
```

`capture:` binds a value for later steps (`{scan_id}` interpolates into any
later `path`). This replaces the prior tool's manual `--id scan_id=<id>` and
turns the step ordering that `checklist-map.md` describes in prose into
something the tool enforces.

## Assertion set — closed

Deliberately small. HoloQA is not a general browser-automation engine; that
ambition is what produced the previous `browser_runner.py`.

| Kind | Evaluates |
| --- | --- |
| `url_contains` / `url_matches` | captured URL |
| `text_contains` / `text_not_contains` | captured visible page text |
| `api` | method + path + status of a captured response |
| `json` | subset match or JSONPath predicate against a captured body |
| `screenshot` | `required` — a non-empty image exists for this step |
| `changed` | two captures of the same target, taken apart, must differ |
| `capture` | bind `$.path` from a body into the run's variable table |

`changed` encodes a rule the prior tool enforced socially — *"Langkah yang
menguji perubahan (5, 13, 23) butuh dua bukti berjarak. Satu tangkapan tidak
membuktikan apa pun."* Now it cannot be skipped.

Anything not expressible here is `BLOCKED` with a cause, never a soft pass.

## Verdicts

| Verdict | Meaning | Who may assert it |
| --- | --- | --- |
| `PASS` | every assertion satisfied against HoloQA's own captures | HoloQA only |
| `FAIL` | ran, and an assertion was violated | HoloQA only |
| `BLOCKED` | could not run or could not be verified | agent, with a cause |

Carried over verbatim from `conventions.md`: **when torn between PASS and
BLOCKED, choose BLOCKED.** A checklist that is too loose is more dangerous than
one that is too strict — the document exists to hold back a release.

## Run directory

Plain files. No database. Inspectable, git-diffable, and already the shape of
the deliverable.

```
.holoqa/
  runs/20260918-1430-staging/
    run.json          steps, verdicts, notes, evidence index, revisions
    vars.json         captured bindings (scan_id, finding_id, ...)
    evidence/
      step-A3-scans.json        sha256 recorded in run.json
      step-A3-scans.png
    out/
      Checklist.xlsx            filled from run.json
      report.md
      run-20260918-1430.zip     the deliverable
  history.jsonl       one line per completed run, for regression comparison
```

Dropping SQLite deletes `runs.py` (366 lines) and `project_store.py` (185) and
removes the lost-update races in `record_agent_verdict` and `record_agent_event`
structurally rather than by locking.

## MCP tools — 10

Down from 18, with the browser-execution and live-monitoring surfaces gone.

| Tool | Purpose |
| --- | --- |
| `holoqa_plan_validate` | lint a plan file; no execution |
| `holoqa_run_start` | create run dir from a plan + meta (tag, commit, tester) |
| `holoqa_run_status` | current step, what is blocked, what remains |
| `holoqa_observe` | **HoloQA** captures: screenshot / api / sse / dom / download |
| `holoqa_judge` | evaluate a step's assertions against captures; returns verdict |
| `holoqa_block` | assert BLOCKED with a required cause |
| `holoqa_note` | attach a note, optionally citing a KB id |
| `holoqa_kb_add` | record a known behaviour discovered mid-run |
| `holoqa_run_package` | validate -> fill -> verify -> zip |
| `holoqa_run_compare` | diff against last green run: new failures, flaky steps |

Note the absence of any tool that accepts a `status` argument.

## Guardrails

Ported from `record.mjs`, which states them plainly: *"sengaja keras, jangan
dilonggarkan."*

1. `PASS` requires at least one evidence file that exists on disk and is non-empty.
2. `FAIL` / `BLOCKED` require a written cause.
3. Verdicts outside the three are rejected.
4. An evidence reference pointing at a missing file is rejected.
5. A changed verdict appends to `step.revisions` with the superseded note.
6. Sensitive headers (`cookie|authorization|token|secret|password|set-cookie`)
   are redacted in evidence. The ZIP gets shared.

New:

7. A step whose `depends_on` is unsatisfied cannot be judged.
8. A KB citation must reference a KB id that exists in the plan.
9. An assertion referencing an uncaptured observation yields `BLOCKED`, not `FAIL`.
10. The plan is validated statically — unknown assertion kinds, dependencies on
    later steps, and references to variables no earlier step captures are all
    refused before a run starts.

`holoqa selftest` exercises all ten against fixtures — the role `dry-run.mjs`
plays today — and must stay runnable with no staging, no browser, and no
network.

## Modules

```
src/holoqa/
  mcp.py        stdio server, 10 tool definitions             ~200
  plan.py       load, validate, interpolate bindings          ~180
  verdict.py    assertion set + evaluator     <- the heart    ~220
  observe.py    agent-browser + cookied fetch wrappers        ~260
  run.py        run dir, run.json, evidence, revisions        ~200
  report.py     xlsx fill (XML surgery) + md + zip            ~200
  history.py    history.jsonl, compare to last green          ~120
```

~1,400 lines. Dependencies: `mcp`, `pydantic`, `pyyaml`, `openpyxl`. No boto3,
no fastapi, no uvicorn, no websockets.

### Ported, not rewritten

| From | To | Why |
| --- | --- | --- |
| `record.mjs` guardrails | `run.py` | six rules, proven in production |
| `lib/common.mjs::redactHeaders` | `observe.py` | the regex is right |
| `session.mjs` expiry detection | `observe.py` | the 80-minute backend token inside a 24-hour cookie degrades silently into empty data — it must stay a first-class hazard |

**Windows note:** `agent-browser` is a `.cmd` shim; since Node 20 it needs a
shell, and `runAgentBrowser` guards arguments containing `"` or `%`. Python's
`subprocess` hits the same wall. Port the guard, do not rediscover it.

## Changed during implementation

Four decisions moved once the code met reality. Recorded here so the document
does not describe a system that was never built.

**No `holoqa_act` tool.** The open question was whether HoloQA had to perform UI
actions to work around Radix components ignoring plain clicks. Tested against
the prior tool's own `fixtures/ui-sandbox.html`: a JavaScript `.click()` leaves
the menu closed, while `agent-browser click` opens it. agent-browser 0.27.0
dispatches real pointer events, so `ui.mjs` is obsolete and actions stay with
the agent. The trust boundary only ever needed to cover observation.

**API captures run in-page.** The prior tool used an out-of-band `fetch` with a
manually copied cookie. Running the fetch inside the page inherits the browser's
own session, including HttpOnly cookies, which deletes the whole session-planting
subsystem. Session expiry survives as a 401 warning on the capture summary.

**No XLSX XML surgery.** Cell-level surgery preserved styles but welded the tool
to one spreadsheet layout — exactly the limit being removed. `workbook.py`
instead matches rows by a step-id column and appends three columns, which works
on any checklist workbook. The original is copied, never modified.

**Redaction extended to bodies.** The end-to-end test showed headers alone were
not enough: a captured `POST /login` wrote its password into evidence that ships
in a ZIP. `redact_payload` and `redact_text` now walk request and response
bodies too. Separately, browsers refuse to expose `Set-Cookie` to `fetch` at
all, so an in-page capture cannot leak it even before redaction runs.

## Deleted

Docker Compose (3 files), Dockerfiles (4), Garage and `deploy/garage.toml`,
boto3 and all S3/presigned-URL code, the Next.js app (30+ files, 25 npm deps),
the 486-line HTML dashboard in `dashboard.py`, the FastAPI service, WebSocket
live view, `scripts/holoqa_agent_bridge.mjs`, SSE streaming, findings triage,
environments CRUD, requirements CRUD, control-mode / human-takeover,
`demo_app.py`, `codegraph.py`, `execution_service.py`, `project_store.py`,
`runs.py`, and the legacy `legacy_state_database` / `.db`-sniffing parameters.

## Build order

**M0 — spine.** Plan loader, run dir, `holoqa_run_start` / `observe`
(screenshot only) / `judge` (screenshot-required only) / `run_status`.
Guardrails 1–5. `holoqa selftest`.
*Exit: one real step passes and one is correctly blocked.*

**M1 — assertions.** Full closed set, bindings, `changed`, `depends_on`.
*Exit: a PASS is impossible without a satisfied assertion.*

**M2 — capture breadth.** `api` (cookied fetch + redaction), `sse`, `dom` via
the Radix-safe driver, `download` via blob interception, session expiry
detection. *Exit: every capture kind the prior tool supported.*

**M3 — deliverable.** XLSX fill by XML surgery, a `verify_xlsx` equivalent,
markdown report, ZIP packaging, KB registry.
*Exit: a ZIP indistinguishable in usefulness from the prior tool's.*

**M4 — memory.** `history.jsonl`, `run_compare`, flaky-step detection.
*Exit: "what regressed since the last green run?" answered in one call.*

**M5 — proof of generality.** Port all 31 Wolvesight steps to
`wolvesight.plan.yaml` and run them against staging.
*Exit: results match a manual run, with no tool source edited.*

M5 is the real test. If the 31 steps do not express cleanly in the spec, the
spec is wrong — and we learn it in week one rather than month three.

## Done means better than e2e-checklist

1. Runs against a second application with no tool source change — plan file only.
2. The agent cannot write PASS. Verified by a selftest that tries.
3. Every prior guardrail still holds (evidence required, cause required,
   revision trail, redaction).
4. Assertions are machine-checked, not prose a human reads.
5. Cross-run regression comparison exists.
6. The deliverable is still a ZIP with a filled workbook and hyperlinked evidence.
7. `holoqa selftest` runs green with no staging and no network.

Anything less is a lateral move.

## Risks

**The spec cannot express a step.** Most likely on multi-page flows and timing
windows — the prior notes record that the enrichment window in step 12 is only
seconds wide. Mitigation: `BLOCKED` is always available, and M5 surfaces this
early. Do not respond by widening the assertion set into a scripting language.

**Capture-through-HoloQA is slower to iterate** than the agent calling
`agent-browser` directly. Mitigation: `holoqa_observe` takes a raw passthrough
mode for exploration, which never produces evidence and never feeds a verdict.

**The XLSX template is still per-app.** M3 keeps HoloQA's own markdown/JSON
report as the default and treats a workbook as an optional, configured output.
