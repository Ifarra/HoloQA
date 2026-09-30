"""The MCP surface — ten tools, none of which accepts a verdict.

Read the tool list looking for a ``status`` parameter. There isn't one, except
on ``holoqa_block``, where it is fixed to BLOCKED and a cause is mandatory.
That absence is the product.

Transport is stdio only. HoloQA binds no port, stores no credentials, and makes
no network connection of its own.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from holoqa import BLOCKED, __version__
from holoqa import history as history_module
from holoqa import observe as observe_module
from holoqa import plan as plan_module
from holoqa import report as report_module
from holoqa import run as run_module
from holoqa import verdict as verdict_module
from holoqa.run import GuardrailError, IntegrityError, Run

server = MCPServer(
    name="holoqa",
    version=__version__,
    description=(
        "Evidence-backed release checklists. HoloQA captures the evidence and "
        "computes PASS/FAIL itself; you drive the browser and explain the result."
    ),
    instructions=(
        "Workflow: validate a plan, start a run, then for each step drive the UI "
        "with agent-browser yourself, call holoqa_observe to capture evidence, and "
        "call holoqa_judge. You cannot record a passing verdict — HoloQA derives it "
        "from what it captured. Use holoqa_block only for a genuine prerequisite or "
        "safety stop, never for a recoverable popup or a selector you have not "
        "retried. When torn between passing and blocking, block."
    ),
)


# ------------------------------------------------------------------ utilities


def _plan_for(run: Run) -> plan_module.Plan:
    """The plan this run is judged against: the pinned copy, not the author's file.

    Reading the author's file here is what let a FAIL become a PASS mid-run: the
    plan is committed beside the code, so the agent under test could edit the
    assertions and be judged against the edited version. The pinned copy is the
    contract the run started with, and :meth:`Run.plan_for_judging` refuses when
    the file on disk no longer matches it.
    """
    return run.plan_for_judging()


def _resolve_url(run: Run, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        target = path
    else:
        base = str(run.read().get("meta", {}).get("base_url", "")).rstrip("/")
        if not base:
            raise GuardrailError(
                "this run has no base_url; pass a full URL or set one when starting the run"
            )
        target = f"{base}/{path.lstrip('/')}"
    return plan_module.interpolate(target, run.vars())


# ---------------------------------------------------------------------- tools


def holoqa_plan_validate(plan_path: str = "") -> dict[str, Any]:
    """Validate a plan file, or call with no path to learn the plan format.

    With no argument this returns the assertion reference and a starter
    template, so you can write a plan for a new application without guessing
    at the schema.
    """
    if not plan_path.strip():
        return {"status": "reference", **plan_module.FORMAT_REFERENCE}
    return plan_module.lint(plan_path)


def holoqa_run_start(
    plan_path: str,
    tag: str = "",
    commit: str = "",
    tester: str = "",
    base_url: str = "",
    run_dir: str = "",
) -> dict[str, Any]:
    """Create a run directory from a plan. Ask the user for tag/commit/tester."""
    plan = plan_module.load(plan_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    directory = run_dir or f".holoqa/runs/{stamp}-{plan.meta.app}"
    run = Run.create(
        directory,
        plan,
        meta={
            "tag": tag,
            "commit": commit,
            "tester": tester,
            "base_url": base_url or plan.meta.base_url,
        },
    )
    # Apply the plan's browser options for this process before any capture.
    observe_module.set_browser_options(
        plan.meta.browser.ignore_https_errors, plan.meta.browser.args
    )
    os.environ["HOLOQA_RUN_DIR"] = str(run.dir)
    status = run.status(plan)
    return {
        "status": "started",
        "run_dir": str(run.dir),
        "run_id": status["run_id"],
        "steps": len(plan.step_ids),
        "first_step": status["next_step"],
        "note": (
            "Drive the browser with agent-browser yourself. Call holoqa_observe to "
            "capture evidence, then holoqa_judge. HoloQA decides PASS and FAIL."
        ),
    }


def holoqa_run_status(run_dir: str = "") -> dict[str, Any]:
    """Where the run stands: next step, counts, captured variables."""
    run = run_module.find(run_dir or None)
    plan = _plan_for(run)
    status = run.status(plan)
    next_step = status["next_step"]
    if next_step:
        step = plan.step(next_step)
        actor_name = plan.actor_for(next_step)
        actor = plan.actor(actor_name)
        status["next"] = {
            "id": step.id,
            "title": step.title,
            "route": step.route,
            "do": step.do,
            "expect": step.expect,
            "depends_on": step.depends_on,
            "actor": actor_name,
            # The identifier only. A password is never returned over MCP; the
            # agent authenticates with it out of band, and HoloQA's redaction
            # covers the capture if it ever reaches a request body.
            "actor_login": (actor.login or actor.as_) if actor else "",
            "unmet_dependencies": run.blocked_dependencies(plan, step.id),
        }
    status["actors"] = sorted(plan.meta.actors) or [plan.default_actor]
    return status


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse the `headers` argument of an api capture.

    Accepts a JSON object. Kept strict: a header typo silently dropped would
    mean a request that never tested what the plan asked for.
    """
    if not (raw or "").strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise GuardrailError(
            f"headers must be a JSON object of name -> value: {error}"
        ) from error
    if not isinstance(parsed, dict):
        raise GuardrailError("headers must be a JSON object of name -> value")
    return {str(key): str(item) for key, item in parsed.items()}


