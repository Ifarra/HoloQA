# HoloQA Desktop

A Windows desktop workspace for local projects, evidence-backed QA plans, and
supervised browser scans. The Tauri 2 / React / TypeScript interface runs the
Python engine as a persistent stdio sidecar.

## Implemented features

- Native folder selection and recent projects; bounded, read-only plan discovery.
- Existing plan loading, validation, warnings, full check review, and exact-byte
  review tokens. A changed plan or resolved target requires a fresh review.
- Prompt-based plan drafts with Codex, Claude Code, or OpenCode; reference document attachments and external YAML import.
- Editable draft previews, schema validation, explicit new-file save, and mandatory plan review.
- Explicit starter-plan creation that refuses to overwrite an existing file.
- Codex and Claude initialization with existing account-state checks. No credential
  file reads, token copies, global configuration edits, or interactive login.
- Supervised Codex and Claude execution with provider-specific approvals,
  source-change refusals, pause/resume, cancellation, and a 30-minute session limit.
- In-process HoloQA tools for browser operation, capture, and judgment; tool calls
  cannot choose another run or assert PASS/FAIL. The same sidecar owns the ledger
  when navigating between screens.
- Saved runs, engine-derived status and integrity, captured artifact viewing,
  and engine report packaging. Historical runs cannot authorize release simply
  because their saved steps say PASS.
- Windows x64 sidecar packaging, an executable smoke check, and NSIS installer CI.

Pause requests provider interruption and rejects outstanding approvals.
The interface shows **Pausing** until interruption is confirmed and an in-flight
QA tool finishes. **Paused** retains the same browser, pinned plan, provider
thread, and owning Python ledger; resume starts another turn on that same thread.
It does not freeze the target application's timers, sessions, or remote state.
Resume asks the agent to inspect run/browser state before continuing. The
30-minute wall-clock limit includes pauses. Stop is available while paused and
cleans up the provider/browser. Unconfirmed interruption times out to Stop;
disconnected sessions fail closed. Resume after closing/restarting the desktop
is not supported, and saved history does not restore trusted evidence authority.
Session controls remain visible when inspecting another run. Project switching,
new runs, plan creation, and report export are blocked during an active session.

This is an unsigned preview. Interactive acceptance with real Windows provider
and browser sessions remains pending.

## Windows development

Install Node.js 22+, Rust with the MSVC toolchain, Visual Studio C++ Build Tools
with a Windows SDK, WebView2, and Python 3.11+ with uv. Tauri uses WebView2 for
the application window; QA still uses agent-browser's separate browser runtime.

From the repository root in PowerShell:

```powershell
uv sync --dev --group desktop --locked
cd desktop
npm ci
$env:HOLOQA_DESKTOP_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe).Path
npm run desktop:dev
```

The development interpreter override is disabled in release builds. Release
builds launch the bundled engine alongside the installed desktop executable.
`desktop:dev` loads the development config without an external-binary packaging
requirement, so iterating against the interpreter does not require rebuilding
the PyInstaller executable. The development CSP permits Vite's loopback HMR;
the release CSP permits only packaged scripts and the native IPC transport.

For QA, install and sign into either Codex CLI or Claude Code in the same
Windows user account. Codex uses the app-server adapter (baseline
**codex-cli 0.159.2**) with per-command approvals. Claude uses its stream-json
CLI with a strict allowlist of HoloQA evidence tools; it cannot run arbitrary
commands or edit project files. Both support pause, resume, stop, and the same
HoloQA evidence ledger. Claude resumes with the same CLI session after a pause.

```powershell
codex login
# Or install Claude Code and sign in with `claude`.
npm install -g agent-browser
agent-browser install
```

Use Agent & target → choose a scan agent and check its connection. You can start
the application yourself or enter an explicit project startup command (npm,
pnpm, yarn, bun, uv, or Python); HoloQA starts it only after you click and waits
for the target URL to respond. The command runs without a shell and is stopped
when you switch projects or close HoloQA. Open a project, choose its plan, read
all checks, and approve it. The preflight verifies that the reviewed plan is
unchanged, the target responds, the browser driver is installed, and the selected
provider is signed in. A run may submit forms or modify test data.

The plan composer saves prompt and YAML edits in this device's local browser
storage and offers to restore them when reopened. After a desktop restart, open
the project and choose **Reopen run & evidence** to inspect its latest saved run;
a live provider session can resume only while the desktop process remains open.

Codex runs with read-only sandboxing and user-routed command approvals. HoloQA's
hosted tools perform the intended browser actions and evidence writes. Native
commands may be requested by Codex; review their full command, directory, and
reason before allowing them. Claude can call only HoloQA's named evidence tools;
it has no shell or source-edit permission. Source-edit requests are denied during
QA. Unknown provider requests receive an error without permissions being granted.
Prompts and project context sent to Codex follow your configured provider's data
handling; a local engine does not mean local model inference.

## Create a plan from a prompt or documents

