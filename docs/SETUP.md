# `setup:` — second-actor preconditions, fixture preparation, and the rest of the tier list

> Status: **design, not implemented.** This document is the contract to build
> against. Nothing in it changes current behaviour until the code lands.

## Why, from the reviewer's numbers

The reviewer's three runs produced 118 BLOCKED out of 290 steps, and admitted a
large share was a plan/seed mismatch rather than a tool limit. Stripping that
out, the genuinely tool-shaped residue is:

| Cause | Steps | Fits which item below |
| --- | --- | --- |
| Needs a second actor acting inside the step (admin/proctor logged in *while* the user's session is active; a simultaneous session) | 31 | **`setup:`** |
| Needs a connected proctor whose presence unlocks a later screen (chain effect) | 15 | **`setup:`** |
| Needs another actor's resource id | 3 | **`setup:` + `capture:`** |
| Time / scheduler ("60s", "end of slot", "tomorrow 12:00") | 3 (+2 unjudged) | **`wait_until:`** |
| Browser/device/network limits (fake camera, no ws observation) | 6 | **`ws` capture, per-plan browser profile** |
| Fixture not buildable/checkable by machine | 63 | mostly plan hygiene; **`setup:`** covers the buildable part |

The two items that close the most steps are `setup:` (49) and `wait_until:`
(3–5). The rest are either plan hygiene or honest `strength: weak` marks.

The constraint every item below must respect, because it is the product:

> **HoloQA performs the action; HoloQA records the evidence; HoloQA derives the
> verdict. The agent never supplies a status, a path, or a session.**

`setup:` is not a hole in that wall. It is the wall extended one step earlier.

---

## 1. `setup:` — the design

### 1.1 Shape

A step gains a `setup:` list. Each entry is an **action** performed by HoloQA in
a named actor's session, before the step's own assertions are judged. An entry
that cannot be performed **blocks the step** with a cause HoloQA computed — not
one the agent wrote.

```yaml
steps:
  - id: F-01
    stage: F
    title: The exam tab unlocks once a proctor is connected
    actor: user
    depends_on: [E-09]

    setup:
      # A second actor logs in and becomes "connected". HoloQA captures the
      # response itself; `capture:` binds the id for the step below.
      - actor: proctor
        api:  { method: POST, path: /api/proctor/connect, status: 200 }
        capture: { conn_id: $.id }

      # Bind a value and assert the fixture is actually there. An unmet
      # `require:` is a BLOCKED with a machine-readable cause, exactly like
      # a step-level `requires:`.
      - actor: admin
        api:  { method: GET, path: "/api/sessions/{session_id}", status: 200 }
        require: { json: { exam_locked: false } }

      # Wait for a UI condition (same engine as holoqa_observe wait_for).
      - actor: user
        wait_for: "document.querySelector('[data-exam-tab][aria-disabled=false]')"

    expect:
      - api: { method: GET, path: "/api/exam/{exam_id}", status: 200 }
      - capture: { exam_id: $.id }
      - screenshot: required
```

### 1.2 Why each field exists

| Field | Purpose | Reviewer step it closes |
| --- | --- | --- |
| `actor` | which identity performs the action. Defaults to the step's own actor. This is what makes a *second* identity act inside one step. | A-12, A-15, B-07, C-12..C-14, E-05, E-06, E-09, I-04 |
| `api` | a capture the *second* actor makes. Runs in that actor's session, so its cookies are its own. | "admin/proctor must act while the user's session is live" |
| `capture` | binds a value from that response into the run's variable table — the answer to "I need another actor's session id" without DB access. | PRC-E-05, F-04, G-09 |
| `require` | a precondition on a value the setup produced. Unmet ⇒ BLOCKED with a stable cause, so 41 steps blocked the same way group into one line. | "fixture not present" |
| `wait_for` | a JS condition that must hold before continuing — the machine-checked form of "wait until the proctor is Online". | "Connecting…, Proctor: Offline" chain |
| `settle_ms` | a pause for an animation/poll after `wait_for`, same as observe. | poll-driven UI |
| `screenshot` | capture a screenshot in this actor's session as setup evidence. | audit trail |

### 1.3 Execution and verdict semantics

`setup:` is evaluated **inside `verdict.evaluate`**, before `requires:` and
before the step's assertions. It is not an MCP tool call the agent makes — the
agent cannot skip it, run it in the wrong session, or claim it ran.

Order within `evaluate` becomes:

1. **`setup:` actions** — executed for real by HoloQA. First failure ⇒ step is
   `BLOCKED`, cause = `setup <n> (actor <a>) <kind> not satisfied: <detail>`.
   Captures produced here are attached to the step with that actor's session, so
   the existing multi-actor soundness check applies unchanged.
2. **`requires:`** — unchanged; evaluated against all captures in the run.
3. **assertions** — unchanged.

This ordering matters: `requires:` checks captures that *already exist*, while
`setup:` *makes* them exist. A plan that needs a fixture it cannot observe
pre-made uses `requires:`; a plan that needs a second actor to act uses `setup:`.

**Nothing new can assert PASS.** Setup produces captures through the same
`Run.attach` path, so the ledger owns them and the hash is verified on read.
The only new outcome a setup can cause is BLOCKED.

### 1.4 The concurrency question, answered honestly

The reviewer's "simultaneous session" is the hard case. There are two readings:

- **Co-present session** (the actor must be *logged in and connected*, and stay
  that way while the step runs). `setup:` with `actor: proctor` establishes the
  proctor session and leaves it alive in agent-browser; the step then runs as
  `user`. agent-browser keeps named sessions alive between invocations, so the
  proctor session persists. **This covers the reviewer's 46 steps.**

  > **Verified, not assumed.** Probed against agent-browser 0.27.0 with a local
  > cookie-setting server: a `Set-Cookie` written in one CLI invocation is still
  > sent by the next *separate* invocation under the same
  > `AGENT_BROWSER_SESSION` (`cookie: sid=ABC123, authed: true`); a second
  > session name gets an isolated jar (`cookie: "", authed: false`); and the
  > first session is still authenticated *after* the second has acted. That is
  > the whole basis for `setup:`, so it was checked before designing on it
  > rather than after. (Confirmed with a hand-written HTTP server, because a
  > design built on a session mechanism that turns out not to persist would be
  > worthless.)


- **True simultaneity** (two actors must act *in the same instant* — a chat
  race, a lock contention). `setup:` cannot express this, and **it should not
  pretend to.** The plan marks such a step `strength: weak` with a reason, or it
  is restructured. Do not add a scripting language to chase it.

HoloQA remains one run at a time (`README` Limitations). `setup:` does not
change that; it sequences two identities within one step.

### 1.5 Static validation (guardrail 10 extended)

The loader must refuse, before a run starts:

- a `setup` entry with an unknown action kind;
- a `setup` entry naming an actor not in `meta.actors`;
- a `capture` in a `setup` whose name collides with a step-level `capture`;
- a `{variable}` referenced by a setup that no earlier step *or earlier setup in
  the same list* captures — setup actions are ordered, so an entry may use a
  binding a previous entry made;
- a `setup` `api` with no `status` (same rule as step assertions);
- `setup` on a step with `strength: weak` and no `weak_reason`, only warning.

### 1.6 Files touched

| File | Change |
| --- | --- |
| `plan.py` | `SetupAction` model (`actor`, `api`, `require`, `capture`, `wait_for`, `settle_ms`, `screenshot`); `Step.setup`; validators in `_validate_graph`; `FORMAT_REFERENCE["setup"]`; template comment |
| `mcp.py` | `_run_setup(plan, run, step)` helper reusing `observe_module.api/dom/screenshot/wait_for` and `run.attach`, resolving the session via the existing `_actor_session` (relaxed to allow the action's own actor) |
| `verdict.py` | call `_run_setup` at the top of `evaluate`; surface a `setup` Result on failure |
| `report.py` | setup failures already carry `blocked_cause` via the `requires`-style path, so root-cause grouping works with one line |
| `README.md` / `docs/DESIGN.md` | document `setup:`, update counts |

### 1.7 Tests (the ones that must exist)

| Test | Proves |
| --- | --- |
| `test_setup_second_actor_binds_id` | a setup captures an id from `proctor`; the step uses `{conn_id}` and passes |
| `test_setup_failure_blocks_with_cause` | a setup whose `require` fails ⇒ BLOCKED, note names the setup, `blocked_cause` non-empty |
| `test_setup_capture_tagged_with_actor` | evidence from setup is tagged `proctor`, and a step's assertion in `user` session does not read it |
| `test_setup_unknown_actor_refused` | loader refuses `actor: ghost` |
| `test_setup_forward_reference_refused` | loader refuses a setup using `{x}` no earlier setup captured |
| `test_setup_cannot_pass_without_evidence` | a step with setup but no captures still blocks, never passes |
| `selftest` guard | a guardrail count bump, with the mutation that turns it red |

---

## 2. `wait_until:` — declarative time control

3 steps + 2 unjudged wait on a scheduler (60s tick, end of slot, "tomorrow
12:00"). HoloQA cannot fast-forward the application's clock, so it must **wait
in a bounded, recorded way** rather than have the agent sleep and guess.

```yaml
meta:
  clock:
    # Facts HoloQA may reason about, never rewrite.
    slot_seconds: 60

steps:
  - id: ADM-G-13
    title: The 60s scheduler closes the slot
    wait_until:
      seconds_since: { since: /api/slot/opened_at, at_least: 65 }
      timeout_s: 180
    expect:
      - json: { state: CLOSED }
```

- `wait_until` polls a **condition**, refusing to proceed on timeout rather than
  capturing early (same discipline as `observe.wait_for`).
- Supported conditions, deliberately small: `seconds_since`,
  `after_time` (`"12:00"` wall clock, with `timezone`), `selector` (delegates to
  `wait_for`), `until_api` (poll an endpoint until a JSON predicate holds).
- On timeout the step is **BLOCKED with the plan's own cause** ("the slot had not
  closed within 180s"), which is countable and groupable.
- A step whose whole meaning is a real-time window it cannot control may still be
  `strength: weak`. `wait_until` reduces the *guesswork*, not the wall-clock cost.

## 3. `ws` capture — websocket observation

The reviewer's list of observe kinds (screenshot, dom, api, sse, download) has
no websocket. Add one, via CDP (`Network.webSocketFrameReceived` /
`webSocketFrameSent`), collected for `seconds` like `sse`:

```
holoqa_observe(step_id, kind="ws", path="/ws", seconds=15, ws_send='{"ping":1}')
-> { "url": "...", "sent": 0, "received": 4, "types": ["open","message","close"], "frames": [...] }
```

New assertion kinds it unlocks: `ws_frame` (a frame matching a JSON subset or
path arrived) and reusing `changed: ws` (two captures of the same socket differ).
This is what makes "the user is pushed a live update when the proctor connects"
provable instead of BLOCKED.

## 4. Per-plan browser profile — stop forcing fake media

`--use-fake-ui-for-media-stream` auto-approves camera permission, so a step that
tests **denying** permission (`USR-E-02`) can never run. Today browser args come
from `meta.browser.args` with no notion of a per-step profile. Add:

```yaml
meta:
  browser:
    profiles:
      normal:  { args: [] }
      nomedia: { args: ["--use-fake-ui-for-media-stream"] }   # the existing default
      realmedia: { args: [] }        # permission genuinely promptable
```

A step selects `browser_profile: realmedia`. The profile is applied for that
actor's session only. Two-faces / face-lost detection (PRC-F-05, F-07,
USR-F-05) stays out of reach unless a real or scripted video source is added —
mark those `strength: weak` rather than pretending.

## 5. Plan hygiene the tool cannot do for you

Half the reviewer's 118 were plan/seed mismatch. Ship a **`holoqa validate
--against-seed <dir|url>`** that checks each step's `requires:` targets against a
declared fixture manifest, and a `holoqa plan diff-seed` that reports steps whose
`requires:` point at data the seed does not contain. This turns "Run Admin dibuat
sebelum seed baru" from a human catch into a lint error.

---

## Build order

1. **`setup:`** — highest impact (49 steps), no new trust hole, reuses existing
   capture/session machinery. Land with the seven tests above and a selftest bump.
2. **`wait_until:`** — 3–5 steps, self-contained in `observe.py` + one evaluator.
3. **`ws` capture** — 6 steps at the network boundary; needs a real app to test.
4. **`browser.profiles`** — 3 steps; small, but keep the fake-media default.
5. **`validate --against-seed`** — no execution; catches the largest *category*
   of waste before a run starts.

Items 1–2 are the ones worth designing a PR around. 3–5 are follow-ups.

## What this design refuses to do

- No scripting language for timing or setup — the closed assertion set stays
  closed. Anything inexpressible is `strength: weak` or BLOCKED with a cause.
- No DB access for another actor's ids. `capture:` from a real capture is the
  only path.
- No speed-up of the application's own clock.
- No verdict, path, or session parameter reachable from the agent.
