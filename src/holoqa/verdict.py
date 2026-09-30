"""The adjudicator — turns captured evidence into a verdict.

This is the module that makes the rest of HoloQA worth building. Every verdict
comes from comparing a plan a human wrote against files HoloQA captured itself.
No caller supplies a status; there is no parameter through which one could.

Three outcomes, and the distinction between the last two is load-bearing:

``PASS``     every assertion satisfied
``FAIL``     ran, and an assertion was violated — the system is wrong
``BLOCKED``  could not run or could not be verified — the test did not arrive

Guardrail 9 lives here: an assertion whose observation was never captured
yields BLOCKED, never FAIL. Absence of evidence is not evidence of a defect.
The prior tool put the same rule in prose — *"Ragu antara Lulus dan Blokir?
Pilih Blokir"* — and it is the reason a filled checklist can hold a release.

``blocked_if`` lives here too. A step that declares one turns a *violated*
assertion into BLOCKED carrying the plan's own cause, because a step that
cannot run in this environment must not be reported as a defect in the
product. The downgrade reuses the BLOCKED-not-FAIL rule above, so a definite
violation on a step with no ``blocked_if`` still outranks an unknown.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from holoqa import BLOCKED, FAIL, PASS
from holoqa import plan as plan_module
from holoqa import run as run_module
from holoqa.run import IntegrityError, Run

_JSONPATH = re.compile(r"\$\.?|\[(\d+)\]|\.?([A-Za-z_][A-Za-z0-9_]*)")


class Result:
    """One assertion's outcome."""

    def __init__(
        self,
        kind: str,
        ok: bool | None,
        detail: str,
        expected: Any = None,
        blocked_if: str = "",
    ):
        self.kind = kind
        self.ok = ok  # True / False / None (could not evaluate)
        self.detail = detail
        self.expected = expected
        #: The cause this assertion's own violation may be downgraded to. Empty
        #: means "a violation here is a defect", which is the default.
        self.blocked_if = blocked_if

    def as_dict(self) -> dict[str, Any]:
        out = {
            "kind": self.kind,
            "ok": self.ok,
            "detail": self.detail,
            "expected": self.expected,
        }
        if self.blocked_if:
            out["blocked_if"] = self.blocked_if
        return out


def jsonpath(data: Any, expression: str) -> tuple[bool, Any]:
    """Resolve a minimal ``$.a.b[0].c`` expression. Returns (found, value)."""
    current = data
    for index, name in _JSONPATH.findall(expression):
        if index:
            if not isinstance(current, list) or int(index) >= len(current):
                return False, None
            current = current[int(index)]
        elif name:
            if not isinstance(current, dict) or name not in current:
                return False, None
            current = current[name]
    return True, current


