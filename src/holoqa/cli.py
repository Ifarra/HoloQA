"""Command line: run the MCP server, validate a plan, or self-test.

``holoqa selftest`` is the descendant of the prior tool's ``dry-run.mjs``. It
proves every guardrail still bites, using only temporary files — no staging, no
browser, no network. If it ever goes green while a guardrail is broken, the
checklist this tool produces has stopped meaning anything.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from holoqa import BLOCKED, FAIL, PASS, __version__
from holoqa import plan as plan_module
from holoqa import run as run_module
from holoqa import verdict as verdict_module
from holoqa import agent as agent_module
from holoqa import mcp as mcp_module
from holoqa import report as report_module
from holoqa.observe import REDACT_KEYS, redact
from holoqa.run import GuardrailError, Run

SELFTEST_PLAN = """
meta:
  app: selftest
  base_url: http://localhost:0
known_behaviors:
  - id: KB-001
    title: known quirk
    applies_to: [S1]
stages:
  - id: S
    title: Selftest
steps:
  - id: S1
    stage: S
    title: Step with a screenshot requirement
    expect:
      - screenshot: required
  - id: S2
    stage: S
    title: Step that depends on S1
    depends_on: [S1]
    expect:
      - api: { method: GET, path: /thing, status: 200 }
  - id: S3
    stage: S
    title: Step requiring change
    expect:
      - changed: dom
  - id: S4
    stage: S
    title: Step with a declared environment cause
    blocked_if: the service is disabled in this environment
    expect:
      - api: { method: GET, path: /disabled, status: 200 }
  - id: S5
    stage: S
    title: Path matching must not accept a lookalike endpoint
    expect:
      - api: { method: GET, path: /disabled, status: 200 }
"""


class SelftestFailure(AssertionError):
    pass


def _expect_refusal(label: str, action, fragment: str = "") -> str:
    try:
        action()
    except GuardrailError as error:
        if fragment and fragment.lower() not in str(error).lower():
            raise SelftestFailure(
                f"{label}: refused, but not for the expected reason: {error}"
            ) from None
        return f"ok   {label}"
    raise SelftestFailure(f"{label}: NOT refused — this guardrail is broken")


def selftest() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="holoqa-selftest-"))
    lines: list[str] = []
    try:
        plan_path = workspace / "plan.yaml"
        plan_path.write_text(SELFTEST_PLAN, encoding="utf-8")
        plan = plan_module.load(plan_path)
        run = Run.create(workspace / "run", plan, meta={"tester": "selftest"})

        # 3 — verdict vocabulary is closed
        lines.append(_expect_refusal(
            "guardrail 3  unknown verdict rejected",
            lambda: run.set_verdict("S1", "Lulus"), "not one of"))

        # 1 — no evidence, no pass
        lines.append(_expect_refusal(
            "guardrail 1  PASS without evidence rejected",
            lambda: run.set_verdict("S1", PASS), "no evidence"))

        # 2 — a non-pass must say why
        lines.append(_expect_refusal(
            "guardrail 2  FAIL without a cause rejected",
            lambda: run.set_verdict("S1", FAIL), "requires a note"))

        # 4 — evidence must exist on disk
        lines.append(_expect_refusal(
            "guardrail 4  missing evidence file rejected",
            lambda: run.attach("S1", kind="screenshot", path=workspace / "nope.png"),
            "does not exist"))

        empty = run.evidence_dir / "empty.png"
        empty.write_bytes(b"")
        lines.append(_expect_refusal(
            "guardrail 4  empty evidence file rejected",
            lambda: run.attach("S1", kind="screenshot", path=empty), "empty"))

        # The agent may never assert a pass.
        lines.append(_expect_refusal(
            "trust        agent cannot assert PASS",
            lambda: run.set_verdict("S1", PASS, by="agent"), "may only assert"))
        lines.append(_expect_refusal(
            "trust        agent cannot assert FAIL",
            lambda: run.set_verdict("S1", FAIL, note="x" * 20, by="agent"),
            "may only assert"))

        # A real capture, then a real pass.
        shot = run.evidence_dir / "step-s1-page.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pretend-image-bytes")
        run.attach("S1", kind="screenshot", path=shot, target="<page>")
        outcome, results, _ = verdict_module.evaluate(plan, run, "S1")
        if outcome != PASS:
            raise SelftestFailure(f"S1 should pass with a screenshot, got {outcome}: {results}")
        run.set_verdict("S1", outcome, assertions=results, by="holoqa")
        lines.append("ok   verdict      screenshot requirement satisfied -> PASS")

        # 9 — a missing capture is BLOCKED, never FAIL.
        outcome, results, _ = verdict_module.evaluate(plan, run, "S2")
        if outcome != BLOCKED:
            raise SelftestFailure(
                f"guardrail 9: uncaptured api assertion should BLOCK, got {outcome}")
        lines.append("ok   guardrail 9  uncaptured assertion -> BLOCKED, not FAIL")

        # blocked_if: a violated assertion on a step that declared an environment
        # cause must report BLOCKED, and must keep the violation visible.
        blocked_evidence = run.evidence_dir / "step-s4-disabled.json"
        blocked_evidence.write_text(
            json.dumps({
                "request": {"method": "GET", "url": "http://localhost:0/disabled", "body": None},
                "status": 503, "ok": False, "headers": {}, "elapsed_ms": 3,
                "body": None, "body_text": None,
            }),
            encoding="utf-8",
        )
        run.attach("S4", kind="api", path=blocked_evidence)
        outcome, results, _ = verdict_module.evaluate(plan, run, "S4")
        if outcome != BLOCKED:
            raise SelftestFailure(
                f"blocked_if: a declared environment cause should BLOCK, got {outcome}: {results}"
            )
        detail = next(item["detail"] for item in results if item["ok"] is None)
        if "blocked_if" not in detail or "503" not in detail:
            raise SelftestFailure(
                f"blocked_if: the cause and the real violation must both survive, got {detail!r}"
            )
        run.set_verdict("S4", outcome, note=detail, assertions=results)
        lines.append("ok   verdict      blocked_if downgrades a violation, keeps the detail")

        # An api assertion must not be satisfied by an unrelated endpoint. This
        # uses a step with no blocked_if, so the verdict it produces is the
        # path-matching result alone.
        lookalike = run.evidence_dir / "step-s5-lookalike.json"
        lookalike.write_text(
            json.dumps({
                "request": {"method": "GET", "url": "http://localhost:0/disabled-archive", "body": None},
                "status": 200, "ok": True, "headers": {}, "elapsed_ms": 3,
                "body": None, "body_text": None,
            }),
            encoding="utf-8",
        )
        run.attach("S5", kind="api", path=lookalike)
        outcome, _, _ = verdict_module.evaluate(plan, run, "S5")
        if outcome != BLOCKED:
            raise SelftestFailure(
                f"api path: /disabled matched /disabled-archive; segment matching is broken "
                f"(got {outcome})"
            )
        lines.append("ok   verdict      api path does not match a lookalike endpoint")

        # changed: identical captures must not satisfy it.
        for name in ("a", "b"):
            same = run.evidence_dir / f"step-s3-{name}.json"
            same.write_text(json.dumps({"url": "u", "text": "identical"}), encoding="utf-8")
            run.attach("S3", kind="dom", path=same)
        outcome, results, _ = verdict_module.evaluate(plan, run, "S3")
        if outcome != FAIL:
            raise SelftestFailure(
                f"changed: two identical captures must FAIL, got {outcome}: {results}")
        run.set_verdict("S3", outcome, note="captures identical", assertions=results)
        lines.append("ok   verdict      identical captures do not prove a change")

        # 5 — a revised verdict keeps the superseded one.
        run.set_verdict("S3", BLOCKED, note="environment limit, rechecking later")
        step = run.read()["steps"]["S3"]
        if not step["revisions"] or step["revisions"][0]["from"] != FAIL:
            raise SelftestFailure("guardrail 5: verdict revision was not recorded")
        lines.append("ok   guardrail 5  revised verdict keeps an audit trail")

        # 7 — dependencies gate judgement.
        run.set_verdict("S1", BLOCKED, note="forced open to test the dependency gate")
        if run.blocked_dependencies(plan, "S2") != ["S1"]:
            raise SelftestFailure("guardrail 7: unmet dependency was not detected")
        lines.append("ok   guardrail 7  a step with an unmet dependency cannot be judged")

        # 8 — KB citations must exist.
        if plan.has_kb("KB-999"):
            raise SelftestFailure("guardrail 8: unknown KB id was accepted")
        lines.append("ok   guardrail 8  unknown known-behaviour id is rejected")

        # 6 — credential-bearing headers never reach disk.
        cleaned = redact({"Cookie": "secret", "X-Api-Key": "k", "content-type": "application/json"})
        if cleaned["Cookie"] != "<redacted>" or cleaned["content-type"] != "application/json":
            raise SelftestFailure("guardrail 6: header redaction is wrong")
        if not REDACT_KEYS.search("authorization"):
            raise SelftestFailure("guardrail 6: redaction pattern lost a key")
        lines.append("ok   guardrail 6  credential headers redacted in evidence")

        # Plan-level static checks, before anything can run.
        lines.append(_plan_refusal(
            workspace, "plan check   forward reference to an uncaptured variable",
            SELFTEST_PLAN + """
  - id: S6
    title: uses an unbound variable
    expect:
      - api: { method: GET, path: "/a/{nope}", status: 200 }