def _actor_session(plan: plan_module.Plan, step_id: str, requested: str) -> str:
    """Resolve which agent-browser session a capture must use.

    The plan decides, not the caller. A step that names an actor is captured in
    that actor's session and nowhere else; the ``actor`` argument exists so the
    agent can be explicit and get a clear error, not so it can choose a session
    the plan did not ask for. Letting the caller pick would put the identity a
    test ran under outside the reviewed contract — the same class of hole as
    letting it pick a verdict.
    """
    expected = plan.actor_for(step_id)
    if not requested or requested == expected:
        return expected
    raise GuardrailError(
        f"step {step_id} runs as actor {expected!r}; it cannot be captured as "
        f"{requested!r}. The plan decides the identity."
    )


def holoqa_observe(
    step_id: str,
    kind: str,
    selector: str = "",
    method: str = "GET",
    path: str = "",
    body: str = "",
    headers: str = "",
    seconds: int = observe_module.SSE_SECONDS,
    slug: str = "",
    actor: str = "",
    wait_for: str = "",
    settle_ms: int = 0,
    timeout_ms: int = 0,
    run_dir: str = "",
) -> dict[str, Any]:
    """Capture evidence for a step. HoloQA performs the capture, not you.

    kind: screenshot | dom | api | sse | download

    screenshot  optional selector, otherwise the full page
    dom         url, title, and visible text
    api         in-page fetch of `path` with the browser's own session
    sse         holds `path` open for `seconds` and summarizes the events
    download    arms a blob interceptor, clicks `selector`, saves the file

    The step's actor decides which browser session is used. Passing `actor` that
    does not match the plan is an error, not an override.

    `headers` is a JSON object of extra request headers for an api capture, so a
    ranged or conditional request can be tested.

    `wait_for` is JavaScript that must evaluate truthy before the capture, and
    `settle_ms` is a pause after that. Both exist because a plan that says
    "wait 3-5 seconds" in prose is a plan whose timing is not enforced, and a
    guard-driven redirect would otherwise be captured mid-flight. Prefer
    `wait_for` — a condition, not a duration — and raise `timeout_ms` if the
    default is too short.
    """
    run = run_module.find(run_dir or None)
    plan = _plan_for(run)
    if not plan.has_step(step_id):
        raise GuardrailError(f"step {step_id} is not in this plan")

    session = _actor_session(plan, step_id, actor)

    if wait_for:
        observe_module.wait_for(wait_for, session=session, timeout_ms=timeout_ms)
    if settle_ms:
        observe_module.settle(settle_ms)

    name = run_module.slugify(slug or path or selector or kind)
    stem = f"step-{run_module.slugify(step_id)}-{name}"
    # A second capture of the same kind in the same step must not overwrite the
    # first. It used to: two `holoqa_observe("A2", "api", path="/api/orders")`
    # calls wrote one file, so the index held two records pointing at the same
    # bytes and `changed` — the assertion that exists to compare two captures
    # taken apart — could only ever see the survivor. Numbering the later
    # captures keeps both on disk, which is what the run record already claims.
    already = run.observations(step_id, kind)
    if already:
        stem = f"{stem}-{len(already) + 1}"

    if kind == "screenshot":
        destination = run.evidence_dir / f"{stem}.png"
        summary = observe_module.screenshot(destination, selector, session=session)
        target = selector or "<page>"
    elif kind == "dom":
        destination = run.evidence_dir / f"{stem}.json"
        summary = observe_module.dom(destination, session=session)
        target = summary.get("url", "")
    elif kind == "api":
        if not path:
            raise GuardrailError("api capture needs a path")
        target = _resolve_url(run, path)
        destination = run.evidence_dir / f"{stem}.json"
        extra = _parse_headers(headers)
        summary = observe_module.api(
            destination, method, target, body or None, session=session, headers=extra
        )
    elif kind == "sse":
        if not path:
            raise GuardrailError("sse capture needs a path")
        target = _resolve_url(run, path)
        destination = run.evidence_dir / f"{stem}.json"
        summary = observe_module.sse(destination, target, seconds, session=session)
    elif kind == "download":
        if not selector:
            raise GuardrailError("download capture needs the selector that starts it")
        observe_module.arm_download(session=session)
        observe_module.run_cli(["click", selector], session=session)
        destination = run.evidence_dir / f"{stem}.bin"
        summary = observe_module.collect_download(destination, session=session)
        target = selector
    else:
        raise GuardrailError(
            f"unknown capture kind {kind!r}; use screenshot, dom, api, sse, or download"
        )

    record = run.attach(
        step_id, kind=kind, path=destination, target=target, summary=summary,
        actor=session,
    )
    return {
        "status": "captured",
        "step": step_id,
        "kind": kind,
        "actor": session,
        "file": record["file"],
        "sha256": record["sha256"][:16],
        "bytes": record["bytes"],
        "summary": summary,
    }