def subset_matches(expected: Any, actual: Any) -> tuple[bool, str]:
    """Structural subset match: every key in ``expected`` must agree."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, f"expected an object, found {type(actual).__name__}"
        for key, want in expected.items():
            if key.startswith("$"):
                found, value = jsonpath(actual, key)
                if not found:
                    return False, f"{key} is absent"
                ok, detail = subset_matches(want, value)
                if not ok:
                    return False, f"{key}: {detail}"
                continue
            if key not in actual:
                return False, f"key {key!r} is absent"
            ok, detail = subset_matches(want, actual[key])
            if not ok:
                return False, f"{key}: {detail}"
        return True, "all keys matched"
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False, f"expected a list, found {type(actual).__name__}"
        if len(actual) < len(expected):
            return False, f"expected at least {len(expected)} items, found {len(actual)}"
        for position, want in enumerate(expected):
            ok, detail = subset_matches(want, actual[position])
            if not ok:
                return False, f"[{position}]: {detail}"
        return True, "all items matched"
    if expected == actual:
        return True, f"equals {expected!r}"
    return False, f"expected {expected!r}, found {actual!r}"


def _load(run: Run, observation: dict[str, Any]) -> Any:
    """Read a capture, refusing to read bytes that are not the ones recorded.

    The hash was always computed and never checked, so a file edited after the
    capture was judged on its new contents. This is where that check lives, and
    it applies to every assertion kind because they all read through here.
    """
    path = run.evidence_dir / observation["file"]
    if not path.is_file():
        return None
    recorded = observation.get("sha256")
    if recorded:
        actual = run_module.sha256_file(path)
        if actual != recorded:
            raise IntegrityError(
                f"{observation['file']} does not match the hash recorded when it "
                f"was captured (recorded {str(recorded)[:12]}, found {actual[:12]}); "
                "the evidence was modified after capture, so it proves nothing"
            )
    if path.suffix.lower() == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return path


def _latest(observations: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    matching = [item for item in observations if item["kind"] == kind]
    return matching[-1] if matching else None


def _run_observations(run: Run) -> list[dict[str, Any]]:
    """Every capture in the run that this process made, in step order.

    A ``requires:`` precondition normally points at a fixture the agent captured
    in an *earlier* step — a probe that the environment is up — so searching
    only the current step's captures would report every precondition as unmet.
    Order is preserved so "the latest capture of this kind" still means what it
    says, and each record carries its step id so a scoped lookup can name it.

    Filtered the same way the step's own captures are: a record that never
    passed through :meth:`Run.attach` is not evidence, so it cannot satisfy a
    precondition either.
    """
    out: list[dict[str, Any]] = []
    for step_id in run.read()["steps"]:
        for item in run.observations_seen_by_this_process(step_id):
            out.append({**item, "step": step_id})
    return out


def _unmet_requirements(
    plan: plan_module.Plan,
    run: Run,
    step: plan_module.Step,
    observations: list[dict[str, Any]],
    variables: dict[str, Any],
) -> list[Result]:
    """Check a step's ``requires:`` preconditions against captured evidence.

    A precondition is an assertion evaluated against *captures that already
    exist* — typically a fixture the agent captured earlier in the run, or a
    value bound by an earlier step. It never triggers a capture of its own: a
    check that could go fetch its own evidence would be a test, not a
    precondition, and the distinction is what keeps BLOCKED meaningful.

    When a requirement cannot be evaluated at all (no capture to read), that is
    itself an unmet requirement — the precondition is unverified, so the step
    cannot run. The reason names the precondition in the plan's own words, so a
    report can group 64 BLOCKED by root cause instead of quoting 64 sentences.
    """
    if not step.requirements:
        return []

    # Preconditions read the whole run, because a fixture is normally captured
    # once, in its own step, and consumed by many.
    everything = _run_observations(run) or observations

    unmet: list[Result] = []
    for kind, raw, scope in step.requirements:
        try:
            value = plan_module.interpolate(raw, variables)
        except plan_module.PlanError as error:
            unmet.append(Result("requires", None, f"requires {kind}: {error}", raw))
            continue
        # Judged by the same evaluator as an assertion, so each kind means one
        # thing across the plan. An unmet or unevaluable one becomes BLOCKED.
        result = _check(kind, value, run, everything, {}, scope, step.id)
        if result.ok is True:
            continue
        unmet.append(
            Result(
                "requires",
                None,
                f"requires {kind} not satisfied: {result.detail}",
                raw,
            )
        )
    return unmet


def _capture_names(item: dict[str, Any]) -> set[str]:
    """The names a capture answers to, normalised.

    A plan writes `scope: orders` or `scope: /api/orders`; the capture records a
    file `orders.json` and a target `http://host/api/orders`. All three spellings
    are accepted, and all three are *exact* — the path is derived with
    :func:`urlparse` rather than substring-matched, so `/api/orders` still cannot
    be satisfied by `/api/orders-archive`.
    """
    target = str(item.get("target", "")).strip().lower()
    names = {
        target.rstrip("/"),
        (urlparse(target).path or "").rstrip("/"),
        Path(str(item.get("file", ""))).stem.strip().lower(),
    }
    return {name.lstrip("/") for name in names if name}


def _scope(
    observations: list[dict[str, Any]],
    kind: str,
    on: str,
    step_id: str,
) -> tuple[dict[str, Any] | None, Result | None]:
    """Pick the capture an assertion applies to.

    Without ``on`` this is the most recent capture of that kind — the historical
    behaviour, kept so existing plans do not change meaning. With ``on`` it is
    the capture the name identifies, which is the fix for a step that makes two
    calls and needs the assertion pinned to one of them.

    Naming is an *exact* match, not a substring. Substring matching let a decoy
    whose name merely contained the wanted one satisfy the assertion — the same
    shape as the ``/api/orders`` versus ``/api/orders-archive`` path bug, and it
    has the same consequence: the wrong response decides the verdict. And when
    more than one capture answers to the name, the evaluator does not guess.
    Reading the newest of them is an arbitrary choice, and arbitrary choices are
    what this module exists to remove, so an ambiguous scope is BLOCKED.

    Returns ``(observation, error)``. An ``on`` naming a capture that does not
    exist is an error rather than a fallback, because silently judging the wrong
    response is the bug being closed.
    """
    candidates = [item for item in observations if item["kind"] == kind]
    if not on:
        return (candidates[-1] if candidates else None), None
    wanted = on.strip().lower().lstrip("/")

    matching = [item for item in candidates if wanted in _capture_names(item)]
    if not matching:
        available = ", ".join(
            str(item.get("file", "?")) for item in candidates
        ) or "none"
        return None, Result(
            kind, None,
            f"step {step_id}: scope {on!r} matches no {kind} capture "
            f"(captured: {available})",
        )
    if len(matching) > 1:
        files = ", ".join(str(item.get("file", "?")) for item in matching)
        return None, Result(
            kind, None,
            f"step {step_id}: scope {on!r} matches {len(matching)} {kind} "
            f"captures ({files}); the assertion does not say which one it means. "
            "Rename the capture so the scope is unambiguous.",
        )
    return matching[0], None


def evaluate(
    plan: plan_module.Plan,
    run: Run,
    step_id: str,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Judge one step. Returns ``(verdict, assertion results, captured vars)``.

    Only captures this process made are considered. When HoloQA owns the run,
    the index it reads is its own memory, so a record written straight into
    ``run.json`` is invisible here rather than authoritative — the step simply
    has no evidence and blocks.
    """
    step = plan.step(step_id)
    data = run.read()
    observations = run.observations_seen_by_this_process(step_id)
    variables = run.vars()

    # `requires:` preconditions are checked first and are fatal when unmet: the
    # step did not run, so its assertions cannot be judged. The point of putting
    # them in the plan is that HoloQA decides this, not the agent's prose — the
    # reviewer's run had 64 BLOCKED whose only justification was a sentence the
    # agent wrote, which is exactly the "agent must not grade" line.
    unmet = _unmet_requirements(plan, run, step, observations, variables)
    if unmet:
        return BLOCKED, [item.as_dict() for item in unmet], {}

    # Multi-actor soundness: evidence captured in the wrong identity's session
    # must not be judged. A step that tests ownership, captured while logged in
    # as somebody else, proves nothing — and the failure mode is invisible,
    # because the bytes look like perfectly good evidence. The reviewer hit
    # exactly this: an anonymous step returned the admin session's DOM with an
    # identical hash.
    expected_actor = plan.actor_for(step_id)
    foreign = [
        item for item in observations
        if item.get("actor") and item["actor"] != expected_actor
    ]
    if foreign:
        actors = ", ".join(sorted({item["actor"] for item in foreign}))
        return (
            BLOCKED,
            [
                Result(
                    "actor",
                    None,
                    f"evidence was captured as actor {actors}, but this step runs "
                    f"as {expected_actor!r}; the capture must be taken in the "
                    "step's own session",
                ).as_dict()
            ],
            {},
        )

    results: list[Result] = []
    captures: dict[str, Any] = {}

    if not step.assertions:
        return (
            BLOCKED,
            [Result("none", None, "the plan defines no assertions for this step").as_dict()],
            {},
        )

    for kind, raw, on, blocked_if in step.assertions_with_causes:
        try:
            value = plan_module.interpolate(raw, variables)
            # `scope` is interpolated for the same reason `path` is: a plan that
            # writes `scope: "/api/orders/{order_id}"` means the resolved URL,
            # and an un-interpolated scope would silently match no capture.
            scope = plan_module.interpolate(on, variables) if on else ""
        except plan_module.PlanError as error:
            results.append(Result(kind, None, str(error), raw, blocked_if))
            continue
        results.append(
            _check(kind, value, run, observations, captures, scope, step_id, blocked_if)
        )

    # A step may declare `blocked_if`, per assertion or once for the whole step.
    # The per-assertion cause wins, so a step whose checkout assertion is allowed
    # to be disabled in this environment does not also excuse an unrelated
    # failure — a session that expired mid-step is a defect, not the feature flag
    # the plan named. A *violated* assertion carrying a cause is downgraded to
    # "could not be verified", which the rule below turns into BLOCKED; the
    # violation is kept verbatim in the detail, after the plan's own cause.
    downgraded: list[Result] = []
    for result in results:
        cause = result.blocked_if
        if result.ok is False and cause:
            downgraded.append(
                Result(
                    result.kind,
                    None,
                    f"blocked_if: {cause}; the assertion was violated: {result.detail}",
                    result.expected,
                    cause,
                )
            )
        else:
            downgraded.append(result)
    results = downgraded

    if any(result.ok is False for result in results):
        verdict = FAIL
    elif any(result.ok is None for result in results):
        verdict = BLOCKED
    else:
        verdict = PASS

    return verdict, [result.as_dict() for result in results], captures