Open a project and select **Create plan**. Enter an application name, its running
HTTP(S) URL, and what to test. **Attach documents** accepts UTF-8 TXT/Markdown,
text-based PDFs (up to 100 pages), DOCX body text, and XLSX/XLSM workbook values.
Images, DOCX comments, headers/footers, spreadsheet formulas/macros/images, and
other embedded objects are not interpreted. Scanned PDFs are passed through as
unreadable documents; no OCR is required by HoloQA.
Limits: eight attachments, 5 MiB per file, 15 MiB combined source bytes, and
100,000 extracted characters combined. Extraction runs in a separate process
with runtime/output limits and can be cancelled. A rejected batch attaches no
partial results. Original files stay in place; extracted text and attachment
metadata live in memory until the project changes or the desktop closes.

**Generate plan with [agent]** explicitly sends the request and selected
extracted document text to the chosen provider's existing CLI sign-in. Plan
drafting supports Codex, Claude Code, and OpenCode. Do not attach
secrets you do not intend to send. Drafting has a separate ephemeral provider
thread, a five-minute deadline, and no HoloQA browser/evidence tools. Shell, browser, search, plugin, hook, and inherited MCP tools are disabled for
drafting; shell/file-change approvals are declined. HoloQA passively reads Codex
configuration to identify MCP server names, discards configuration values, and
stores no credentials. Drafting cannot start a QA run. Project switching and
QA starts remain blocked until authoring finishes or is cancelled.

Review the generated plan in the visual overview, with each journey, route, and
assertion shown as a separate card. **View raw YAML** remains available for
advanced edits; choose a filename such as `checkout.plan.yaml`.
**Save and review** validates the edited bytes and creates a new file in the
project root; an existing file is never overwritten. No review token is issued.
Read the checks on **Test plan**, approve their expected results, then start the
supervised run separately. Generating a replacement draft replaces the previous
proposal, so save a draft you want to keep first. **Import plan file** provides
the same editable preview/save/review flow for existing YAML, without a model
call. Plans already discovered inside the project can still be loaded directly.

During analysis, the selected planning agent can pause with a focused question
when a requirement or
repository decision is ambiguous. Choose one of the offered answers in the
HITL panel and the same draft thread continues; cancelling the draft also
cancels a pending question. This keeps uncertain assumptions out of the saved
plan and gives the user the final decision before review.

## Build a Windows installer

From the repository root on **x64 Windows**:

```powershell
uv sync --dev --group desktop --locked
uv run python desktop/scripts/build_engine.py
uv run python desktop/scripts/smoke_engine.py desktop/src-tauri/binaries/holoqa-desktop-engine-x86_64-pc-windows-msvc.exe
cd desktop
npm ci
npm run desktop:build
```

The installer appears under `desktop/src-tauri/target/release/bundle/nsis/`.
`.github/workflows/desktop.yml` executes the engine tests and these build steps
on Windows. Its artifact is an **unsigned development installer**; signing,
automatic updates, and public release distribution are not part of this spike.
The WebView2 bootstrapper can require network access during installation.
The PyInstaller one-file engine extracts into the user's temporary directory;
test startup latency and endpoint-protection compatibility on the target PC.

## Architecture and protocol

```text
React / Radix UI
  → typed Tauri invoke commands
  → Rust allowlisted bridge + native folder picker
  → persistent Python engine (JSON lines; protocol 1)
      → Codex app-server (JSON-RPC over stdio)
      ← explicit approval requests / dynamic tool calls
      → HoloQA tools in this same Python process
      → existing browser capture, ledger, judgment, and reporting
```

Each request is `{id, method, params}` and each response is `{id, result}` or
`{id, error: {message}}`. Events use `{event, data}`. Stdout is protocol-only.
The native boundary allows `project.open` only through the system picker; the
web UI can reopen only already-recorded recent roots. All subsequent plan/run/
artifact paths are confined to the selected project. Project scripts are never
executed while discovering files. Scanning stops at depth three / 5,000 files /
50 plans and ignores dependency/build/run directories and directory symlinks.
Project metadata reads require bounded regular JSON files. Before starting a
run or packaging a report, nested storage links/junctions and nonregular files
are rejected; storage inspection stops at 20,000 entries. This protects normal
project-folder use and is not a filesystem sandbox against a concurrent hostile
process changing paths after validation.

Only registered captured artifacts can be previewed, their hashes are checked,
and the viewer permits inert PNG/JPEG or escaped text. Arbitrary paths, SVG,
HTML execution, shell plugins, remote webview navigation, and renderer-wide
filesystem access are not exposed. CSP restricts the webview to packaged code,
IPC, and inline image data. UI preferences/recent paths are stored in the native
application-data directory; plans and runs remain in the project.

Windows provider processes are created suspended, assigned to a Job Object with
kill-on-close, then resumed. This includes descendants that outlive their parent.
Explicit stop kills the provider tree, waits for in-flight bounded tool calls,
then cleans up browser sessions. The native exit handler requests sidecar
shutdown before terminating it; if that process dies, Windows closes its Job
handle and terminates the provider tree. Navigation never restarts the sidecar.
The native shell additionally owns the sidecar and all browser descendants in
an outer kill-on-close Job. If graceful cleanup exceeds its exit deadline, this
outer Job terminates the whole remaining tree instead of leaving a browser alive.

