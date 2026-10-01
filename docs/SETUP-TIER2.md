# After `setup:` — time, websockets, media, and seed drift

> Companion to [SETUP.md](SETUP.md). That document covers the highest-impact
> item; this one covers the rest of the reviewer's residual list, in build
> order. Same contract as everything else in HoloQA: **the agent decides what to
> look at; HoloQA captures and adjudicates. No path, session, or verdict
> parameter is reachable from the agent.**

## Where these came from

Screenshotted against the reviewer's 118 BLOCKED, after removing the ~63 that
were plan/seed mismatch:

| Item | Steps | Tool-shaped? | This document |
| --- | --- | --- | --- |
| `setup:` (second actor inside a step) | 46 | yes | SETUP.md |
| Time / scheduler | 3 (+2 unjudged) | yes | §1 `wait_until:` |
| Browser/device/network limits | 6 | partly | §2 `ws`, §3 media profile |
| Plan/seed mismatch | 63 | **no — plan hygiene** | §4 `validate --against-seed` |

---

## 1. `wait_until:` — declarative time control

### 1.1 The problem, stated exactly

Three steps wait on a 60-second scheduler, the end of a slot, and "tomorrow
12:00". HoloQA is not allowed to move the application's clock — it runs
subprocesses and hashes files, it does not reach into the system under test. So
the only honest thing it can do is **wait for a condition, in a bounded way, and
record what happened** — never capture early, never sleep blindly.

The reviewer's current situation is worse than a limit: the agent *did* wait, by
hand, and the result is 2 steps "belum dinilai" and 3 BLOCKED whose justification
is prose. That is the same defect `requires:` fixed for fixtures: a decision the
agent makes in its head instead of the tool making it from evidence.

### 1.2 Shape

```yaml
meta:
  clock:
    timezone: Asia/Jakarta        # default; only used by after_time

steps:
  - id: ADM-G-13
    title: The 60s scheduler closes the slot
    wait_until:
      since: { api: { method: GET, path: "/api/slots/{slot_id}" }, field: $.opened_at }
      at_least_seconds: 65
      timeout_s: 180
    expect:
      - api:  { method: GET, path: "/api/slots/{slot_id}", status: 200 }
      - json: { state: CLOSED }

  - id: USR-D-03
    title: A reminder fires after the appointment time
    wait_until:
      clock: "12:00"             # wall clock, plan timezone
      timeout_s: 3600
    expect:
      - text_contains: Reminder sent
```

### 1.3 Supported conditions — closed, like the assertion set

| Form | Waits until |
| --- | --- |
| `{ since: {api|json|dom...}, at_least_seconds: N }` | at least N seconds have passed since the value the `since` assertion read — a timestamp or an epoch |
| `{ clock: "HH:MM", days_ahead: 0 }` | the wall clock in `meta.clock.timezone` reaches that time |
| `{ selector: "<css>", state: visible\|detached }` | a DOM condition (delegates to `observe.wait_for`) |
| `{ poll: <assertion>, every_s: N }` | an assertion becomes satisfied on a repeated capture |

Every form carries `timeout_s`. On timeout the step is **BLOCKED with the plan's
own cause** (`wait_until clock 12:00 in Asia/Jakarta not reached within 3600s`),
which is stable across runs and therefore countable — the whole point.

### 1.4 Why not just `time.sleep`

Because a sleep is unfalsifiable. A step that slept 65 seconds and then passed
proves the assertion held *at second 65*; a `wait_until` records the actual
elapsed time and the value it observed, so the evidence states "the slot was
still OPEN at 64.8s and CLOSED at 66.1s" instead of "we waited". The former is a
finding; the latter is a hope.

### 1.5 Implementation seams

- New module-level helper in `observe.py`: `wait_until(predicate, *, timeout_s,
  session, poll_s=2.0)` — reuses the polling loop already in `wait_for`, raising
  `CaptureError` on timeout exactly as `wait_for` does.
- `verdict.py` evaluates `wait_until` **before `setup:`** (a window may need to
  open before a second actor can act), and surfaces a `wait_until` Result on
  timeout.