def _check(
    kind: str,
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    captures: dict[str, Any],
    on: str = "",
    step_id: str = "",
    blocked_if: str = "",
) -> Result:
    """Evaluate one assertion, tagging it with the cause its violation carries.

    ``blocked_if`` travels with the assertion rather than the step so that the
    downgrade stays scoped to the failure the plan actually anticipated.
    """
    result = _dispatch(kind, value, run, observations, captures, on, step_id)
    if blocked_if and not result.blocked_if:
        result.blocked_if = blocked_if
    return result


def _dispatch(
    kind: str,
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    captures: dict[str, Any],
    on: str = "",
    step_id: str = "",
) -> Result:
    if kind == "screenshot":
        shots = [item for item in observations if item["kind"] == "screenshot"]
        if not shots:
            return Result(kind, None, "no screenshot was captured for this step", value)
        return Result(kind, True, f"{len(shots)} screenshot(s) captured", value)

    if kind in {"url_contains", "url_matches", "text_contains", "text_not_contains"}:
        return _check_page(kind, value, run, observations, on, step_id)

    if kind == "api":
        return _check_api(value, run, observations, on, step_id)

    if kind == "json":
        return _check_json(value, run, observations, on, step_id)

    if kind == "header":
        return _check_header(value, run, observations, on, step_id)

    if kind == "file":
        return _check_file(value, run, observations, on, step_id)

    if kind == "changed":
        return _check_changed(value, observations, on, step_id)

    if kind == "capture":
        return _check_capture(value, run, observations, captures, on, step_id)

    return Result(kind, None, f"assertion kind {kind!r} has no evaluator", value)