""", "no earlier step captures it"))

        lines.append(_plan_refusal(
            workspace, "plan check   unknown assertion kind rejected",
            SELFTEST_PLAN + """
  - id: S7
    title: invented assertion
    expect:
      - eventually_works: yes
""", "unknown assertion"))

        lines.append(_plan_refusal(
            workspace, "plan check   dependency on a later step rejected",
            """
meta: { app: selftest }
steps:
  - id: A
    title: first
    depends_on: [B]
    expect:
      - screenshot: required
  - id: B
    title: second
    expect:
      - screenshot: required
""", "does not come earlier"))

        lines.append(_plan_refusal(
            workspace, "plan check   unquoted {braces} explained, not just rejected",
            """
meta: { app: selftest }
steps:
  - id: A
    title: unquoted brace
    expect:
      - api: { method: GET, path: /a/{x}, status: 200 }
""", "must be quoted"))

        # A status-less api assertion used to pass on any response at all, so a
        # 500 on a broken endpoint satisfied `{api: {method: GET, path: /x}}`.
        lines.append(_plan_refusal(
            workspace, "plan check   api assertion without a status rejected",
            """
meta: { app: selftest }
steps:
  - id: A
    title: asserts nothing
    expect:
      - api: { method: GET, path: /a }
""", "needs a `status`"))

        # `size_gtt` / `contain` were accepted and ignored, so the assertion
        # degraded silently instead of failing.
        lines.append(_plan_refusal(
            workspace, "plan check   unknown file/header key rejected",
            """
meta: { app: selftest }
steps:
  - id: A
    title: typo keys
    expect:
      - file: { size_gtt: 1000 }
""", "unknown key"))

        # A bad regex used to load, then raise an uncaught re.error mid-judge.
        lines.append(_plan_refusal(
            workspace, "plan check   invalid regex rejected at load",
            """