- `plan.py`: validator for the closed condition set; `clock` is a `Meta` field.
- Tests: `test_wait_until_timeout_blocks_with_cause`,
  `test_wait_until_since_uses_observed_timestamp`, `test_wait_until_clock_respects_timezone`.

**Honest limit kept:** a step whose meaning is a real-time window HoloQA cannot
observe (a push that arrives 10 minutes later somewhere else) stays `strength:
weak`. `wait_until` removes the guesswork, not the wall-clock cost.

---

## 2. `ws` capture — websocket observation

### 2.1 The gap

`holoqa_observe` supports `screenshot | dom | api | sse | download`. There is no
websocket kind, so "the user is pushed a live update when the proctor connects"
— the exact chain the reviewer hit at `F-11`, `F-12`, `G-08` — cannot be
proven or disproven. It is BLOCKED by construction.

### 2.2 Shape

```
holoqa_observe(step_id, kind="ws", path="/ws", seconds=15, ws_send='{"ping":1}')
-> {
  "url": "wss://host/ws",
  "sent": 1, "received": 4,
  "types": ["open", "message", "close"],
  "frames": [{"dir":"recv","data":"{...}"}, ...]   # capped at 50
}
```

### 2.3 Implementation

CDP, not a page script — `EventSource` covers SSE but there is no page-level hook
for frames the app's own socket handles. agent-browser drives Chrome directly
over CDP, so the frames are observable **at the page target**:

- `Network.webSocketCreated` → the URL, to match `path`
- `Network.webSocketWillSendHandshakeRequest` / `...HandshakeResponseReceived`
- `Network.webSocketFrameSent` / `Network.webSocketFrameReceived` → frames
- `Network.webSocketClosed`

Frames go to evidence as JSON, redacted by the same `redact_text` walk that
covers api bodies (a token in a frame is a leaked token). `seconds` bounds the
collection like `sse`.

> **Verified, not assumed.** Probed against agent-browser 0.27.0 + Chrome 153
> with a local `websockets` echo server: attaching to the **page target**,
> sending `Network.enable`, then opening a socket from the page yielded
> `Network.webSocketCreated`, `...WillSendHandshakeRequest`,
> `...HandshakeResponseReceived` and **6/6** `Network.webSocketFrameReceived`
> events (9 total). So the frames this design needs are reachable.

> **The trap the probe found — do not skip this.** agent-browser launches Chrome
> with `--remote-debugging-port=0`, i.e. a **random port**, and its `--cdp <port>`
> flag is a *client-connect* option: passing it to `open` hangs waiting for
> something on that port. The `ws` implementation must therefore **discover** the
> port from the running browser rather than assume one — read it from agent-browser's
> Chrome profile dir (`DevToolsActivePort` under
> `%LOCALAPPDATA%/Temp/agent-browser-chrome-*/`), then attach. Hardcoding 9222, as
> a first draft would, fails on every machine that is not already running a
> debugger on 9222.


**Caveat to state in the README:** frame payloads from the app under test are
payloads from the app under test; `ws` proves a frame *arrived*, and the plan's
assertion decides whether its content is the one it wanted.

### 2.4 New assertions it unlocks

| Kind | Evaluates |
| --- | --- |
| `ws_frame` | a received frame's JSON matches a subset or JSONPath predicate |
| `changed: ws` | two `ws` captures of the same socket differ (extension of the existing `CHANGED_KINDS`) |

`CHANGED_KINDS` grows from `("dom", "api")` to `("dom", "api", "ws")` — one line,
and the existing `changed` evaluator already handles any kind it is given.

### 2.5 Test plan

Unit-testable without a real app by feeding a recorded CDP frame stream into the
formatter; the end-to-end test needs a real app with a socket (a local
`websockets` echo server). Add it to the opt-in `test_e2e_browser.py` group so CI
stays offline-green.

---

## 3. Per-plan media profile — stop forcing fake camera UI

### 3.1 The bug, not the limit