def _check_page(
    kind: str,
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    on: str = "",
    step_id: str = "",
) -> Result:
    latest, error = _scope(observations, "dom", on, step_id)
    if error:
        return error
    if not latest:
        return Result(kind, None, "no page capture; capture a dom observation first", value)
    payload = _load(run, latest)
    if not isinstance(payload, dict):
        return Result(kind, None, "page capture could not be read", value)

    if kind == "url_contains":
        url = payload.get("url", "")
        ok = str(value).lower() in url.lower()
        return Result(kind, ok, f"url is {url!r}", value)
    if kind == "url_matches":
        url = payload.get("url", "")
        ok = re.search(str(value), url) is not None
        return Result(kind, ok, f"url is {url!r}", value)

    text = payload.get("text", "")
    present = str(value).lower() in text.lower()
    if kind == "text_contains":
        detail = "found in page text" if present else "not found in page text"
        if not present and payload.get("truncated"):
            return Result(
                kind, None,
                "not found, but the page text was truncated, so this is unverified",
                value,
            )
        return Result(kind, present, detail, value)

    # text_not_contains is the mirror image, and it had the same hole: a
    # truncated capture cannot prove a string is *absent* either, because the
    # string may sit in the part that was cut. Declaring PASS there is exactly
    # the "absence of evidence read as evidence of absence" failure the module
    # docstring warns about.
    if present:
        return Result(kind, False, "unexpectedly present", value)
    if payload.get("truncated"):
        return Result(
            kind, None,
            "absent from the captured text, but the page text was truncated, "
            "so its absence is unverified",
            value,
        )
    return Result(kind, True, "absent as required", value)