def holoqa_judge(step_id: str, run_dir: str = "") -> dict[str, Any]:
    """Compute a step's verdict from the evidence HoloQA captured.

    You cannot influence the outcome. PASS requires every assertion in the plan
    to hold; a missing capture yields BLOCKED rather than FAIL.
    """
    run = run_module.find(run_dir or None)
    plan = _plan_for(run)

    unmet = run.blocked_dependencies(plan, step_id)
    if unmet:
        raise GuardrailError(
            f"step {step_id} depends on {', '.join(unmet)}, which has not passed"
        )

    integrity = run.integrity()
    try:
        outcome, results, captures = verdict_module.evaluate(plan, run, step_id)
    except IntegrityError as error:
        # Evidence that no longer matches its own record is not evidence. The
        # step blocks with the reason rather than being judged on the bytes.
        outcome, results, captures = BLOCKED, [
            {"kind": "integrity", "ok": None, "detail": str(error), "expected": None}
        ], {}
    except plan_module.PlanError as error:
        outcome, results, captures = BLOCKED, [
            {"kind": "plan", "ok": None, "detail": str(error), "expected": None}
        ], {}

    failures = [item["detail"] for item in results if item["ok"] is False]
    unknowns = [item["detail"] for item in results if item["ok"] is None]
    note = "; ".join(failures or unknowns)

    run.set_verdict(step_id, outcome, note=note, assertions=results, by="holoqa")
    status = run.status(plan)
    return {
        "step": step_id,
        "verdict": outcome,
        "assertions": results,
        "captured": captures,
        "note": note,
        "integrity": integrity["status"],
        "next_step": status["next_step"],
        "counts": status["counts"],
    }


def holoqa_block(step_id: str, reason: str, kb_ref: str = "", run_dir: str = "") -> dict[str, Any]:
    """Record BLOCKED — the test could not run or could not be verified.

    The only verdict you may assert, and it needs a concrete cause: what was
    missing, not that something "failed". Use this for an absent prerequisite,
    a disabled feature flag, or an environment limit — never for a popup you
    have not tried to dismiss.
    """
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise GuardrailError(
            "BLOCKED needs a concrete cause naming what was missing or unverifiable"
        )
    run = run_module.find(run_dir or None)
    plan = _plan_for(run)
    if kb_ref and not plan.has_kb(kb_ref):
        raise GuardrailError(
            f"{kb_ref} is not a known behaviour in this plan; add it with holoqa_kb_add"
        )
    step = run.set_verdict(step_id, BLOCKED, note=reason, by="agent")
    if kb_ref:
        run.note(step_id, "", [kb_ref])
    return {"step": step_id, "verdict": BLOCKED, "note": step["note"], "kb_ref": kb_ref}


def holoqa_note(step_id: str, note: str, kb_refs: list[str] | None = None, run_dir: str = "") -> dict[str, Any]:
    """Attach an observation note, optionally citing known behaviours.

    Write what you actually saw, with the numbers or status codes in it.
    "celery_workers.count=3, DNS resolved, TCP 443 open" is useful;
    "worked as expected" is not.
    """
    run = run_module.find(run_dir or None)
    plan = _plan_for(run)
    for ref in kb_refs or []:
        if not plan.has_kb(ref):
            raise GuardrailError(f"{ref} is not a known behaviour in this plan")
    step = run.note(step_id, note, kb_refs)
    return {"step": step_id, "note": step["note"], "kb_refs": step["kb_refs"]}


