# HoloQA changelog

## 2.1.0 — Unreleased

- Plan related checks as staged user journeys, reuse actors and captured evidence where appropriate, and retain distinct verdicts for meaningful state changes.
- Reduce browser contention with per-session command serialization while allowing separate actor sessions to proceed independently; constrain excessive tabs and clean up owned sessions safely.
- Avoid repeated TUI parsing and integrity hashing on every refresh, while periodically rechecking completed-run integrity.
- Clarify that browser session and tab limits are per run/session, not global machine-wide resource caps.