def _api_path_matches(pattern: str, url: str) -> bool:
    """Match a plan's ``path`` against a captured request URL.

    Matching is on *path segments*, not raw substrings. A pattern matches the
    captured path when it is the same path, or a prefix that ends on a segment
    boundary — so ``/api/orders`` matches ``/api/orders``, ``/api/orders?page=2``
    and ``/api/orders/88``, but never ``/api/orders-archive``.

    The bug this closes: a raw substring test let an unrelated endpoint satisfy
    an assertion. Because the evaluator reads captures newest-first, a healthy
    ``/api/orders-archive`` captured after a broken ``/api/orders`` would decide
    the verdict — a PASS for an endpoint that was returning 500.

    The query string is ignored, since a plan legitimately writes a path without
    it. For anything stricter, ``path_exact`` and ``path_regex`` exist.
    """
    if not pattern:
        return True
    target = urlparse(url).path or url
    # A pattern that is explicitly anchored is treated as a regex by the caller,
    # but keep this honest if one reaches here.
    if pattern.startswith("^") or pattern.endswith("$"):
        return re.search(pattern, target) is not None
    pattern = pattern.rstrip("/") or "/"
    target = target.rstrip("/") or "/"
    return target == pattern or target.startswith(pattern + "/")


def _check_api(
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    on: str = "",
    step_id: str = "",
) -> Result:
    if not isinstance(value, dict):
        return Result("api", None, "api assertion must be a mapping", value)
    want_method = str(value.get("method", "GET")).upper()
    want_path = str(value.get("path", ""))
    want_status = value.get("status")
    exact = bool(value.get("path_exact", False))
    regex = value.get("path_regex")

    def path_matches(url: str) -> bool:
        target = urlparse(url).path or url
        if regex:
            return re.search(str(regex), target) is not None
        if exact:
            return target.rstrip("/") == want_path.rstrip("/")
        return _api_path_matches(want_path, url)

    candidates = [item for item in observations if item["kind"] == "api"]
    if on:
        scoped, error = _scope(observations, "api", on, step_id)
        if error:
            return error
        candidates = [scoped] if scoped else []

    for observation in reversed(candidates):
        payload = _load(run, observation)
        if not isinstance(payload, dict):
            continue
        request = payload.get("request", {})
        if str(request.get("method", "")).upper() != want_method:
            continue
        url = str(request.get("url", ""))
        if (want_path or regex) and not path_matches(url):
            continue
        status = payload.get("status")
        if want_status is None:
            return Result("api", True, f"{want_method} {want_path} -> {status}", value)
        allowed = want_status if isinstance(want_status, list) else [want_status]
        ok = status in allowed
        return Result("api", ok, f"{want_method} {want_path} returned {status}", value)

    return Result(
        "api", None,
        f"no capture of {want_method} {want_path}; capture it before judging",
        value,
    )