def holoqa_kb_add(
    kb_id: str,
    title: str,
    applies_to: list[str] | None = None,
    run_dir: str = "",
) -> dict[str, Any]:
    """Propose a known behaviour discovered mid-run.

    HoloQA never edits a plan — plans are human-owned and reviewed. This writes
    a proposal into the run's out/ directory for you to merge deliberately.
    """
    run = run_module.find(run_dir or None)
    path = run.out_dir / "kb-proposals.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"  - id: {kb_id}\n"
        f"    title: {title!r}\n"
        f"    applies_to: [{', '.join(applies_to or [])}]\n"
        f"    verdict_hint: not_a_failure\n"
    )
    if not path.exists():
        path.write_text("known_behaviors:\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    return {
        "status": "proposed",
        "file": str(path),
        "note": "Merge this into the plan yourself; HoloQA does not edit plans.",
    }


def holoqa_run_package(
    strict: bool = True,
    workbook: str = "",
    run_dir: str = "",
) -> dict[str, Any]:
    """validate → report → optional annotated XLSX → ZIP, and record history."""
    run = run_module.find(run_dir or None)
    plan = _plan_for(run)
    source = workbook or plan.meta.workbook
    if source and not Path(source).is_absolute():
        source = str((Path(plan.source_path).parent / source).resolve())
    result = report_module.package(run, strict=strict, workbook_source=source or None)
    history_module.record(run)
    return result


def holoqa_run_compare(against: str = "", run_dir: str = "") -> dict[str, Any]:
    """Diff this run against a named run, or the most recent green one."""
    run = run_module.find(run_dir or None)
    return history_module.compare(run, against or None)


def holoqa_verify(run_dir: str = "") -> dict[str, Any]:
    """Report whether this run's evidence can be trusted, and why.

    Three answers, and the middle one matters: ``verified`` means the process
    answering also produced every capture it counts; ``tampered`` means the
    records contradict the bytes; ``unverified`` means nobody can tell, because
    this process did not make the run. A run that cannot be verified is not
    silently promoted to a release decision — see ``holoqa_run_package``.
    """
    run = run_module.find(run_dir or None)
    report = run.integrity()
    report["run_id"] = run.read().get("run_id", run.dir.name)
    report["run_dir"] = str(run.dir)
    report["evidence_files"] = len(
        [item for item in run.evidence_dir.glob("*") if item.is_file()]
    )
    return report


def _reporting_errors(function: Any) -> Any:
    """Turn a raised guardrail into a reply the agent can read.

    A tool that raises used to produce *no response at all* on the stdio wire:
    the client waits forever and the run dies with no explanation. Every refusal
    in this codebase is deliberate — a tampered capture, a plan edited mid-run, a
    missing pinned plan — so the refusal has to arrive as an answer. It is
    returned as a normal result carrying ``status: "refused"``, which keeps the
    caller in control of what to do next instead of hanging.
    """
    import functools
    import inspect

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except (GuardrailError, IntegrityError) as error:
            return {
                "status": "refused",
                "error": type(error).__name__,
                "detail": str(error),
            }
        except observe_module.CaptureError as error:
            # agent-browser missing, a selector that matched nothing, a wait that
            # never held. The capture did not happen, and the caller has to be
            # told so — a silent hang is how a run dies with no explanation.
            return {
                "status": "capture_failed",
                "error": "CaptureError",
                "detail": str(error),
            }
        except plan_module.PlanError as error:
            return {"status": "refused", "error": "PlanError", "detail": str(error)}
        except report_module.PackageError as error:
            return {"status": "refused", "error": "PackageError", "detail": str(error)}

    # Keep the original signature: the MCP layer derives each tool's JSON schema
    # from it, and a `*args, **kwargs` wrapper would erase every parameter.
    wrapper.__signature__ = inspect.signature(function)
    return wrapper


for function, name in (
    (holoqa_plan_validate, "holoqa_plan_validate"),
    (holoqa_run_start, "holoqa_run_start"),
    (holoqa_run_status, "holoqa_run_status"),
    (holoqa_observe, "holoqa_observe"),
    (holoqa_judge, "holoqa_judge"),
    (holoqa_block, "holoqa_block"),
    (holoqa_note, "holoqa_note"),
    (holoqa_kb_add, "holoqa_kb_add"),
    (holoqa_run_package, "holoqa_run_package"),
    (holoqa_run_compare, "holoqa_run_compare"),
    (holoqa_verify, "holoqa_verify"),
):
    server.add_tool(_reporting_errors(function), name=name, structured_output=True)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