`--use-fake-ui-for-media-stream` auto-approves camera permission. A plan step
that tests **denying** permission (`USR-E-02`) can therefore never run — and the
tool's own flag made it impossible. That is a configuration defect, not an
inherent boundary.

### 3.2 Shape

```yaml
meta:
  browser:
    default_profile: nomedia
    profiles:
      nomedia:   { args: ["--use-fake-ui-for-media-stream"] }   # today's behaviour
      realmedia: { args: [] }        # permission prompts for real
      camera:    { args: ["--use-fake-device-for-media-stream",
                           "--use-file-for-fake-video-capture=/path/clip.y4m"] }

steps:
  - id: USR-E-02
    browser_profile: realmedia
    title: The user can decline the camera
    expect:
      - text_contains: Permission denied
```

- `default_profile` keeps today's behaviour for every existing plan, so nothing
  changes until a plan opts in.
- The profile is applied per **actor session** for that step only, and restored
  after — `observe.set_browser_options` already exists as the seam; it gains a
  profile argument rather than module-global state that leaks between steps.

### 3.3 What this still cannot do

Two-faces and face-lost detection (`PRC-F-05`, `F-07`, `USR-F-05`) needs a real
or scripted *video source* and a real detector. A green pattern is not a face.
Unless the plan ships a `.y4m` clip and the app's detector accepts it, those stay
`strength: weak` with a written reason — the tool must not let them look green.

---

## 4. `validate --against-seed` — catching the biggest category

### 4.1 The point

Most of the 63 fixture BLOCKED were not a tool limit. The reviewer said it
plainly: *"Run Admin dibuat sebelum seed baru. Plan Proctor menargetkan red-1,
sementara data seed ada di blue-1 dan red-4."* That is a plan referencing data
the seed does not contain, discoverable **before** a run starts for the cost of a
lint.

### 4.2 Shape

```
holoqa validate plan.yaml --against-seed ./seed/manifest.yaml
```

The seed manifest is a small checked-in file the team already knows how to write:

```yaml
# seed/manifest.yaml
users:    [user1, user4, user5]
proctors: [{id: blue-1}, {id: red-4}]
slots:    [{id: slot-open}, {id: slot-closed}]
```

`validate` then checks, statically:

- every literal id a step's `requires:`/`setup:` `path` names exists in the seed
  (`/api/proctors/red-1` → `red-1` is not in `proctors` → **error**);
- every `capture:`d variable used in a `path` is bound by an earlier step (already
  enforced) — now cross-checked against the seed to catch "bound to the wrong
  thing";
- steps whose `requires:` target a fixture class the seed does not declare at all
  (`fixtures: {}` and a step requires `/api/fixtures/...`) → **warning**.

Exit non-zero on error, like every other guardrail. No execution, no network.

### 4.3 Why this belongs in HoloQA and not in a script

Because the failure it prevents is *silent*. A plan referencing `red-1` against a
seed with `blue-1` produces 35 BLOCKED that look like tool limitations and get
reported as such — which is exactly what happened. Turning it into a lint error
in the same tool that owns the plan keeps the diagnosis where the contract is.

---

## Build order and effort

| # | Item | Steps closed | Risk | Effort |
| --- | --- | --- | --- | --- |
| 1 | `setup:` (SETUP.md) | 46 | medium — touches evaluate order | 1 PR |
| 2 | `wait_until:` | 3–5 | low — reuses `wait_for` | small PR |
| 3 | `ws` capture | 6 | medium — CDP, needs real app to test | 1 PR + e2e |
| 4 | `browser.profiles` | 3 | low | small PR |
| 5 | `validate --against-seed` | (prevents 63) | low — static only | small PR |

Do **2 and 5 first** if you want quick wins: both are small, both are static or
self-contained, and together they remove the two loudest categories. Then `setup:`
as the one substantial feature.

## Standing refusal

No item above adds a scripting language, a DB read, a clock rewrite, or a
parameter through which the agent can name a verdict, a session, or an evidence
path. Anything the closed set still cannot express is `strength: weak` with a
reason, or BLOCKED with a cause the plan wrote — never a soft pass.