The desktop does not attest historical runs or promote saved JSON to trusted
state. For this milestone human attestation remains an explicit engine CLI
workflow. The default UI inspects such runs and displays their engine-reported
integrity and HOLD decision.

## Verification and remaining work

```powershell
uv run pytest -q
uv run holoqa selftest
cd desktop
npm run build
npm run test:ui
```

The deterministic Codex peer tests prove initialization, blocking approval
requests, answer routing, bounded tool dispatch, pause confirmation, same-thread
resume, stale-tool/completion refusal, unknown-request refusal, and process-tree
termination without model calls. Engine tests cover review invalidation, path
confinement, preservation of in-process capture authority, unverified history,
UTF-8 stdio, nested storage confinement, failed-retest preservation, and starter-plan creation. The existing engine suite remains required.
The UI smoke uses the real Python stdio engine and substitutes only the native
folder picker/IPC transport. Install Chromium with `npx playwright install chromium`
first, or set `CHROMIUM_EXECUTABLE` to an existing Chromium executable. The test
proves that an external plan edit invalidates a reviewed launch before provider
execution. It also exercises approval → pause → resume → pause → stop using the
real engine and adapter with a deterministic protocol peer. That peer does not
run shell actions or contact a model. The test does not exercise native WebView2.

A real Windows acceptance pass is still necessary: install the NSIS artifact,
open a folder containing spaces and non-ASCII characters, sign into a supported
Codex or Claude CLI, exercise Codex approval choices, run a local fixture, inspect
its capture, pause/resume both providers, cancel mid-run, and close the window with a provider child alive.
CI packaging and a fake peer do not substitute for that interactive test.

This foundation does not yet include OpenCode scan support, live browser embedding,
provider login recovery, or cross-session active-run reattachment.
Starter plans need real assertions edited by the project owner. Run comparisons
are currently the saved counts shown together; richer per-check diffs follow
after the first Windows proof. Packaging and provider behavior need validation
before enabling production use.

Initial provider initialization can hold the session lock until a protocol request
times out; immediate preflight cancellation remains follow-up work.

## Versions and GitHub Releases

Desktop and engine versions are independent. The current desktop preview is
`0.3.0`; the bundled Python engine is `2.1.0`. Desktop tags use `desktop-vX.Y.Z` so they do not collide with engine releases.
[Desktop changelog](CHANGELOG.md) records each version. Before 1.0, patch
versions carry fixes and minor versions introduce capabilities or compatibility
changes; document breaking changes explicitly.

`desktop/package.json` is the desktop version source. Synchronize and check all
shipped desktop metadata with:

```powershell
python desktop/scripts/version.py --set 0.3.1
python desktop/scripts/version.py --check
python desktop/scripts/version.py --check --tag desktop-v0.3.1
```

This updates the npm manifest/lock, Tauri config, Rust manifest/lock, and Python
app-server client version. It leaves the Python engine version and dependency
versions alone. Use three numeric components; preview status is a GitHub
prerelease flag so Windows installer version comparison stays predictable.

After the PR checks pass and the release commit reaches main, tag that exact
commit:

```powershell
git tag -a desktop-v0.3.1 <release-commit> -m "HoloQA Desktop 0.3.1 Preview"
git push origin desktop-v0.3.1
```

The tag workflow verifies that tag and metadata agree, reruns Windows engine/UI
checks, builds/smokes the sidecar, builds the installer, and publishes a GitHub
**prerelease** with a stable installer filename, SHA256SUMS.txt, and a manifest
recording the exact source commit and successful build run. Publication runs
only after the Windows job succeeds. Existing tags/assets are never silently
replaced: fix a failed release, then use a new version if it was published.
Preview releases do not become GitHub's latest stable release.

Download previews from [GitHub Releases](https://github.com/Ifarra/HoloQA/releases).
The installers are unsigned; check the release notes before installing. GitHub
Actions artifacts are temporary build outputs; release assets provide the
versioned download page. Automatic in-app updates are not enabled.

The first `desktop-v0.1.0` preview uses the already-passing installer from source
commit `72bb3de`; the release automation is added in the follow-up development
commit. Future tags must include this workflow to publish automatically.

Maintainers can also promote an already-successful Windows build with the
workflow's `publish_existing`, `source_run`, and `release_tag` inputs. The job
verifies repository, workflow, successful completion, exact tagged commit, and
tagged desktop version before downloading the artifact. It records the engine
version from that source commit, not from the workflow's newer checkout. It
refuses to replace a published release. This supports publication of a tested
build without recompiling it or moving its tag.

Windows CI now runs once per PR update, on main pushes, and on desktop tags.
It no longer starts duplicate push/PR builds for the desktop development branch.
Historical failed/cancelled runs remain visible; they are not current failures.