def _check_json(
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    on: str = "",
    step_id: str = "",
) -> Result:
    latest, error = _scope(observations, "api", on, step_id)
    if error:
        return error
    if not latest:
        return Result("json", None, "no api capture to match against", value)
    payload = _load(run, latest)
    if not isinstance(payload, dict):
        return Result("json", None, "api capture could not be read", value)
    body = payload.get("body")
    if body is None:
        return Result("json", None, "the captured response body was not JSON", value)
    ok, detail = subset_matches(value, body)
    return Result("json", ok, detail, value)


def _check_header(
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    on: str = "",
    step_id: str = "",
) -> Result:
    """Match a response header on a captured api exchange.

    Headers were already captured — ``observe.api`` writes them — but there was
    no way to assert on one, so a plan could not check ``content-range`` for a
    ranged download or a cache directive. Accepts either a plain string (the
    header must contain it) or ``{name, contains|equals|matches}``.
    """
    latest, error = _scope(observations, "api", on, step_id)
    if error:
        return error
    if not latest:
        return Result("header", None, "no api capture to read headers from", value)
    payload = _load(run, latest)
    if not isinstance(payload, dict):
        return Result("header", None, "api capture could not be read", value)

    headers = {
        str(key).lower(): str(item)
        for key, item in (payload.get("headers") or {}).items()
    }

    if isinstance(value, str):
        # `header: content-range` — merely present and non-empty.
        name, contains, equals, matches = value.lower(), None, None, None
    elif isinstance(value, dict):
        name = str(value.get("name", "")).lower()
        contains = value.get("contains")
        equals = value.get("equals")
        matches = value.get("matches")
        if not name:
            return Result("header", None, "header assertion needs a name", value)
        if contains is None and equals is None and matches is None:
            # A name alone asserts presence, which is a legitimate weak check.
            pass
    else:
        return Result("header", None, "header assertion must be a name or a mapping", value)

    if name not in headers:
        return Result("header", False, f"response has no {name!r} header", value)
    actual = headers[name]

    if equals is not None:
        ok = actual == str(equals)
        return Result("header", ok, f"{name}: {actual!r}", value)
    if matches is not None:
        ok = re.search(str(matches), actual) is not None
        return Result("header", ok, f"{name}: {actual!r}", value)
    if contains is not None:
        ok = str(contains).lower() in actual.lower()
        return Result("header", ok, f"{name}: {actual!r}", value)
    return Result("header", True, f"{name}: {actual!r} present", value)


def _check_file(
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    on: str = "",
    step_id: str = "",
) -> Result:
    """Assert on the *contents* of a downloaded file.

    Without this a download step could only prove a 200 happened — the reviewer
    could not tell a real CSV export from an error page saved as one. Accepts
    ``{name_matches, size_gt, size_lt, contains, magic}``; every key given must
    hold.
    """
    if not isinstance(value, dict) or not value:
        return Result("file", None, "file assertion must be a mapping", value)

    latest, error = _scope(observations, "download", on, step_id)
    if error:
        return error
    if not latest:
        # A `download` capture is what observe writes for a saved blob. An
        # `api` capture can also carry a body worth checking.
        latest, error = _scope(observations, "api", on, step_id)
        if error:
            return error
    if not latest:
        return Result("file", None, "no download capture to inspect", value)

    path = run.evidence_dir / latest["file"]
    if not path.is_file():
        return Result("file", None, "the captured file is no longer on disk", value)
    payload = path.read_bytes()
    size = len(payload)

    if "size_gt" in value and not size > int(value["size_gt"]):
        return Result("file", False, f"file is {size} bytes, not > {value['size_gt']}", value)
    if "size_lt" in value and not size < int(value["size_lt"]):
        return Result("file", False, f"file is {size} bytes, not < {value['size_lt']}", value)
    if "name_matches" in value:
        ok = re.search(str(value["name_matches"]), latest["file"]) is not None
        if not ok:
            return Result("file", False, f"file name {latest['file']!r} does not match", value)
    if "magic" in value:
        # A leading magic number proves the bytes are the format they claim to
        # be, which a saved error page would fail.
        want = str(value["magic"]).encode("latin-1")
        if not payload.startswith(want):
            return Result("file", False, "file does not start with the expected magic bytes", value)
    if "contains" in value:
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:
            return Result("file", False, "file is not readable as text", value)
        ok = str(value["contains"]).lower() in text.lower()
        if not ok:
            return Result("file", False, f"file does not contain {value['contains']!r}", value)

    return Result("file", True, f"{latest['file']} ({size} bytes) satisfied", value)


