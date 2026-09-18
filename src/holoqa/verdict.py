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
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from holoqa import BLOCKED, FAIL, PASS
from holoqa import plan as plan_module
from holoqa.run import Run

_JSONPATH = re.compile(r"\$\.?|\[(\d+)\]|\.?([A-Za-z_][A-Za-z0-9_]*)")


class Result:
    """One assertion's outcome."""

    def __init__(self, kind: str, ok: bool | None, detail: str, expected: Any = None):
        self.kind = kind
        self.ok = ok  # True / False / None (could not evaluate)
        self.detail = detail
        self.expected = expected

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "detail": self.detail,
            "expected": self.expected,
        }


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
    path = run.evidence_dir / observation["file"]
    if not path.is_file():
        return None
    if path.suffix.lower() == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return path


def _latest(observations: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    matching = [item for item in observations if item["kind"] == kind]
    return matching[-1] if matching else None


def evaluate(
    plan: plan_module.Plan,
    run: Run,
    step_id: str,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Judge one step. Returns ``(verdict, assertion results, captured vars)``."""
    step = plan.step(step_id)
    data = run.read()
    observations = run.step(data, step_id)["observations"]
    variables = run.vars()

    results: list[Result] = []
    captures: dict[str, Any] = {}

    if not step.assertions:
        return (
            BLOCKED,
            [Result("none", None, "the plan defines no assertions for this step").as_dict()],
            {},
        )

    for kind, raw in step.assertions:
        try:
            value = plan_module.interpolate(raw, variables)
        except plan_module.PlanError as error:
            results.append(Result(kind, None, str(error), raw))
            continue
        results.append(_check(kind, value, run, observations, captures))

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
) -> Result:
    if kind == "screenshot":
        shots = [item for item in observations if item["kind"] == "screenshot"]
        if not shots:
            return Result(kind, None, "no screenshot was captured for this step", value)
        return Result(kind, True, f"{len(shots)} screenshot(s) captured", value)

    if kind in {"url_contains", "url_matches", "text_contains", "text_not_contains"}:
        return _check_page(kind, value, run, observations)

    if kind == "api":
        return _check_api(value, run, observations)

    if kind == "json":
        return _check_json(value, run, observations)

    if kind == "changed":
        return _check_changed(value, observations)

    if kind == "capture":
        return _check_capture(value, run, observations, captures)

    return Result(kind, None, f"assertion kind {kind!r} has no evaluator", value)


def _check_page(kind: str, value: Any, run: Run, observations: list[dict[str, Any]]) -> Result:
    latest = _latest(observations, "dom")
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

    return Result(kind, not present, "absent as required" if not present else "unexpectedly present", value)


def _check_api(value: Any, run: Run, observations: list[dict[str, Any]]) -> Result:
    if not isinstance(value, dict):
        return Result("api", None, "api assertion must be a mapping", value)
    want_method = str(value.get("method", "GET")).upper()
    want_path = str(value.get("path", ""))
    want_status = value.get("status")

    for observation in reversed([item for item in observations if item["kind"] == "api"]):
        payload = _load(run, observation)
        if not isinstance(payload, dict):
            continue
        request = payload.get("request", {})
        if str(request.get("method", "")).upper() != want_method:
            continue
        if want_path and want_path not in str(request.get("url", "")):
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


def _check_json(value: Any, run: Run, observations: list[dict[str, Any]]) -> Result:
    latest = _latest(observations, "api")
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


def _check_changed(value: Any, observations: list[dict[str, Any]]) -> Result:
    """Two captures of the same target, taken apart, must differ.

    The prior tool stated the rule and relied on discipline: *"Langkah yang
    menguji perubahan butuh dua bukti berjarak... Satu tangkapan tidak
    membuktikan apa pun."* Here it is checked.
    """
    kind = str(value) if isinstance(value, str) else str(value.get("kind", "dom"))
    matching = [item for item in observations if item["kind"] == kind]
    if len(matching) < 2:
        return Result(
            "changed", None,
            f"needs two {kind} captures taken apart, found {len(matching)}",
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
) -> Result:
    if not isinstance(value, dict) or not value:
        return Result("capture", None, "capture must map name -> $.json.path", value)
    latest = _latest(observations, "api")
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
