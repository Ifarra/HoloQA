"""Plan loading, validation, and variable interpolation.

A plan is the only per-application artifact. It is written by a human, reviewed
in a pull request, and committed beside the application it tests. HoloQA never
edits one, and nothing in the tool is application-specific.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

#: Closed assertion set. Deliberately small — HoloQA adjudicates, it does not
#: script browsers. Anything inexpressible here is BLOCKED with a cause.
ASSERTION_KINDS = (
    "url_contains",
    "url_matches",
    "text_contains",
    "text_not_contains",
    "api",
    "json",
    "screenshot",
    "changed",
    "capture",
)

_VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ENV = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class PlanError(ValueError):
    """The plan file is malformed. Raised before anything executes."""


class KnownBehavior(BaseModel):
    id: str
    title: str
    applies_to: list[str] = Field(default_factory=list)
    verdict_hint: str = "not_a_failure"


class Stage(BaseModel):
    id: str
    title: str = ""


class Step(BaseModel):
    id: str
    stage: str = ""
    title: str
    depends_on: list[str] = Field(default_factory=list)
    route: str = ""
    do: str = ""
    expect: list[dict[str, Any]] = Field(default_factory=list)
    blocked_if: str = ""

    @field_validator("expect")
    @classmethod
    def _known_kinds(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for assertion in value:
            if not isinstance(assertion, dict) or len(assertion) != 1:
                raise ValueError(
                    f"each expect entry must be a single-key mapping, got {assertion!r}"
                )
            (kind,) = assertion
            if kind not in ASSERTION_KINDS:
                raise ValueError(
                    f"unknown assertion {kind!r}; choose from {', '.join(ASSERTION_KINDS)}"
                )
        return value

    @property
    def assertions(self) -> list[tuple[str, Any]]:
        return [next(iter(item.items())) for item in self.expect]


class Meta(BaseModel):
    app: str
    base_url: str = ""
    language: str = "en"
    workbook: str | None = None


class Plan(BaseModel):
    meta: Meta
    known_behaviors: list[KnownBehavior] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=list)
    steps: list[Step]

    source_path: str = ""
    source_sha256: str = ""

    def step(self, step_id: str) -> Step:
        for candidate in self.steps:
            if candidate.id == step_id:
                return candidate
        raise PlanError(f"step not found in plan: {step_id}")

    def has_step(self, step_id: str) -> bool:
        return any(candidate.id == step_id for candidate in self.steps)

    def has_kb(self, kb_id: str) -> bool:
        return any(entry.id == kb_id for entry in self.known_behaviors)

    @property
    def step_ids(self) -> list[str]:
        return [step.id for step in self.steps]


def _brace_hint(error: Exception) -> str:
    """Point at the one YAML trap this format walks into constantly.

    ``path: /api/scans/{scan_id}`` is a syntax error inside a ``{ }`` flow
    mapping, and interpolated paths are the common case. The raw parser message
    does not mention quoting, so say it here.
    """
    text = str(error)
    if "flow mapping" in text or "expected ',' or '}'" in text:
        return (
            "\n\nHint: inside a flow mapping { }, a value containing {braces} or "
            "[brackets] must be quoted. Write\n"
            '    path: "/api/scans/{scan_id}"\n'
            '    capture: { finding_id: "$.items[0].id" }\n'
            "rather than leaving them bare."
        )
    return ""


def _expand_env(value: str) -> str:
    """Replace ``${VAR}`` with the environment value, or leave it visible.

    An unset variable is left as-is rather than blanked, so a misconfigured
    base URL fails loudly at request time instead of silently targeting ``/``.
    """
    return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def load(path: str | Path) -> Plan:
    """Read and validate a plan file. Never executes anything."""
    plan_path = Path(path).expanduser().resolve()
    if not plan_path.is_file():
        raise PlanError(f"plan file not found: {plan_path}")

    raw_bytes = plan_path.read_bytes()
    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    except yaml.YAMLError as error:
        raise PlanError(f"plan is not valid YAML: {error}{_brace_hint(error)}") from error
    if not isinstance(data, dict):
        raise PlanError("plan must be a mapping at the top level")

    meta = data.get("meta") or {}
    if isinstance(meta, dict) and meta.get("base_url"):
        meta["base_url"] = _expand_env(str(meta["base_url"]))

    # A section whose entries are all commented out parses as null, not as an
    # empty list. That is the first thing a new user does to the template, so
    # treat it as empty rather than as a validation error.
    for optional in ("known_behaviors", "stages"):
        if data.get(optional) is None:
            data[optional] = []

    try:
        plan = Plan(
            **data,
            source_path=str(plan_path),
            source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
    except Exception as error:  # pydantic validation detail is the useful part
        raise PlanError(f"plan is invalid: {error}") from error

    _validate_graph(plan)
    return plan


def _validate_graph(plan: Plan) -> None:
    """Structural checks a schema cannot express."""
    seen: set[str] = set()
    for step in plan.steps:
        if step.id in seen:
            raise PlanError(f"duplicate step id: {step.id}")
        seen.add(step.id)

    stage_ids = {stage.id for stage in plan.stages}
    for step in plan.steps:
        if step.stage and stage_ids and step.stage not in stage_ids:
            raise PlanError(f"step {step.id} references unknown stage {step.stage!r}")
        for dependency in step.depends_on:
            if dependency not in seen:
                raise PlanError(
                    f"step {step.id} depends on {dependency!r}, which is not in the plan"
                )
            if dependency == step.id:
                raise PlanError(f"step {step.id} depends on itself")

    for entry in plan.known_behaviors:
        for step_id in entry.applies_to:
            if step_id not in seen:
                raise PlanError(
                    f"{entry.id} applies_to unknown step {step_id!r}"
                )

    _reject_cycles(plan)
    _validate_bindings(plan)


def _reject_cycles(plan: Plan) -> None:
    order = {step.id: index for index, step in enumerate(plan.steps)}
    for step in plan.steps:
        for dependency in step.depends_on:
            if order[dependency] >= order[step.id]:
                raise PlanError(
                    f"step {step.id} depends on {dependency}, which does not come earlier "
                    "in the plan; reorder the steps"
                )


def _validate_bindings(plan: Plan) -> None:
    """A ``{name}`` placeholder must be captured by an earlier step.

    This turns the prior tool's prose rule — *"kerjakan langkah berurutan; banyak
    langkah bergantung pada state langkah sebelumnya"* — into something the
    loader enforces before a run starts.
    """
    bound: set[str] = set()
    for step in plan.steps:
        for kind, value in step.assertions:
            for placeholder in _referenced_vars(value):
                if placeholder not in bound:
                    raise PlanError(
                        f"step {step.id} uses {{{placeholder}}} but no earlier step "
                        "captures it"
                    )
            if kind == "capture":
                if not isinstance(value, dict) or not value:
                    raise PlanError(
                        f"step {step.id}: capture must be a mapping of name -> $.json.path"
                    )
                bound.update(value)


def _referenced_vars(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_VAR.findall(value))
    if isinstance(value, dict):
        found: set[str] = set()
        for key, item in value.items():
            if key == "capture":
                continue
            found |= _referenced_vars(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _referenced_vars(item)
        return found
    return set()


def interpolate(value: Any, variables: dict[str, Any]) -> Any:
    """Substitute ``{name}`` placeholders from captured run variables."""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variables:
                raise PlanError(f"variable {{{name}}} has not been captured yet")
            return str(variables[name])

        return _VAR.sub(replace, value)
    if isinstance(value, dict):
        return {key: interpolate(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate(item, variables) for item in value]
    return value


def lint(path: str | Path) -> dict[str, Any]:
    """Validate a plan and summarize it. Used by ``holoqa_plan_validate``."""
    plan = load(path)
    warnings: list[str] = []
    for step in plan.steps:
        kinds = [kind for kind, _ in step.assertions]
        if not kinds:
            warnings.append(
                f"step {step.id} has no expect: entries, so it can only ever be BLOCKED"
            )
        elif kinds == ["screenshot"]:
            warnings.append(
                f"step {step.id} only requires a screenshot; consider adding a "
                "checkable assertion so a pass means more than a file existing"
            )
    return {
        "status": "ok",
        "plan": plan.meta.app,
        "source": plan.source_path,
        "sha256": plan.source_sha256,
        "steps": len(plan.steps),
        "stages": [stage.id for stage in plan.stages],
        "known_behaviors": [entry.id for entry in plan.known_behaviors],
        "warnings": warnings,
    }


TEMPLATE = '''# HoloQA plan for {app}
#
# You write this file; HoloQA never edits it. It is the contract that decides
# every verdict, which is why a human owns it.
#
# Quoting: inside a flow mapping {{ }}, values containing {{braces}} or
# [brackets] must be quoted — path: "/api/orders/{{order_id}}"

meta:
  app: {app}
  base_url: {url}
  language: en
  # workbook: ./Checklist.xlsx      # optional: annotate an existing checklist

known_behaviors:
  # Quirks that look like bugs but are known. Cite them with holoqa_note so a
  # re-run does not report them as new failures.
  # - id: KB-001
  #   title: search is eventually consistent for a few seconds
  #   applies_to: [A2]

stages:
  - id: A
    title: Smoke

steps:
  - id: A1
    stage: A
    title: The home page loads
    route: /
    do: Open the application.
    expect:
      - url_contains: {url}
      - screenshot: required

  # - id: A2
  #   stage: A
  #   title: Placing an order returns 201 PENDING
  #   depends_on: [A1]
  #   do: Fill the cart and submit.
  #   expect:
  #     - api: {{ method: POST, path: /api/orders, status: 201 }}
  #     - json: {{ status: PENDING }}
  #     - capture: {{ order_id: $.id }}
  #     - screenshot: required
  #
  # - id: A3
  #   stage: A
  #   title: The order reads back
  #   depends_on: [A2]
  #   expect:
  #     - api: {{ method: GET, path: "/api/orders/{{order_id}}", status: 200 }}
  #     - text_contains: Thank you
'''


FORMAT_REFERENCE = {
    "how_it_works": (
        "You drive the browser with agent-browser. HoloQA captures evidence via "
        "holoqa_observe and derives the verdict via holoqa_judge. You cannot "
        "record a pass; BLOCKED is the only verdict you may assert."
    ),
    "assertions": {
        "url_contains": "substring of the captured URL (needs a dom capture)",
        "url_matches": "regex against the captured URL (needs a dom capture)",
        "text_contains": "substring of visible page text (needs a dom capture)",
        "text_not_contains": "text that must be absent (needs a dom capture)",
        "api": "{method, path, status} — status may be an int or a list",
        "json": "subset or JSONPath match against the last api capture body",
        "screenshot": "'required' — a non-empty image exists for this step",
        "changed": "'dom' or 'api' — two captures, taken apart, must differ",
        "capture": "{name: $.json.path} — binds a value for later steps",
    },
    "quoting": (
        "Inside a flow mapping { }, quote values containing {braces} or "
        '[brackets]: path: "/api/orders/{order_id}", capture: { id: "$.items[0].id" }'
    ),
    "rules": [
        "A step may only reference {variables} captured by an earlier step.",
        "depends_on must point at steps that appear earlier in the file.",
        "Anything not expressible as an assertion is BLOCKED with a cause, never a soft pass.",
    ],
    "template": TEMPLATE.format(app="myapp", url="https://staging.example"),
}


def scaffold(app: str, url: str) -> str:
    return TEMPLATE.format(app=app, url=url)
