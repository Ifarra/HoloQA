# HoloQA Product Plan

## Product thesis

HoloQA is an open-source, self-hostable, MCP-first SIT/UAT toolkit. An AI coding client supplies intent and reasoning; HoloQA supplies deterministic project understanding, browser execution, evidence, run state, approvals, and reports.

The product promise is an auditable chain:

```text
requirement → action → observation → evidence → validation → verdict
```

## Product boundary

HoloQA owns project initialization, CodeGraph indexing and queries, test-case normalization, controlled browser execution, evidence storage, run state, reports, safety policies, and approvals. The external coding agent owns the conversation, interpretation of user intent, tool selection, and clarification. The user owns approval of execution and access to the target system.

The first release is single-project and self-hosted. It accepts a local repository and tabular test specification, creates a reviewable plan, executes in an isolated browser, and returns evidence-backed JSON/HTML/XLSX results. Remote MCP, multi-user security, repository providers, arbitrary workflows, and production orchestration are later milestones.

## System shape

The local MCP server is the first public interface. The control plane persists projects, graph snapshots, test specifications, plans, runs, evidence metadata, and reports. A local bridge provides workspace and browser access. CodeGraph is an adapter-backed dependency, not a fork. Browser actions are executed by deterministic workers and every asynchronous operation has an inspectable ID and state.

## Workflow

1. Inspect and initialize a repository.
2. Index the repository and record commit/snapshot provenance.
3. Import requirements or an XLSX test specification.
4. Generate a structured, reviewable run plan.
5. Require explicit approval.
6. Execute browser actions against the target application.
7. Capture screenshots, DOM, console, URL, network metadata, and timestamps.
8. Validate expectations and produce PASS, FAIL, BLOCKED, or INCONCLUSIVE verdicts.
9. Export reports and preserve artifacts for replay and audit.

## Current state

The Docker MVP and local MCP vertical slice are working: repository inspection and initialization, CodeGraph artifact discovery, XLSX import/export, plan approval, deterministic demo browser execution, persistent run state, evidence-backed reports, and dashboard views are implemented and covered by the current test suite. The demo app and Juice Shop exercise provide repeatable validation targets.

## Delivery direction

The canonical delivery sequence is maintained in [MILESTONES.md](MILESTONES.md). The next material capability is to replace the current CodeGraph artifact/status integration with queryable, provenance-aware graph data, then generalize browser actions and expand evidence and validation semantics.

## Historical planning material

Superseded planning snapshots remain recoverable under `temp/archive/planning/`. They are not part of the active documentation set and should not be updated.