meta: { app: selftest }
steps:
  - id: A
    title: unclosed set
    expect:
      - url_matches: "([unclosed"
""", "invalid regular expression"))

        # Zero steps used to validate, package, and decide RELEASE.
        lines.append(_plan_refusal(
            workspace, "plan check   empty plan rejected",
            """
meta: { app: selftest }
steps: []
""", "at least one step"))

        # One declared cause used to excuse every violated assertion on the step,
        # so a session that expired was reported under a feature-flag cause.
        cause_plan_path = workspace / "cause.yaml"
        cause_plan_path.write_text("""
meta: { app: selftest }
steps:
  - id: C1
    title: one expected failure, one unrelated
    expect:
      - { api: { method: GET, path: /thing, status: 200 },
          blocked_if: the service is disabled in this environment }
      - text_contains: Checkout
""", encoding="utf-8")
        cause_plan = plan_module.load(cause_plan_path)
        cause_run = Run.create(workspace / "cause-run", cause_plan)
        cause_api = cause_run.evidence_dir / "c1.json"
        cause_api.write_text(json.dumps({
            "request": {"method": "GET", "url": "http://localhost:0/thing", "body": None},
            "status": 503, "ok": False, "headers": {}, "elapsed_ms": 3,
            "body": None, "body_text": None,
        }), encoding="utf-8")
        cause_run.attach("C1", kind="api", path=cause_api)
        cause_page = cause_run.evidence_dir / "c1-page.json"
        cause_page.write_text(json.dumps({
            "url": "http://localhost:0/login", "title": "Sign in",
            "text": "Session expired.", "text_length": 16, "truncated": False,
        }), encoding="utf-8")
        cause_run.attach("C1", kind="dom", path=cause_page)
        cause_outcome, cause_results, _ = verdict_module.evaluate(
            cause_plan, cause_run, "C1"
        )
        excused = next(r for r in cause_results if r["kind"] == "api")
        unexcused = next(r for r in cause_results if r["kind"] == "text_contains")
        if cause_outcome != FAIL or excused["ok"] is not None or unexcused["ok"] is not False:
            raise SelftestFailure(
                "blocked_if scoping: one assertion's declared cause excused "
                f"another ({cause_outcome}: {cause_results})"
            )
        lines.append(
            "ok   verdict      blocked_if on one assertion does not excuse another"
        )

        # The three ways an agent used to be able to write its own PASS. Each is
        # reproduced here so `holoqa selftest` fails if the mechanism is reverted.
        integrity_plan_path = workspace / "integrity.yaml"
        integrity_plan_path.write_text("""
meta: { app: selftest }
steps:
  - id: I1
    title: an api assertion that must be earned
    expect:
      - api: { method: GET, path: /thing, status: 200 }
