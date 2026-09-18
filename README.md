# HoloQA

A local MCP server that turns a checked-in test plan into an evidence-backed
release checklist.

You drive the browser. HoloQA captures the evidence, evaluates the plan, and
computes the verdict — **you cannot record a pass.** That constraint is the
product: a filled checklist is only worth something if the thing being tested
did not also write the results.

No API key. No server, no Docker, no database, no port. HoloQA never calls a
model.

## Install

HoloQA is not on PyPI; install it from this repository.

```bash
uv tool install git+https://github.com/USER/holoqa      # provides `holoqa`
npm install -g agent-browser                            # the browser driver
```

`agent-browser` is a deterministic automation CLI (Playwright underneath). It
is not an AI agent and needs no API key.

Then one entry in your MCP client:

```jsonc
// ~/.claude.json  or  Cursor MCP settings
{ "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }
```

Check the setup before trusting it:

```bash
holoqa doctor       # is agent-browser present? what MCP entry do I need?
holoqa selftest     # 18 guardrails, offline, no browser, no network
```

<details>
<summary>Without installing (run from a clone)</summary>

```bash
git clone https://github.com/USER/holoqa && cd holoqa && uv sync
uv run holoqa doctor
```

MCP entry for a clone — use an absolute path to the repo:

```jsonc
{ "mcpServers": { "holoqa": {
    "command": "uv",
    "args": ["run", "--directory", "/absolute/path/to/holoqa", "holoqa"] } } }
```

</details>

## First run on your own app

```bash
cd ~/code/your-app
holoqa init --app your-app --url https://staging.your-app.test
```

That writes `holoqa.plan.yaml` with one working step and commented examples.
Edit it to describe real steps, then:

```bash
holoqa validate holoqa.plan.yaml
```

Then in your AI client: **"run the checklist in holoqa.plan.yaml using holoqa."**

Writing the plan is the one part that is yours. It is the contract that decides
every verdict, which is why a human owns it — and your agent can draft it for
you by calling `holoqa_plan_validate` with no argument to get the format, then
exploring the app. Review what it writes; commit it.

## How a run works

```
you ──drive the UI with agent-browser──▶ the application
 │
 ├─ holoqa_observe   HoloQA runs the capture and owns the bytes
 ├─ holoqa_judge     HoloQA evaluates the plan against those bytes → PASS / FAIL
 └─ holoqa_block     the one verdict you may assert, and it needs a cause
```

1. `holoqa_plan_validate` — lint the plan
2. `holoqa_run_start` — creates `.holoqa/runs/<stamp>-<app>/`
3. For each step: drive the UI yourself, `holoqa_observe`, then `holoqa_judge`
4. `holoqa_run_package` — report + evidence + optional XLSX, zipped
5. `holoqa_run_compare` — what regressed since the last green run

## The plan

One YAML file per application, committed beside the code. It is the only
per-app artifact; HoloQA's source is never edited.

```yaml
meta:
  app: shop
  base_url: ${STAGING_URL}

steps:
  - id: A3
    title: Creating an order returns 201 PENDING
    depends_on: [A2]
    route: /checkout
    do: Fill the cart, submit the order.
    expect:
      - api: { method: POST, path: /api/orders, status: 201 }
      - json: { status: PENDING }
      - capture: { order_id: $.id }
      - screenshot: required

  - id: A4
    title: The order reads back
    depends_on: [A3]
    expect:
      - api: { method: GET, path: "/api/orders/{order_id}", status: 200 }
      - text_contains: Thank you
```

`capture:` binds a value for later steps. `{order_id}` interpolates into any
later path, and the loader refuses a plan that references a variable no earlier
step captures.

> **Quoting:** inside a flow mapping `{ }`, values containing `{braces}` or
> `[brackets]` must be quoted — `path: "/api/orders/{order_id}"`,
> `capture: { id: "$.items[0].id" }`. HoloQA says so when you get it wrong.

`examples/wolvesight.plan.yaml` is a real 31-step release checklist ported from
a different application, including its negative tests and known behaviours.

### Assertions

Deliberately a closed set. HoloQA adjudicates; it is not a browser scripting
language.

| Kind | Checks |
| --- | --- |
| `url_contains` / `url_matches` | the captured URL |
| `text_contains` / `text_not_contains` | captured visible page text |
| `api` | method + path + status (status may be a list) |
| `json` | subset or JSONPath match against a captured body |
| `screenshot: required` | a non-empty image exists |
| `changed` | two captures, taken apart, differ |
| `capture` | bind `$.json.path` into the run |

Anything inexpressible here is `BLOCKED` with a cause — never a soft pass.

## Verdicts

| Verdict | Meaning | Who decides |
| --- | --- | --- |
| `PASS` | every assertion satisfied | HoloQA |
| `FAIL` | ran, an assertion was violated — the system is wrong | HoloQA |
| `BLOCKED` | could not run or could not be verified | you, with a cause |

**When torn between passing and blocking, block.** A checklist that is too
loose is more dangerous than one that is too strict; it exists to hold back a
release. One failed or blocked step means HOLD.

## Guardrails

Ten rules, all covered by `holoqa selftest`:

1. `PASS` requires a non-empty evidence file on disk
2. `FAIL` / `BLOCKED` require a written cause
3. Verdicts outside the three are rejected
4. An evidence reference to a missing file is rejected
5. A changed verdict keeps the superseded one in `revisions`
6. Credential-bearing headers **and request/response bodies** are redacted
7. A step with unmet `depends_on` cannot be judged
8. A known-behaviour citation must reference an id in the plan
9. An uncaptured assertion yields `BLOCKED`, never `FAIL`
10. The plan is validated statically before anything runs

Rule 6 matters more than it looks: evidence ships in a ZIP, so a captured
`POST /login` has its password stripped before the file is written.

## Run directory

```
.holoqa/
  runs/20260918-1430-shop/
    run.json        verdicts, notes, evidence index, revision trail
    vars.json       captured bindings
    evidence/       png and json, each hashed in run.json
    out/            report.md, checklist.xlsx, <run>.zip
  history.jsonl     one line per run, for regression comparison
```

Plain files. Inspect with `cat`, diff in git, no migrations.

## Development

```bash
uv sync --dev
uv run pytest -q
uv run holoqa selftest
holoqa validate examples/wolvesight.plan.yaml
```

The browser end-to-end tests skip automatically when `agent-browser` is absent,
so the offline suite stays green.

See `docs/DESIGN.md` for the architecture and the reasoning behind it.