def _scope_many(
    observations: list[dict[str, Any]],
    kind: str,
    on: str,
    step_id: str,
) -> tuple[list[dict[str, Any]], Result | None]:
    """Every capture of ``kind`` whose ``target`` is the one ``on`` names.

    ``changed`` needs *two* captures of the same subject, so a single-capture
    scope would be useless: a file stem identifies one file, while the target
    identifies the subject. ``scope: /api/orders`` therefore means "the two
    captures of ``/api/orders``", which is what a plan that tests a change
    actually means. Matching is exact for the same reason it is exact in
    :func:`_scope` — a substring let a lookalike decide the verdict.
    """
    candidates = [item for item in observations if item["kind"] == kind]
    if not on:
        return candidates, None
    wanted = on.strip().lower().lstrip("/")

    matching = [item for item in candidates if wanted in _capture_names(item)]
    if not matching:
        available = ", ".join(
            str(item.get("target") or item.get("file", "?")) for item in candidates
        ) or "none"
        return [], Result(
            kind, None,
            f"step {step_id}: scope {on!r} matches no {kind} capture "
            f"(captured: {available})",
        )
    return matching, None


def _check_changed(
    value: Any,
    observations: list[dict[str, Any]],
    on: str = "",
    step_id: str = "",
) -> Result:
    """Two captures of the same target, taken apart, must differ.

    The prior tool stated the rule and relied on discipline: *"Langkah yang
    menguji perubahan butuh dua bukti berjarak... Satu tangkapan tidak
    membuktikan apa pun."* Here it is checked.

    ``scope`` used to be accepted on this assertion and silently dropped, so a
    plan that pinned it got the unpinned behaviour — comparing captures of two
    different endpoints and calling that a change. It is honoured now.
    """
    kind = str(value) if isinstance(value, str) else str(value.get("kind", "dom"))
    matching, error = _scope_many(observations, kind, on, step_id)
    if error:
        return error
    if len(matching) < 2:
        where = f" for scope {on!r}" if on else ""
        return Result(
            "changed", None,
            f"needs two {kind} captures taken apart, found {len(matching)}{where}",
            value,
        )
    first, last = matching[0], matching[-1]
    if first["sha256"] == last["sha256"]:
        return Result(
            "changed", False,
            f"the first and last {kind} captures are byte-identical, so nothing changed",
            value,
        )
    return Result(
        "changed", True,
        f"{kind} changed between {first['captured_at']} and {last['captured_at']}",
        value,
    )


def _check_capture(
    value: Any,
    run: Run,
    observations: list[dict[str, Any]],
    captures: dict[str, Any],
    on: str = "",
    step_id: str = "",
) -> Result:
    if not isinstance(value, dict) or not value:
        return Result("capture", None, "capture must map name -> $.json.path", value)
    latest, error = _scope(observations, "api", on, step_id)
    if error:
        return error
    if not latest:
        return Result("capture", None, "no api capture to read values from", value)
    payload = _load(run, latest)
    if not isinstance(payload, dict) or payload.get("body") is None:
        return Result("capture", None, "the captured response body was not JSON", value)

    bound = []
    for name, expression in value.items():
        found, resolved = jsonpath(payload["body"], str(expression))
        if not found:
            return Result("capture", None, f"{expression} is absent from the response", value)
        captures[name] = resolved
        run.bind(name, resolved)
        bound.append(f"{name}={resolved!r}")
    return Result("capture", True, "bound " + ", ".join(bound), value)
