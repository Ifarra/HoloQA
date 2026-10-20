A **Windows x64 preview** of HoloQA Desktop 0.3.1. It bundles the HoloQA
2.1.0 engine and supports supervised QA with Codex CLI or Claude Code using
their existing sign-in. This prerelease installer is unsigned and has not
completed interactive Windows acceptance with a real provider and browser.

### What’s included

- Open a local project folder and create plans from a prompt or existing
  requirements. Attach TXT, Markdown, text-based PDF, DOCX, XLSX, or XLSM files;
  HoloQA extracts bounded text and workbook values without OCR.
- Answer focused questions during plan creation, review the plan in a visual
  interface or inspect its YAML, save it, and approve every check before a run.
- Run supervised Codex or Claude scans, inspect evidence and saved history, and
  pause, resume, or stop within the same live desktop session.
- Edit project environment values and optionally start an application with an
  explicit command.
- Use the refreshed HoloQA wordmark and app icon throughout the desktop and site.

### Install and run

1. Download `HoloQA-Desktop-0.3.1-windows-x64-setup.exe` below. WebView2 may need
   internet access during setup.
2. Install either Codex CLI or Claude Code and sign in as the same Windows user
   who runs HoloQA (`codex login` or `claude`).
3. Install the QA browser driver and browser runtime with
   `npm install -g agent-browser` and `agent-browser install`.
4. Start your target application, open its folder in HoloQA, then create or
   review a plan and complete the launch readiness checks.

HoloQA bundles the Python engine. Plans, captures, and run history stay in the
project folder; prompts and attached project context are sent to the selected
provider under its data handling terms. Claude is restricted to named HoloQA
evidence tools. Codex uses its supervised app-server adapter and per-command
approval flow. Neither provider can author HoloQA verdicts.

### Verification and limits

`SHA256SUMS.txt` checks the installer and `release-manifest.json`. The manifest
records the exact source commit, successful build run, engine version, and
installer hash. In PowerShell, compare
`Get-FileHash .\HoloQA-Desktop-0.3.1-windows-x64-setup.exe -Algorithm SHA256`
with the checksum file.

Automated engine, protocol, UI, executable startup, and installer checks run in
CI. They do not substitute for interactive Windows acceptance with a real
provider and browser session. The installer is unsigned. Paused runs require
the same desktop session to remain open; closing or restarting the app does not
restore a live provider session. Automatic updates and code signing are not
included.
