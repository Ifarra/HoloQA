# HoloQA Desktop changelog

Desktop releases use `desktop-vX.Y.Z` tags. The Python engine has an independent
version line. Before 1.0, patch versions carry fixes; minor versions introduce
capabilities or compatibility changes, with breaking changes called out here.

## 0.2.0 — Unreleased preview

- Create a QA plan from a natural-language request using the existing Codex sign-in.
- Attach TXT, Markdown, text-based PDF, DOCX, and XLSX/XLSM requirements with bounded, cancellable extraction.
- Show generated plans as a visual journey/assertion review with an optional raw YAML editor.
- Let Codex ask focused human-in-the-loop questions during analysis before it submits a plan.
- Import external YAML plans, edit generated/imported YAML, and save a new project plan without overwriting existing files.
- Validate drafts through the execution schema; require a fresh full-plan review before any scan.
- Separate draft generation and document intake from QA lifecycles, with cancellation and visible provider-sharing disclosure.
- Bundle Python engine 2.1.0 with journey-oriented planning, browser lifecycle/resource safeguards, and lower-cost TUI refreshes.

## 0.1.1 — Preview — 2026-10-19

Desktop and browser/resource lifecycle work integrated into `main`.

- Synchronized desktop versions, checksummed releases, and verified-source publishing.
- Independent desktop and Python engine version lines; engine was 2.0.0 at this desktop release.
- Separate release/CI concurrency and no duplicate development push/PR builds.
- Windows cleanup regressions wait for worker completion before asserting cleanup.
- Release assets are built from the exact tagged main commit.

[Release and installation notes](https://github.com/Ifarra/HoloQA/releases/tag/desktop-v0.1.1).
The installer remains unsigned; interactive Windows acceptance is pending.

## 0.1.0 — Preview — 2026-10-19

First unsigned Windows x64 preview, bundling HoloQA engine 2.0.0.

- Open a local project folder and discover/review QA plans.
- Use existing Codex CLI sign-in for supervised QA runs and explicit approvals.
- Inspect captured evidence, engine-derived results, run history, and reports.
- Pause/resume in the same live session; stop and clean up provider/browser processes.
- Reject stale approvals/tools, changed reviewed plans, and redirected storage.

Source: `72bb3de257a1d40403044ec3c342f296da9954cc`.
[Release and installation notes](https://github.com/Ifarra/HoloQA/releases/tag/desktop-v0.1.0).

Interactive Windows acceptance with a real Codex/browser session is pending.
The installer is unsigned. No automatic app startup, in-app updater, or recovery
of active runs after desktop restart is included.
