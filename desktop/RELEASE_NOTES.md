A **Windows x64 preview** of the HoloQA desktop workspace. This is a prerelease from the desktop development branch, built on the existing HoloQA engine. The installer is unsigned; it has not completed an interactive Windows acceptance pass with a real Codex QA session.

### Included

- Native project folder selection and recent projects.
- Plan discovery, starter plans, exact-byte review, and supervised Codex runs.
- Evidence-backed check results, captures, run history, and report export.
- Confirmed pause/resume within the same live desktop session, plus Stop and process cleanup.

### Install and run

1. Download the `HoloQA-Desktop-*-windows-x64-setup.exe` asset below and run it. WebView2 may need internet access during setup.
2. Install Codex CLI and sign in with `codex login` in the same Windows user account.
3. Install the QA browser driver with `npm install -g agent-browser`, then `agent-browser install`.
4. Start your target application, open its project folder in HoloQA, and review its plan before running QA.

The Python engine is bundled; a separate Python installation is not needed. Codex uses its existing sign-in. A plan may submit forms or modify test data, and project context follows the configured provider's data handling.

### Verification and limits

`SHA256SUMS.txt` checks the installer and `release-manifest.json`. The manifest records the exact source commit, successful build run, engine version, and installer hash. In PowerShell, compare `Get-FileHash .\HoloQA-Desktop-*-windows-x64-setup.exe -Algorithm SHA256` with the checksum file.

Automated tests cover engine guardrails, protocol interactions, pause/resume UI flows with a deterministic Codex peer, executable startup, and installer packaging. They do not substitute for interactive Windows acceptance with a real model/browser session. Codex app-server baseline: **0.159.2** (experimental dynamic tools).

Resume requires the owning desktop engine to remain alive; it does not freeze the application's state. The 30-minute session limit includes pauses. Automatic app startup, additional agents, signing, automatic updates, and cross-session recovery are future work.
