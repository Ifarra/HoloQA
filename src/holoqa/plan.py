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
    "header",
    "screenshot",
    "changed",
    "capture",
    "file",
)

_VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ENV = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


#: Allowed values for ``KnownBehavior.verdict_hint``. A closed set, because the
#: field is consumed by report classification: an unrecognised value used to be
#: accepted silently and then vanish from the report, which reads as "this is a
#: new regression" for a defect the plan already knew about.
VERDICT_HINTS = ("not_a_failure", "known_defect")

#: How much a passing step proves. A closed set for the same reason the verdict
#: vocabulary is: a report that groups by strength must not silently drop a
#: value it does not recognise.
STRENGTHS = ("normal", "weak")


class PlanError(ValueError):
    """The plan file is malformed. Raised before anything executes."""


class KnownBehavior(BaseModel):
    id: str
    title: str
    applies_to: list[str] = Field(default_factory=list)
    verdict_hint: str = "not_a_failure"

    @field_validator("verdict_hint")
    @classmethod
    def _known_hint(cls, value: str) -> str:
        if value not in VERDICT_HINTS:
            raise ValueError(
                f"unknown verdict_hint {value!r}; choose from {', '.join(VERDICT_HINTS)}"
            )
        return value


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
    requires: list[dict[str, Any]] = Field(default_factory=list)
    blocked_if: str = ""
    actor: str = ""
    #: How much a PASS here actually proves. `weak` is for a step that can only
    #: be verified by a human reading the evidence — the reviewer marked 82 such
    #: steps with a `# UJI LEMAH` comment, which nothing could count or filter.
    #: Making it a field means a report can say "12 of 31 passes are weak".
    strength: str = "normal"
    weak_reason: str = ""

    @field_validator("strength")
    @classmethod
    def _known_strength(cls, value: str) -> str:
        if value not in STRENGTHS:
            raise ValueError(
                f"unknown strength {value!r}; choose from {', '.join(STRENGTHS)}"
            )
        return value

    @field_validator("expect", "requires", mode="before")
    @classmethod
    def _known_kinds(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        for assertion in value:
            if not isinstance(assertion, dict) or not assertion:
                raise ValueError(f"each entry must be a mapping, got {assertion!r}")
            # YAML 1.1 parses a bare `on:` key as the boolean True, so a plan
            # written with `on: create` arrives here as `{True: 'create'}`.
            # Normalise it before pydantic coerces the keys, rather than making
            # the author discover that YAML's boolean trap applies to `on`.
            if True in assertion and "scope" not in assertion:
                assertion["scope"] = assertion.pop(True)
            keys = set(assertion)
            # An entry is `{kind: value}`, optionally with `scope: <capture>` to
            # pin it to one capture, and optionally with its own `blocked_if` to
            # say which environment cause excuses *this* violation. A cause on
            # the step is inherited by every assertion that does not override it.
            extra = keys - {"scope", "blocked_if"}
            if len(extra) != 1:
                raise ValueError(
                    f"each entry must be one assertion, optionally with "
                    f"`scope: <capture>` and `blocked_if: <cause>`; got {assertion!r}"
                )
            (kind,) = extra
            if kind not in ASSERTION_KINDS:
                raise ValueError(
                    f"unknown assertion {kind!r}; choose from {', '.join(ASSERTION_KINDS)}"
                )
        return value

    @staticmethod
    def _entries(field: list[dict[str, Any]]) -> list[tuple[str, Any, str]]:
        out: list[tuple[str, Any, str]] = []
        for item in field:
            pairs = [(k, v) for k, v in item.items() if k not in ("scope", "blocked_if")]
            kind, value = pairs[0]
            out.append((kind, value, str(item.get("scope", ""))))
        return out

    @staticmethod
    def _entries_with_causes(
        field: list[dict[str, Any]], default_cause: str
    ) -> list[tuple[str, Any, str, str]]:
        """``(kind, value, scope, blocked_if)``, inheriting the step's cause.

        The step-level ``blocked_if`` is the default and a per-assertion one
        overrides it, which is how a step with mixed expectations narrows the
        excuse to the assertion it applies to: declaring the cause on the step
        covers *every* violated assertion on it, including one that failed for
        an unrelated reason.
        """
        out: list[tuple[str, Any, str, str]] = []
        for item in field:
            pairs = [(k, v) for k, v in item.items() if k not in ("scope", "blocked_if")]
            kind, value = pairs[0]
            cause = str(item.get("blocked_if") or default_cause or "").strip()
            out.append((kind, value, str(item.get("scope", "")), cause))
        return out

    @property
    def assertions(self) -> list[tuple[str, Any, str]]:
        """``(kind, value, scope)`` for each assertion. ``scope`` is ``""`` unset."""
        return self._entries(self.expect)

    @property
    def assertions_with_causes(self) -> list[tuple[str, Any, str, str]]:
        """``(kind, value, scope, blocked_if)`` for each assertion."""
        return self._entries_with_causes(self.expect, self.blocked_if)

    @property
    def requirements(self) -> list[tuple[str, Any, str]]:
        """``(kind, value, scope)`` for each precondition."""
        return self._entries(self.requires)

    def assertion(self, kind: str) -> Any:
        """The value of the first assertion of ``kind``, or ``None``.

        A convenience for readers and tests: the internal tuple shape carries
        the ``scope`` too, but most callers only want the value.
        """
        for found, value, _scope in self.assertions:
            if found == kind:
                return value
        return None


class Actor(BaseModel):
    """A browser identity a step runs in.

    Declared in ``meta.actors`` and referenced by ``step.actor``. Each actor
    gets its own isolated agent-browser session, so a step that needs an admin
    and a step that needs a proctor no longer fight over one cookie jar.

    Credentials come from the environment (``${ADMIN_PASSWORD}``), which is the
    point: the secret never lands in the plan file, in a ``do:`` instruction the
    agent reads, or in the transcript. An actor with no ``as`` is anonymous — a
    clean session, useful for testing the logged-out path.
    """

    as_: str = Field(default="", alias="as")
    password: str = ""
    login: str = ""
    note: str = ""

    model_config = {"populate_by_name": True}

    @property
    def identifier(self) -> str:
        return self.as_

    @property
    def anonymous(self) -> bool:
        return not self.as_


class BrowserOptions(BaseModel):
    """Browser flags a plan needs but the tool should not hardcode.

    A staging environment with a self-signed certificate, or a camera that must
    be faked, used to require an environment variable or a wrapper script —
    which put a per-application concern outside the plan that documents the
    application. These are passed through to agent-browser.
    """

    ignore_https_errors: bool = False
    args: list[str] = Field(default_factory=list)


class Meta(BaseModel):
    app: str
    base_url: str = ""
    language: str = "en"
    workbook: str | None = None
    actors: dict[str, Actor] = Field(default_factory=dict)
    browser: BrowserOptions = Field(default_factory=BrowserOptions)


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
    def default_actor(self) -> str:
        """The actor a step runs in when it does not name one.

        The first declared actor, or ``"default"`` when the plan declares none —
        which keeps every existing single-actor plan working unchanged.
        """
        return next(iter(self.meta.actors), "default")

    def actor(self, name: str) -> Actor | None:
        return self.meta.actors.get(name)

    def actor_for(self, step_id: str) -> str:
        """The session name a step's captures must use."""
        return self.step(step_id).actor or self.default_actor

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
    # Actor credentials arrive as ${ENV} too. Expanding them here means a secret
    # never reaches disk in a plan file, and the redaction layer sees a resolved
    # value it can strip if one ever lands in a captured body.
    actors = meta.get("actors") if isinstance(meta, dict) else None
    if isinstance(actors, dict):
        for name, actor in actors.items():
            if isinstance(actor, dict) and isinstance(actor.get("password"), str):
                # Checked on the raw value, before expansion: afterwards a
                # resolved secret is indistinguishable from a literal one, and
                # the whole point is to catch the literal before it is committed.
                if actor["password"] and not _ENV.search(actor["password"]):
                    raise PlanError(
                        f"actor {name!r} has a literal password; write "
                        "password: ${ENV_VAR} so the secret is never committed"
                    )
                for field in ("password", "login", "as"):
                    if isinstance(actor.get(field), str):
                        actor[field] = _expand_env(actor[field])

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
    # A plan with no steps validates, packages, and releases: `decision()` counts
    # only FAIL and BLOCKED, so zero steps was the same as zero failures. An
    # empty checklist holding a release is the single cheapest way to ship a
    # green result that means nothing, so it is refused here.
    if not plan.steps:
        raise PlanError(
            "a plan must define at least one step; an empty checklist cannot "
            "decide anything"
        )

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
    _validate_assertions(plan)
    _validate_actors(plan)


def _validate_actors(plan: Plan) -> None:
    """A step's ``actor`` must name a declared actor.

    A typo here would silently run the step in the wrong session, which for a
    step that tests ownership is the difference between a real test and a
    meaningless one. It is refused at load time instead.
    """
    declared = set(plan.meta.actors)
    for step in plan.steps:
        if step.actor and step.actor not in declared:
            known = ", ".join(sorted(declared)) or "none declared"
            raise PlanError(
                f"step {step.id} uses actor {step.actor!r}, which is not in "
                f"meta.actors ({known})"
            )
    # A plan is committed beside the code. A literal password in it is a leaked
    # password, so a credential must arrive through ${ENV}. That check lives in
    # load(), on the raw value before expansion.
    for name, actor in plan.meta.actors.items():
        if actor.login and not actor.password:
            raise PlanError(
                f"actor {name!r} has a login but no password; add "
                "password: ${ENV_VAR}"
            )


#: Recognised keys inside a ``file`` assertion. Same reasoning as
#: :data:`API_ASSERTION_KEYS`: ``size_gtt`` or ``name_matchz`` used to be accepted
#: and then ignored, so a step that meant to prove a 1 MB CSV had arrived was
#: satisfied by an 8-byte file. Every key given must hold, so an unrecognised one
#: silently weakens the assertion instead of failing it.
FILE_ASSERTION_KEYS = ("name_matches", "size_gt", "size_lt", "contains", "magic")

#: Recognised keys inside a ``header`` assertion. ``contains`` has no
#: abbreviation, and ``contain`` is the typo a plan author writes: it was
#: accepted, ignored, and the assertion degraded to "the header exists".
HEADER_ASSERTION_KEYS = ("name", "contains", "equals", "matches")

#: Capture kinds ``changed`` can compare. A typo here (``changed: domm``) used to
#: fall through to a count of zero captures and report BLOCKED with a message
#: about needing two captures, which reads as a missing capture rather than a
#: misspelled kind.
CHANGED_KINDS = ("dom", "api")


#: Recognised keys inside an ``api`` assertion. An unknown key is a
#: plan typo that would otherwise be ignored silently — the assertion would run
#: with a default it never asked for.
API_ASSERTION_KEYS = ("method", "path", "status", "path_exact", "path_regex")


def _regex_source(kind: str, value: Any) -> str:
    """The regular expression an assertion will compile at judge time, or ``""``.

    Collecting these at load time is what turns a plan typo from an uncaught
    ``re.error`` in the middle of judging into a refusal before the run starts,
    which is exactly what guardrail 10 promises.
    """
    if kind == "url_matches" and isinstance(value, str):
        return value
    if kind == "api" and isinstance(value, dict) and value.get("path_regex"):
        return str(value["path_regex"])
    if kind == "header" and isinstance(value, dict) and value.get("matches"):
        return str(value["matches"])
    if kind == "file" and isinstance(value, dict) and value.get("name_matches"):
        return str(value["name_matches"])
    return ""


def _validate_assertions(plan: Plan) -> None:
    for step in plan.steps:
        for kind, value, _on in step.assertions:
            _validate_one_assertion(step.id, kind, value)
        for kind, value, _on in step.requirements:
            _validate_one_assertion(step.id, kind, value, where="requires")


def _validate_one_assertion(
    step_id: str, kind: str, value: Any, *, where: str = "expect"
) -> None:
    """Refuse a plan typo before it can quietly weaken an assertion."""
    if kind == "api" and isinstance(value, dict):
        unknown = [key for key in value if key not in API_ASSERTION_KEYS]
        if unknown:
            raise PlanError(
                f"step {step_id}: api assertion has unknown key(s) "
                f"{', '.join(map(repr, unknown))}; choose from "
                f"{', '.join(API_ASSERTION_KEYS)}"
            )
        if value.get("path_exact") and value.get("path_regex"):
            raise PlanError(
                f"step {step_id}: api assertion cannot set both path_exact "
                "and path_regex"
            )
        # A status-less api assertion returned PASS on any response at all, so a
        # 500 on a broken endpoint satisfied `{api: {method: GET, path: /x}}`.
        # Status is the assertion; require it rather than defaulting it away.
        if value.get("status") is None:
            raise PlanError(
                f"step {step_id}: api assertion needs a `status` (an int or a "
                "list of ints); without one any response satisfies it"
            )

    if kind == "file":
        if not isinstance(value, dict) or not value:
            raise PlanError(
                f"step {step_id}: file assertion must be a mapping of "
                f"{', '.join(FILE_ASSERTION_KEYS)}"
            )
        unknown = [key for key in value if key not in FILE_ASSERTION_KEYS]
        if unknown:
            raise PlanError(
                f"step {step_id}: file assertion has unknown key(s) "
                f"{', '.join(map(repr, unknown))}; choose from "
                f"{', '.join(FILE_ASSERTION_KEYS)}"
            )

    if kind == "header":
        if isinstance(value, dict):
            unknown = [key for key in value if key not in HEADER_ASSERTION_KEYS]
            if unknown:
                raise PlanError(
                    f"step {step_id}: header assertion has unknown key(s) "
                    f"{', '.join(map(repr, unknown))}; choose from "
                    f"{', '.join(HEADER_ASSERTION_KEYS)}"
                )
            if not value.get("name"):
                raise PlanError(f"step {step_id}: header assertion needs a name")
            given = [key for key in ("contains", "equals", "matches") if value.get(key)]
            if len(given) > 1:
                raise PlanError(
                    f"step {step_id}: header assertion sets "
                    f"{', '.join(given)}; pick one"
                )
        elif not isinstance(value, str):
            raise PlanError(
                f"step {step_id}: header assertion must be a name or a mapping"
            )

    if kind == "changed":
        target = value if isinstance(value, str) else (
            value.get("kind", "") if isinstance(value, dict) else ""
        )
        if target not in CHANGED_KINDS:
            raise PlanError(
                f"step {step_id}: changed must compare "
                f"{' or '.join(CHANGED_KINDS)}, got {target!r}"
            )

    source = _regex_source(kind, value)
    if source:
        try:
            re.compile(source)
        except re.error as error:
            raise PlanError(
                f"step {step_id}: {where} {kind} has an invalid regular "
                f"expression {source!r}: {error}"
            ) from error


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
        for kind, value, scope in step.assertions:
            # The scope is interpolated at judge time, so a `{name}` in it is a
            # forward reference like any other and must be checked here too.
            for placeholder in _referenced_vars(value) | _referenced_vars(scope):
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
        kinds = [kind for kind, _, _on in step.assertions]
        if not kinds:
            warnings.append(
                f"step {step.id} has no expect: entries, so it can only ever be BLOCKED"
            )
        elif kinds == ["screenshot"]:
            warnings.append(
                f"step {step.id} only requires a screenshot; consider adding a "
                "checkable assertion so a pass means more than a file existing"
            )
        if step.strength == "weak" and not step.weak_reason:
            warnings.append(
                f"step {step.id} is marked strength: weak but gives no weak_reason; "
                "the report prints it, so say why the evidence needs a human"
            )
    weak_total = sum(1 for step in plan.steps if step.strength == "weak")
    if weak_total:
        warnings.append(
            f"{weak_total} step(s) are marked strength: weak; the report states how "
            "many passes rest on evidence a human still has to read"
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
  # actors:                          # optional: more than one browser identity
  #   admin:
  #     as: admin@example.test
  #     login: ${{ADMIN_PASSWORD}}     # secrets come from the environment
  #   proctor:
  #     as: proctor@example.test
  #     login: ${{PROCTOR_PASSWORD}}
  #   anon: {{}}                      # no `as` means a clean, logged-out session

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
        "api": (
            "{method, path, status} — `status` is REQUIRED and may be an int or a "
            "list of ints; without it any response satisfies the assertion. `path` "
            "is matched on path segments; add `path_exact: true` or "
            "`path_regex: <regex>`. The query string is ignored."
        ),
        "json": "subset or JSONPath match against the last api capture body",
        "header": (
            "a response header on an api capture: either a name alone (must be "
            "present) or {name, contains|equals|matches}"
        ),
        "file": (
            "the contents of a download: {name_matches, size_gt, size_lt, "
            "contains, magic} — every key given must hold"
        ),
        "screenshot": "'required' — a non-empty image exists for this step",
        "changed": (
            "'dom' or 'api' — two captures of that kind, taken apart, must differ. "
            "Add `scope: <capture>` to compare the captures that belong to one "
            "named capture instead of every capture of the kind."
        ),
        "capture": "{name: $.json.path} — binds a value for later steps",
    },
    "scoping": (
        "By default an assertion reads the most recent capture of its kind. Add "
        "`scope: <name>` to pin it to a specific capture when a step makes more "
        "than one call: `- {json: {status: PENDING}, scope: create}`. The name "
        "must match the capture's file stem or its target exactly — `scope: "
        "orders` does not match `orders-archive` — and a scope that matches more "
        "than one capture is BLOCKED rather than guessed, so name captures so the "
        "scope is unambiguous."
    ),
    "requires": (
        "Preconditions, checked by HoloQA before the step's assertions. "
        "`requires: [{api: {method: GET, path: /api/fixtures, status: 200}}]` — "
        "if it is not satisfied the step is BLOCKED with the plan's own cause, "
        "and the report groups such steps by root cause. This is how a step "
        "reports 'the fixture is missing' without you having to write it in "
        "prose."
    ),
    "blocked_if": (
        "A cause for a violation the plan expects in this environment, so it "
        "reports BLOCKED instead of FAIL: `blocked_if: the orders service is "
        "disabled in this environment`. Set it on the step to cover its "
        "assertions, or on a single assertion to scope the excuse to that one: "
        "`- {api: {method: GET, path: /api/orders, status: 200}, blocked_if: the "
        "orders service is disabled}`. An assertion with no cause still reports "
        "FAIL, so an unrelated failure is never excused by another assertion's "
        "cause."
    ),
    "strength": (
        "`strength: weak` plus `weak_reason: <why>` marks a step whose PASS "
        "still needs a human to read the evidence. The report states how many "
        "passes are weak, so a green checklist does not overstate itself."
    ),
    "quoting": (
        "Inside a flow mapping { }, quote values containing {braces} or "
        '[brackets]: path: "/api/orders/{order_id}", capture: { id: "$.items[0].id" }'
    ),
    "rules": [
        "A step may only reference {variables} captured by an earlier step.",
        "depends_on must point at steps that appear earlier in the file.",
        "An `api` assertion needs a `status`; a status-less one passes on any response.",
        "A plan needs at least one step; an empty checklist cannot decide a release.",
        "Unknown keys in an api/file/header assertion are refused at load, and "
        "every regex is compiled at load, so a typo cannot weaken a check silently.",
        "Anything not expressible as an assertion is BLOCKED with a cause, never a soft pass.",
    ],
    "actors": (
        "A step that needs a second identity declares it. meta.actors maps a name "
        "to a browser identity; step.actor selects it; each actor gets its own "
        "isolated browser session, so an admin step and a proctor step no longer "
        "share one cookie jar. Credentials must come from the environment "
        "(password: ${ADMIN_PASSWORD}) — a literal password in a plan is refused, "
        "because the plan is committed beside the code. An actor with no `as` is "
        "anonymous. Evidence is tagged with the session that produced it, and a "
        "step whose evidence was captured in the wrong session is BLOCKED, never "
        "counted as a pass."
    ),
    "template": TEMPLATE.format(app="myapp", url="https://staging.example"),
}


def scaffold(app: str, url: str) -> str:
    return TEMPLATE.format(app=app, url=url)