""", encoding="utf-8")
        integrity_plan = plan_module.load(integrity_plan_path)
        integrity_run = Run.create(workspace / "integrity-run", integrity_plan)

        # F-4: the plan is pinned, so rewriting the author's file is refused.
        original_plan = integrity_plan_path.read_text(encoding="utf-8")
        integrity_plan_path.write_text(
            "meta: { app: selftest }\nsteps:\n  - id: I1\n    title: t\n"
            "    expect:\n      - screenshot: required\n",
            encoding="utf-8",
        )
        try:
            integrity_run.plan_for_judging()
        except run_module.IntegrityError as error:
            if "has changed since this run started" not in str(error):
                raise SelftestFailure(f"F-4: refused for the wrong reason: {error}")
        else:
            raise SelftestFailure("F-4: a rewritten plan was accepted for judging")
        integrity_plan_path.write_text(original_plan, encoding="utf-8")
        lines.append("ok   integrity    a plan rewritten mid-run is refused")

        # F-1: a record this process never made is not evidence.
        forged = integrity_run.evidence_dir / "forged.json"
        forged.write_text(
            json.dumps({"request": {"method": "GET", "url": "http://localhost:0/thing"},
                        "status": 200, "ok": True, "headers": {}, "elapsed_ms": 1,
                        "body": None, "body_text": None}), encoding="utf-8"
        )
        forged_index = integrity_run.read()
        forged_index["steps"]["I1"]["observations"].append({
            "kind": "api", "file": forged.name, "target": "http://localhost:0/thing",
            "actor": "default", "bytes": forged.stat().st_size, "sha256": "0" * 64,
            "captured_at": "2026-01-01T00:00:00+00:00", "summary": {},
        })
        integrity_run._write(forged_index)
        forged_outcome, forged_results, _ = verdict_module.evaluate(
            integrity_plan, integrity_run, "I1"
        )
        if forged_outcome != BLOCKED or any(item["ok"] is not None for item in forged_results):
            raise SelftestFailure(
                "F-1: a hand-written index record produced a verdict "
                f"({forged_outcome}: {forged_results})"
            )
        if integrity_run.integrity()["status"] != "tampered":
            raise SelftestFailure("F-1: a hand-written record was not reported as tampered")
        lines.append("ok   integrity    a hand-written evidence record is not evidence")

        # F-3: bytes edited after capture no longer match the recorded hash.
        honest = integrity_run.evidence_dir / "honest.json"
        honest.write_text(
            json.dumps({"request": {"method": "GET", "url": "http://localhost:0/thing"},
                        "status": 503, "ok": False, "headers": {}, "elapsed_ms": 1,
                        "body": None, "body_text": None}), encoding="utf-8"
        )
        integrity_run.attach("I1", kind="api", path=honest)
        honest.write_text(
            json.dumps({"request": {"method": "GET", "url": "http://localhost:0/thing"},
                        "status": 200, "ok": True, "headers": {}, "elapsed_ms": 1,
                        "body": None, "body_text": None}), encoding="utf-8"
        )
        if not integrity_run.verify_evidence():
            raise SelftestFailure("F-3: an edited capture was not detected")
        try:
            verdict_module.evaluate(integrity_plan, integrity_run, "I1")
        except run_module.IntegrityError:
            pass
        else:
            raise SelftestFailure("F-3: an edited capture was judged instead of refused")
        lines.append("ok   integrity    a capture edited after hashing is refused")

        # A refusal must reach the caller as a reply, not as a dead connection.
        wrapped = mcp_module._reporting_errors(
            lambda: (_ for _ in ()).throw(
                run_module.GuardrailError("deliberate refusal")
            )
        )
        refusal = wrapped()
        if refusal.get("status") != "refused" or "deliberate" not in refusal.get("detail", ""):
            raise SelftestFailure(f"a refusal did not come back as a reply: {refusal}")
        lines.append("ok   integrity    a refusal reaches the caller as a reply")

        # The shortest forgery of all: leave the evidence alone and edit the
        # answer. run.json is a projection, so the ledger must win.
        # Read the file, edit it, write it back — what an agent with a shell
        # does. Not `_write` (HoloQA's own writer, which keeps the ledger in
        # step) and not `read()` (which hands back the ledger's live dict, so
        # mutating it would edit the ledger rather than the file).
        run_file = integrity_run.dir / run_module.RUN_FILE
        verdict_index = json.loads(run_file.read_text(encoding="utf-8"))
        verdict_index["steps"]["I1"]["verdict"] = PASS
        verdict_index["steps"]["I1"]["verdict_note"] = ""
        run_file.write_text(
            json.dumps(verdict_index, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if integrity_run.read()["steps"]["I1"]["verdict"] == PASS:
            raise SelftestFailure(
                "a verdict written into run.json overrode the ledger"
            )
        lines.append("ok   integrity    a hand-edited verdict in run.json is ignored")

        print("\n".join(lines))
        print(f"\n{len(lines)} guardrails verified. No staging, no network, no browser.")
        return 0
    except SelftestFailure as failure:
        # The failure goes to stdout, after the checks that did pass. stderr is
        # unbuffered, so reporting there alone put the failure *above* the ok
        # lines and a `tail` of the output looked green while the run was red.
        print("\n".join(lines))
        print(f"\nFAILED: {failure}")
        sys.stdout.flush()
        print(f"selftest failed: {failure}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _plan_refusal(workspace: Path, label: str, source: str, fragment: str) -> str:
    path = workspace / "bad-plan.yaml"
    path.write_text(source, encoding="utf-8")
    try:
        plan_module.load(path)
    except plan_module.PlanError as error:
        if fragment.lower() not in str(error).lower():
            raise SelftestFailure(f"{label}: wrong reason: {error}") from None
        return f"ok   {label}"
    raise SelftestFailure(f"{label}: NOT refused")


def init(app: str, url: str, output: str) -> int:
    """Scaffold a starter plan next to the application under test."""
    target = Path(output).expanduser()
    if target.exists():
        print(f"refusing to overwrite {target}", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan_module.scaffold(app, url), encoding="utf-8")
    # Plain ASCII: the Windows console is cp1252 by default and turns an
    # em-dash into a replacement character.
    print(f"wrote {target}")
    print()
    print("Next:")
    print(f"  1. edit {target} - describe your real steps and assertions")
    print(f"  2. holoqa validate {target}")
    print(f'  3. in your AI client: "run the checklist in {target} using holoqa"')
    return 0


#: Where `holoqa upgrade` fetches from. A git ref, not an index: there is no
#: `holoqa` on PyPI, which is also why `uv tool upgrade holoqa` answers "Nothing
#: to upgrade" — there is no version for it to resolve.
UPGRADE_SOURCE = "git+https://github.com/Ifarra/HoloQA"
UPGRADE_REPO = "https://github.com/Ifarra/HoloQA"


def installed_commit() -> str:
    """The commit uv recorded when it installed this tool, or ``""``.

    PEP 610 says a direct-URL install writes ``direct_url.json`` beside the
    metadata, and uv does. That file is the only way to know which build is
    actually installed, because the package version is a static string.
    """
    try:
        from importlib.metadata import distribution

        raw = distribution("holoqa").read_text("direct_url.json")
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        return str(json.loads(raw).get("vcs_info", {}).get("commit_id", ""))
    except ValueError:
        return ""


def remote_commit(timeout: int = 30) -> tuple[str, str]:
    """The current commit on main, as ``(sha, how)``. Empty sha means unknown.

    Uses ``git ls-remote`` when git is present — no rate limit — and falls back
    to the GitHub API so a machine without git can still check.
    """
    git = shutil.which("git")
    if git:
        try:
            done = subprocess.run(
                [git, "ls-remote", UPGRADE_REPO, "refs/heads/main"],
                capture_output=True, text=True, timeout=timeout,
            )
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.split()[0], "git ls-remote"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        with urlopen(
            "https://api.github.com/repos/Ifarra/HoloQA/commits/main",
            timeout=timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload.get("sha", "")), "GitHub API"
    except (URLError, OSError, ValueError):
        return "", "unreachable"


def _run_upgrade(source: str, reinstall: bool) -> tuple[bool, str]:
    uv = shutil.which("uv")
    if not uv:
        return False, (
            "uv is not on PATH. HoloQA is installed as a uv tool, so updating it "
            "needs uv: https://docs.astral.sh/uv/getting-started/installation/"
        )
    command = [uv, "tool", "install"]
    if reinstall:
        command.append("--reinstall")
    command.append(source)
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    except subprocess.SubprocessError as error:
        return False, f"uv failed: {error}"
    output = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, output.strip()


def upgrade(*, check_only: bool = False, source: str = UPGRADE_SOURCE) -> int:
    """Update the installed HoloQA from the repository, or report whether one exists."""
    current = installed_commit()
    print(f"installed        {current[:12] or 'unknown (not installed as a uv tool)'}")
    latest, how = remote_commit()
    if not latest:
        print("latest           unreachable - check your network, then retry")
        return 2
    print(f"latest (main)    {latest[:12]}  (via {how})")

    if current and current == latest:
        print("")
        print("Already up to date.")
        return 0
    if not current:
        print("")
        print(
            "This install has no recorded commit - it was not installed from git, "
            "or was installed by something other than uv. Re-run the install "
            "command to adopt the tracked form."
        )
    if check_only:
        print("")
        print("An update is available. Run `holoqa upgrade` to install it.")
        return 1

    print("")
    print(f"Installing {source} ...")
    ok, output = _run_upgrade(source, reinstall=False)
    if not ok:
        # On Windows a running MCP server holds files inside the tool's venv, and
        # uv fails with "failed to remove directory ... (os error 145)" part-way
        # through, which leaves the venv incomplete: the next `holoqa` call dies
        # with a ModuleNotFoundError. `--reinstall` repairs it once the holder is
        # gone, so the failure path is a recover, not a retry-in-the-dark.
        print("The first attempt failed:")
        print("  " + (output.strip().splitlines()[-1] if output.strip() else "no output"))
        print("")
        print(
            "This usually means an MCP client is running HoloQA right now, and "
            "Windows will not let the update replace files it is using."
        )
        print("Close your MCP client (Claude Code, Cursor, ...), then run:")
        print(f"  uv tool install --reinstall {source}")
        print("")
        print(
            "`--reinstall` matters: a failed attempt can leave the tool's "
            "environment incomplete, and a plain retry may report success without "
            "repairing it."
        )
        return 2

    print(output)
    print("")
    print(
        "Updated. Restart your MCP client - it reads the server list at startup, "
        "so the new build is not picked up in the current session."
    )
    print("Confirm with `holoqa selftest`: the check count is the version marker.")
    return 0


def doctor() -> int:
    """Check that the pieces a run depends on are actually present."""
    ok = True

    print(f"holoqa           {__version__}")

    found = shutil.which("agent-browser")
    if found:
        try:
            version = subprocess.run(
                ([ "cmd", "/c", found, "--version"] if os.name == "nt" and
                 found.lower().endswith((".cmd", ".bat")) else [found, "--version"]),
                capture_output=True, text=True, timeout=60,
            ).stdout.strip()
        except Exception:
            version = "installed"
        print(f"agent-browser    {version}")
    else:
        ok = False
        print("agent-browser    MISSING")
        print("                 install it: npm install -g agent-browser")

    print()
    print("MCP client entry:")
    print('  { "mcpServers": { "holoqa": { "command": "holoqa", "args": [] } } }')

    if not ok:
        print(
            "\nagent-browser is required: HoloQA captures evidence through it "
            "and cannot judge a step without captures.",
            file=sys.stderr,
        )
    return 0 if ok else 1


def _print_agent(value: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
        return
    if "providers" in value:
        for item in value["providers"]:
            state = item["version"] if item["installed"] else "MISSING"
            print(f"{item['provider']:<10} {state}")
            # Say what unattended means here, before someone picks it.
            if item.get("unattended"):
                print(f"{'':<10} unattended: {item['unattended']}")
        return
    agent = value.get("agent", {})
    holoqa = value.get("holoqa", {})
    if agent:
        print(f"agent: {agent.get('provider', 'unknown')} {agent.get('state', 'unknown')} "
              f"(exit {agent.get('exit_code', 'running')})")
    print(f"holoqa: {holoqa.get('decision', 'UNKNOWN')}  counts={holoqa.get('counts', {})}")
    if holoqa.get("next_step"):
        print(f"next step: {holoqa['next_step']}")


def agent_command(args: argparse.Namespace) -> int:
    """Own the provider-neutral CLI contract for coding-agent execution."""
    if args.agent_command == "providers":
        _print_agent({"providers": [agent_module.provider_info(name) for name in agent_module.PROVIDERS]}, args.json)
        return 0
    if args.agent_command == "doctor":
        names = [args.provider] if args.provider else list(agent_module.PROVIDERS)
        details = [agent_module.provider_info(name) for name in names]
        browser = shutil.which("agent-browser")
        result = {"providers": details, "agent_browser": {"installed": bool(browser), "binary": browser or ""}}
        _print_agent(result, args.json)
        return 0 if browser and all(item["installed"] for item in details) else 1
    if args.agent_command == "status":
        try:
            _print_agent(agent_module.status(args.run_dir), args.json)
            return 0
        except (agent_module.AgentError, run_module.GuardrailError, plan_module.PlanError) as error:
            print(str(error), file=sys.stderr)
            return 2

    request = agent_module.AgentRunRequest(
        provider=args.provider,
        cwd=Path(args.cwd),
        mode=args.mode,
        model=args.model,
        retain_events=args.retain_events,
    )
    try:
        if args.agent_command == "run" and args.dry_run:
            plan = plan_module.load(args.plan)
            intended = Path(args.run_dir) if args.run_dir else request.cwd / ".holoqa" / "runs" / f"TIMESTAMP-{plan.meta.app}"
            print(json.dumps({
                "provider": request.provider, "mode": request.mode, "cwd": str(request.cwd.resolve()),
                "plan": str(Path(args.plan).resolve()), "run_dir": str(intended),
                "note": "dry run: no run, config, or coding-agent process was created",
            }, indent=2))
            return 0
        if args.agent_command == "run":
            result = agent_module.create_and_launch(
                args.plan, request, tag=args.tag, commit=args.commit, tester=args.tester,
                base_url=args.base_url, run_dir=args.run_dir,
            )
            if getattr(args, "until_done", False):
                run = run_module.find(result["holoqa"]["run_dir"])
                result = agent_module.run_until_done(
                    request, run, max_rounds=args.max_rounds, timeout_s=args.timeout,
                )
        else:
            run = run_module.find(args.run_dir)
            if getattr(args, "until_done", False):
                result = agent_module.run_until_done(
                    request, run, max_rounds=args.max_rounds, timeout_s=args.timeout,
                    resume=True,
                )
            else:
                result = agent_module.launch(request, run, resume=True, timeout_s=args.timeout)
        if args.package:
            result["package"] = report_module.package(run_module.find(result["holoqa"]["run_dir"]), strict=True)
        _print_agent(result, args.json)
        return 0 if result["agent"]["exit_code"] == 0 else result["agent"]["exit_code"] or 1
    except (agent_module.AgentError, run_module.GuardrailError, plan_module.PlanError, report_module.PackageError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _runs_table(rows: list[dict[str, Any]]) -> str:
    """A fixed-width table a terminal can read at a glance."""
    header = f"{'':2} {'run':22} {'decision':8} {'pass':>4} {'fail':>4} {'block':>5} {'left':>4}  {'integrity':11} app"
    lines = [header, "-" * len(header)]
    for row in rows:
        counts = row.get("counts") or {}
        marker = "*" if row.get("current") else " "
        lines.append(
            f"{marker:2} {str(row.get('run_id','')):22} "
            f"{str(row.get('decision','')):8} "
            f"{counts.get('PASS', 0):>4} {counts.get('FAIL', 0):>4} "
            f"{counts.get('BLOCKED', 0):>5} {row.get('pending', 0):>4}  "
            f"{str(row.get('integrity','')):11} {row.get('app', '')}"
            + ("  (legacy)" if row.get("legacy") else "")
        )
    lines.append("")
    lines.append("* = current run (what `holoqa status` resolves to)")
    return "\n".join(lines)


def runs_command(args: argparse.Namespace) -> int:
    """List runs. Read-only: never adopts a ledger, never decides anything."""
    from holoqa import workspace as workspace_module

    try:
        if args.plan:
            plan = plan_module.load(args.plan)
            spaces = [workspace_module.workspace_for(plan)]
        else:
            spaces = workspace_module.find_workspaces(start=Path.cwd())
        if args.app:
            spaces = [ws for ws in spaces if ws.app == args.app]
        if not spaces:
            print("no workspaces yet; start a run to create one", file=sys.stderr)
            return 0
    except (plan_module.PlanError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    for ws in spaces:
        current = ws.current_run_id()
        for row in ws.list_runs():
            rows.append({**row, "app": ws.app, "current": row["run_id"] == current})
        if args.all:
            rows.extend({**row, "app": ws.app, "current": False} for row in ws.legacy_runs())

    rows.sort(key=lambda item: str(item.get("run_id", "")), reverse=True)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_runs_table(rows))
    return 0


def workspaces_command(args: argparse.Namespace) -> int:
    from holoqa import workspace as workspace_module

    spaces = workspace_module.find_workspaces(start=Path.cwd())
    payload = [
        {
            "app": ws.app,
            "path": str(ws.dir),
            "plan_path": ws.read().get("plan_path", ""),
            "current_run": ws.current_run_id(),
            "runs": len(ws.run_ids()),
        }
        for ws in spaces
    ]
    if args.json:
        print(json.dumps(payload, indent=2))
    elif not payload:
        print("no workspaces yet; start a run to create one")
    else:
        for item in payload:
            print(f"{item['app']:20} {item['runs']:>3} run(s)  current={item['current_run'] or '—'}  {item['path']}")
    return 0


def retest_command(args: argparse.Namespace) -> int:
    """Start a new run from the same plan. The previous run is never touched."""
    from holoqa import workspace as workspace_module

    try:
        plan = plan_module.load(args.plan)
        ws = workspace_module.workspace_for(plan)
    except (plan_module.PlanError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2

    previous = ws.current_run_id()
    if args.dry_run:
        print(json.dumps({
            "status": "dry_run",
            "workspace": str(ws.dir),
            "retest_of": previous,
            "next_sequence": ws.next_sequence(),
            "existing_runs": ws.run_ids(),
            "note": "the previous run is kept; a retest appends a new one",
        }, indent=2))
        return 0

    try:
        run = ws.retest(plan, meta={
            "tag": args.tag, "commit": args.commit, "tester": args.tester,
            "base_url": args.base_url or plan.meta.base_url,
        })
    except GuardrailError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "started",
        "run_id": run.read()["run_id"],
        "run_dir": str(run.dir),
        "retest_of": previous,
        "kept": ws.run_ids()[:-1],
    }, indent=2))
    return 0


def workspace_command(args: argparse.Namespace) -> int:
    from holoqa import workspace as workspace_module

    if args.workspace_command != "migrate":
        print(f"unknown workspace command: {args.workspace_command}", file=sys.stderr)
        return 2
    try:
        plan = plan_module.load(args.plan)
        ws = workspace_module.workspace_for(plan)
        result = workspace_module.migrate(ws, dry_run=args.dry_run)
    except (plan_module.PlanError, OSError, GuardrailError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    if result["status"] == "dry_run":
        print("re-run without --dry-run to perform these moves", file=sys.stderr)
    return 0


def _tui_mode(mode: str, sandbox: bool) -> str:
    return mode or ("unattended" if sandbox else "supervised")


def tui_command(args: argparse.Namespace) -> int:
    """Launch the optional interactive dashboard or its safe layout demo."""
    try:
        from holoqa.tui import HoloQATui
        from holoqa.sandbox import TuiSandbox

        if args.demo:
            if args.sandbox:
                print("--demo and --sandbox are mutually exclusive", file=sys.stderr)
                return 2
            HoloQATui.demo(animated=args.demo == "animated").run()
            return 0
        if not args.sandbox and (not args.plan or not args.provider):
            print("tui requires --plan and --provider, or use --demo", file=sys.stderr)
            return 2
        sandbox = TuiSandbox.create() if args.sandbox else None
        previous_base_url = os.environ.get("SMOKE_BASE_URL")
        if sandbox:
            os.environ["SMOKE_BASE_URL"] = sandbox.base_url
        try:
            provider = args.provider or "codex"
            cwd = sandbox.root if sandbox else Path(args.cwd)
            plan_path = sandbox.plan if sandbox else Path(args.plan)
            mode = _tui_mode(args.mode, bool(sandbox))
            request = agent_module.AgentRunRequest(
                provider=provider, cwd=cwd, mode=mode,
                model=args.model, retain_events=args.retain_events,
            )
            print(f"TUI sandbox: {sandbox.root if sandbox else cwd}")
            if sandbox:
                print(f"Fixture URL: {sandbox.base_url}")
            HoloQATui(
                plan_path=plan_path, request=request,
                run_dir=args.run_dir if not sandbox else str(sandbox.root / ".holoqa" / "runs" / "tui"),
                tag=args.tag, commit=args.commit, tester=args.tester,
                base_url=args.base_url or (sandbox.base_url if sandbox else ""),
                resume=args.resume,
            ).run()
            return 0
        finally:
            if previous_base_url is None:
                os.environ.pop("SMOKE_BASE_URL", None)
            else:
                os.environ["SMOKE_BASE_URL"] = previous_base_url
            if sandbox:
                root = sandbox.root
                if args.cleanup_sandbox:
                    sandbox.cleanup()
                    print(f"Removed sandbox: {root}")
                else:
                    sandbox.close()
                    print(f"Sandbox retained at: {root}")
    except (agent_module.AgentError, plan_module.PlanError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        from holoqa.sandbox import SandboxError
        if isinstance(error, SandboxError):
            print(str(error), file=sys.stderr)
            return 2
        raise


def main(argv: list[str] | None = None) -> int:
    # Not __doc__: that is RST for developers, and its em-dashes mojibake on a
    # cp1252 console. Users get plain ASCII.
    parser = argparse.ArgumentParser(
        prog="holoqa",
        description=(
            "Evidence-backed release checklists. Run with no command to serve "
            "the MCP server over stdio."
        ),
        epilog=(
            "typical first run:\n"
            "  holoqa doctor                      check agent-browser and the MCP entry\n"
            "  holoqa init --app myapp --url URL  scaffold holoqa.plan.yaml\n"
            "  holoqa validate holoqa.plan.yaml   lint it\n"
            "then ask your AI client to run the checklist."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"holoqa {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("mcp", help="run the stdio MCP server (default)")
    sub.add_parser("selftest", help="verify every guardrail, offline")
    sub.add_parser("doctor", help="check agent-browser and show the MCP entry")
    starter = sub.add_parser("init", help="scaffold a starter plan file")
    starter.add_argument("--app", default="myapp", help="application name")
    starter.add_argument("--url", default="https://staging.example", help="base URL")
    starter.add_argument("-o", "--output", default="holoqa.plan.yaml")
    validate = sub.add_parser("validate", help="validate a plan file")
    validate.add_argument("plan")
    status = sub.add_parser("status", help="show the active run")
    status.add_argument("--run-dir", default="")
    runs = sub.add_parser("runs", help="list runs in a workspace, newest first")
    runs.add_argument("--app", default="", help="workspace name (default: all)")
    runs.add_argument("--plan", default="", help="resolve the workspace from this plan")
    runs.add_argument("--json", action="store_true", help="emit the same rows as JSON")
    runs.add_argument(
        "--all", action="store_true",
        help="include legacy runs from the old flat .holoqa/runs layout",
    )
    workspaces = sub.add_parser("workspaces", help="list every workspace")
    workspaces.add_argument("--json", action="store_true")
    retest = sub.add_parser(
        "retest", help="start a new run from the same plan, keeping the old one"
    )
    retest.add_argument("--plan", required=True)
    retest.add_argument("--tag", default="")
    retest.add_argument("--commit", default="")
    retest.add_argument("--tester", default="")
    retest.add_argument("--base-url", default="")
    retest.add_argument(
        "--dry-run", action="store_true", help="say what would be created, then stop"
    )
    workspace_parser = sub.add_parser(
        "workspace", help="manage workspaces (migrate an old layout)"
    )
    workspace_sub = workspace_parser.add_subparsers(dest="workspace_command", required=True)
    ws_migrate = workspace_sub.add_parser(
        "migrate", help="move legacy runs into the workspace layout"
    )
    ws_migrate.add_argument("--plan", required=True)
    ws_migrate.add_argument(
        "--dry-run", action="store_true", help="print the moves without performing them"
    )
    upgrade_parser = sub.add_parser(
        "upgrade", help="update HoloQA from the repository, or check for one"
    )
    upgrade_parser.add_argument(
        "--check", action="store_true",
        help="report whether an update exists without installing it",
    )
    verify = sub.add_parser(
        "verify", help="report whether a run's evidence can be trusted"
    )
    verify.add_argument("--run-dir", default="")
    attest = sub.add_parser(
        "attest",
        help="record that a human reviewed a run this process cannot verify",
    )
    attest.add_argument("--run-dir", default="")
    attest.add_argument("--by", required=True, help="who reviewed it")
    attest.add_argument(
        "--reason", required=True,
        help="what was checked; at least 10 characters, because it is the record",
    )
    tui = sub.add_parser("tui", help="open the interactive agent dashboard")
    tui.add_argument(
        "--demo", nargs="?", const="static", choices=("static", "animated"),
        help="show a safe layout demo; use '--demo animated' for a Holoshop playback",
    )
    tui.add_argument("--sandbox", action="store_true", help="use a disposable localhost Git workspace and fixture app")
    tui.add_argument("--cleanup-sandbox", action="store_true", help="remove the generated sandbox after exit")
    tui.add_argument("--plan", default="", help="plan file for a real agent run")
    tui.add_argument("--provider", choices=agent_module.PROVIDERS, default="")
    tui.add_argument("--cwd", default=".")
    tui.add_argument(
        "--mode", default="", choices=agent_module.MODES,
        help="execution mode (sandbox defaults to unattended; otherwise supervised)",
    )
    tui.add_argument("--model", default="")
    tui.add_argument("--run-dir", default="")
    tui.add_argument("--tag", default="")
    tui.add_argument("--commit", default="")
    tui.add_argument("--tester", default="")
    tui.add_argument("--base-url", default="")
    tui.add_argument("--retain-events", action="store_true")
    tui.add_argument(
        "--resume", action="store_true",
        help="continue an existing --run-dir instead of creating a new run",
    )
    agent = sub.add_parser("agent", help="run a coding agent through HoloQA")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_providers = agent_sub.add_parser("providers", help="list supported coding agents")
    agent_providers.add_argument("--json", action="store_true")
    agent_doctor = agent_sub.add_parser("doctor", help="check coding-agent prerequisites")
    agent_doctor.add_argument("--provider", choices=agent_module.PROVIDERS)
    agent_doctor.add_argument("--json", action="store_true")
    agent_status = agent_sub.add_parser("status", help="show wrapper and HoloQA run status")
    agent_status.add_argument("--run-dir", required=True)
    agent_status.add_argument("--json", action="store_true")
    for name, help_text in (("run", "start a new HoloQA run with a coding agent"), ("resume", "continue an existing HoloQA run with a coding agent")):
        command = agent_sub.add_parser(name, help=help_text)
        if name == "run":
            command.add_argument("plan")
            command.add_argument("--tag", default="")
            command.add_argument("--commit", default="")
            command.add_argument("--tester", default="")
            command.add_argument("--base-url", default="")
            command.add_argument("--dry-run", action="store_true")
        command.add_argument("--provider", required=True, choices=agent_module.PROVIDERS)
        command.add_argument("--cwd", default=".")
        command.add_argument("--mode", default="supervised", choices=agent_module.MODES)
        command.add_argument("--model", default="")
        command.add_argument("--run-dir", required=(name == "resume"), default="")
        command.add_argument("--package", action="store_true")
        command.add_argument(
            "--until-done", action="store_true",
            help="keep resuming the agent while steps remain (stops on no progress)",
        )
        command.add_argument(
            "--max-rounds", type=int, default=5,
            help="cap on automatic resume rounds (default 5)",
        )
        command.add_argument(
            "--timeout", type=int, default=0,
            help="wall-clock seconds per round; 0 means no limit",
        )
        command.add_argument("--retain-events", action="store_true", help="persist raw agent output in the run directory")
        command.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()
    if args.command == "doctor":
        return doctor()
    if args.command == "init":
        return init(args.app, args.url, args.output)
    # A malformed plan is ordinary user input, not a crash. Print the reason
    # and the hint, never a traceback.
    if args.command == "validate":
        try:
            result = plan_module.lint(args.plan)
        except plan_module.PlanError as error:
            print(f"invalid plan: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    if args.command == "status":
        try:
            run = run_module.find(args.run_dir or None)
            plan = run.pinned_plan()
        except (run_module.GuardrailError, plan_module.PlanError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(run.status(plan), indent=2))
        return 0
    if args.command == "runs":
        return runs_command(args)
    if args.command == "workspaces":
        return workspaces_command(args)
    if args.command == "retest":
        return retest_command(args)
    if args.command == "workspace":
        return workspace_command(args)
    if args.command == "upgrade":
        try:
            return upgrade(check_only=args.check)
        except KeyboardInterrupt:
            return 130
    if args.command == "verify":
        try:
            run = run_module.find(args.run_dir or None)
            report = run.integrity()
        except run_module.GuardrailError as error:
            print(str(error), file=sys.stderr)
            return 2
        report["run_id"] = run.read().get("run_id", run.dir.name)
        report["run_dir"] = str(run.dir)
        print(json.dumps(report, indent=2))
        # Exit code is the answer, so this is usable as a gate in a script.
        # 0 verified/attested, 1 tampered, 2 unverified.
        return {"verified": 0, "attested": 0, "tampered": 1}.get(report["status"], 2)
    if args.command == "attest":
        try:
            run = run_module.find(args.run_dir or None)
            record = run.attest(by=args.by, reason=args.reason)
        except run_module.GuardrailError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps({"status": "attested", **record}, indent=2))
        print(
            "This records that you reviewed the evidence yourself. HoloQA still "
            "cannot verify it; the archive now says a human did."
        )
        return 0
    if args.command == "tui":
        return tui_command(args)
    if args.command == "agent":
        return agent_command(args)

    from holoqa.mcp import main as serve

    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
