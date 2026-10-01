"""Run directory, run.json, evidence index, and the guardrails.

Plain files, no database. A run is inspectable with ``cat``, diffable in git,
and already the shape of the ZIP that ships.

The guardrails here are ported from the prior tool's ``record.mjs``, which put
it plainly: *sengaja keras, jangan dilonggarkan* — deliberately strict, do not
loosen them. They are the reason a filled checklist means anything.

## Why there is an in-process ledger

Every guardrail above this line assumed that only HoloQA writes into the run
directory. Nothing enforced that, and the agent under test has a shell: it could
write a file into ``evidence/``, append a record to ``run.json``, and call
``holoqa_judge`` — which read the index off disk and returned PASS for bytes the
agent had authored. That is the exact failure this tool exists to prevent, and
it was reachable without touching a single tool parameter.

HoloQA is a long-lived stdio process, and an agent cannot write into another
process's memory. So the authoritative index lives there, in :data:`_LEDGERS`,
and ``run.json`` becomes a *projection* of it rather than the source of truth.
Two consequences follow, and both are deliberate:

* While this process holds the ledger, a hand-edited ``run.json`` or a
  substituted evidence file cannot produce a PASS — the mismatch is detected and
  the step is BLOCKED.
* A process that starts *without* a ledger (a fresh server resuming a run, the
  CLI, the TUI) cannot tell a genuine ``run.json`` from a forged one. It says so
  instead of guessing: the run's integrity is ``unverified``, and packaging
  refuses to decide RELEASE. A human resolves it with ``holoqa verify``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from holoqa import AGENT_ASSERTABLE, BLOCKED, PASS, VERDICTS
from holoqa import plan as plan_module

RUN_FILE = "run.json"
VARS_FILE = "vars.json"
EVIDENCE_DIR = "evidence"
OUT_DIR = "out"
#: The plan as it was when the run started. Judging reads this copy, never the
#: author's file, so a plan edited mid-run cannot change what is being judged.
PLAN_PIN_FILE = "plan.pinned.yaml"
#: Records that a human reviewed an unverified run and accepted it.
ATTEST_FILE = "attestation.json"

_SLUG = re.compile(r"[^a-z0-9]+")


class GuardrailError(ValueError):
    """A guardrail refused the write. The message says which one and why."""


class IntegrityError(GuardrailError):
    """The run's own records do not agree with its bytes."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    return _SLUG.sub("-", str(value).lower()).strip("-") or "item"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Ledger:
    """The authoritative state of a run, held by the process that owns it.

    ``data`` has the same shape as ``run.json``, because every accessor in the
    codebase already goes through :meth:`Run.read`. Keeping the shape identical
    is what makes "prefer memory over disk" a one-line change rather than a
    rewrite.
    """

    data: dict[str, Any]
    plan: plan_module.Plan
    #: Set when the ledger was adopted from disk rather than created here.
    adopted: bool = False
    #: Files this process produced.
    seen_files: set[str] = field(default_factory=set)
    #: Files already in the index when this ledger was created. Their provenance
    #: is unknown to us — another HoloQA process may have made them — so they
    #: make a run *unverifiable*, not forged. A file that appears later, while we
    #: hold the ledger, is a different matter.
    baseline: set[str] = field(default_factory=set)
    #: mtime of run.json as of the last read or write, so a file another process
    #: updated can be picked up without discarding what this one knows it made.
    mtime: float = 0.0


#: Keyed by resolved run directory. Module-level because HoloQA serves one run
#: at a time over stdio; a second entry appears only in tests, which is fine.
_LEDGERS: dict[str, Ledger] = {}


def ledger_for(directory: str | Path) -> Ledger | None:
    return _LEDGERS.get(str(Path(directory).expanduser().resolve()))


def forget_ledger(directory: str | Path) -> None:
    """Drop a run's in-memory state. Used by tests and by explicit re-adoption."""
    _LEDGERS.pop(str(Path(directory).expanduser().resolve()), None)


class Run:
    """One checklist execution, backed by a directory on disk."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory).expanduser().resolve()
        self.evidence_dir = self.dir / EVIDENCE_DIR
        self.out_dir = self.dir / OUT_DIR

    # ------------------------------------------------------------------ setup

    @classmethod
    def create(
        cls,
        directory: str | Path,
        plan: plan_module.Plan,
        meta: dict[str, Any] | None = None,
    ) -> "Run":
        run = cls(directory)
        if (run.dir / RUN_FILE).exists():
            raise GuardrailError(
                f"run already exists at {run.dir}; pick a new directory or resume it"
            )
        run.evidence_dir.mkdir(parents=True, exist_ok=True)
        run.out_dir.mkdir(parents=True, exist_ok=True)

        # Pin the plan as it is now. Judging reads this copy, so editing the
        # author's file mid-run cannot change the contract being applied — that
        # was how a FAIL could be turned into a PASS with no new evidence.
        run.plan_pin.write_bytes(plan.source_bytes)
        run.plan_sha256 = hashlib.sha256(plan.source_bytes).hexdigest()

        steps = {
            step.id: {
                "id": step.id,
                "stage": step.stage,
                "title": step.title,
                "verdict": None,
                "note": "",
                "verdict_note": "",
                "blocked_cause": "",
                "observations": [],
                "assertions": [],
                "kb_refs": [],
                "revisions": [],
                "started_at": None,
                "finished_at": None,
            }
            for step in plan.steps
        }
        data = {
            "run_id": run.dir.name,
            "plan_path": plan.source_path,
            "plan_sha256": run.plan_sha256,
            "app": plan.meta.app,
            "language": plan.meta.language,
            "meta": {**(meta or {}), "started_at": now()},
            "steps": steps,
        }
        # No ledger is installed here on purpose. A process that creates a run
        # but delegates every capture to another HoloQA process (the coding-agent
        # wrapper does exactly that) has no business claiming authority over
        # records it did not make. Ownership is taken in `attach`, by the process
        # that actually produces the bytes.
        run._write(data)
        run._write_vars({})
        return run

    # ------------------------------------------------------------- ledger

    @property
    def plan_pin(self) -> Path:
        return self.dir / PLAN_PIN_FILE

    @property
    def attestation_path(self) -> Path:
        return self.dir / ATTEST_FILE

    def ledger(self, *, adopt: bool = True) -> Ledger | None:
        """This run's in-process ledger, adopting it from disk if necessary.

        ``adopt=False`` returns ``None`` instead of adopting, which is how
        read-only consumers (the report, the TUI, history) avoid promoting an
        unverified ``run.json`` into the authority for a process that did not
        produce it.
        """
        key = str(self.dir)
        held = _LEDGERS.get(key)
        if held is not None:
            return held
        if not adopt:
            return None
        if not (self.dir / RUN_FILE).is_file():
            return None
        try:
            data = json.loads((self.dir / RUN_FILE).read_text(encoding="utf-8"))
            plan = plan_module.load(self.plan_pin)
        except Exception:
            return None
        # Everything already on disk becomes the baseline: this process cannot
        # distinguish the captures a previous server made from ones somebody else
        # planted, so it does not pretend to. Those make the run unverifiable.
        seen: set[str] = set()
        everything = {
            item["file"]
            for step in data.get("steps", {}).values()
            for item in step.get("observations", [])
        }
        held = Ledger(
            data=data, plan=plan, adopted=True, seen_files=seen, baseline=everything
        )
        _LEDGERS[key] = held
        return held

    # ---------------------------------------------------------- integrity

    def integrity(self, *, adopt: bool = True) -> dict[str, Any]:
        """Whether this run's records can be trusted, and why.

        ``verified`` — this process produced every capture it counts.
        ``unverified`` — some captures came from elsewhere (a resumed run, or a
            sibling HoloQA process). Nobody can tell those from a hand-written
            record, so the run cannot decide a release on its own.
        ``tampered`` — a capture appeared while we held the ledger and we did not
            make it, or a file's bytes no longer match the hash recorded for it.
        ``attested`` — unverified, and a human recorded that they read it.
        """
        ledger = self.ledger(adopt=adopt)
        if ledger is None:
            # No ledger and no memory of this run: a run started by an older
            # HoloQA, or read by a process that did not make it. If a human has
            # read the evidence and said so, that is the strongest statement
            # available — report it as such rather than as bare `unverified`,
            # because `holoqa attest` exists for exactly this case.
            if self.attestation_path.is_file():
                return {
                    "status": "attested",
                    "reason": "a human reviewed this run and accepted its records",
                }
            return {
                "status": "unverified",
                "reason": "this process did not create the run and cannot "
                          "distinguish a genuine run.json from a forged one",
            }

        problems = self._tamper_problems(ledger)
        if problems:
            return {
                "status": "tampered",
                "reason": "; ".join(problems),
                "problems": problems,
            }

        foreign = sorted(self._foreign_files(ledger))
        if foreign or ledger.adopted:
            attested = self.attestation_path.is_file()
            detail = (
                f"{len(foreign)} capture(s) in this run were made by another "
                "process: " + ", ".join(foreign)
                if foreign else
                "the ledger was adopted from run.json, so a hand-written record "
                "is indistinguishable from a captured one"
            )
            return {
                "status": "attested" if attested else "unverified",
                "reason": (
                    "a human reviewed this run and accepted its records"
                    if attested else detail
                ),
                "foreign_files": foreign,
            }
        return {
            "status": "verified",
            "reason": "every capture was made by this process",
        }

    def _foreign_files(self, ledger: Ledger) -> set[str]:
        """Captures in the index that this process did not produce."""
        return {
            item["file"]
            for step in ledger.data["steps"].values()
            for item in step.get("observations", [])
            if item["file"] not in ledger.seen_files
        }

    def _tamper_problems(self, ledger: Ledger) -> list[str]:
        problems: list[str] = []
        # A capture that arrived *after* we took ownership, which we did not
        # make, is not a hand-off from another process — it is a record written
        # into the index behind HoloQA's back.
        for name in sorted(self._foreign_files(ledger) - ledger.baseline):
            problems.append(
                f"{name} is not a capture this process made, and it appeared "
                "after this run was already being recorded"
            )
        # A capture whose bytes no longer match the recorded hash.
        for problem in self.verify_evidence():
            problems.append(problem)
        return problems

    def verify_evidence(self, *, adopt: bool = True) -> list[str]:
        """Every place a recorded hash disagrees with the bytes on disk.

        Runs against whatever index is being trusted: the ledger when this
        process owns the run, ``run.json`` when it was handed one.
        """
        ledger = self.ledger(adopt=adopt)
        data = ledger.data if ledger is not None else self.read()
        problems: list[str] = []
        for step_id, step in data.get("steps", {}).items():
            for item in step.get("observations", []):
                path = self.evidence_dir / item["file"]
                if not path.is_file():
                    problems.append(f"step {step_id}: {item['file']} is missing")
                    continue
                actual = sha256_file(path)
                if actual != item.get("sha256"):
                    problems.append(
                        f"step {step_id}: {item['file']} does not match its "
                        f"recorded hash (recorded {str(item.get('sha256'))[:12]}, "
                        f"found {actual[:12]})"
                    )
        return problems

    def attest(self, *, by: str, reason: str) -> dict[str, Any]:
        """Record that a human reviewed an unverified run and accepted it."""
        if not (self.dir / RUN_FILE).is_file():
            raise GuardrailError(f"no run.json in {self.dir}")
        payload = {
            "by": (by or "").strip(),
            "reason": (reason or "").strip(),
            "at": now(),
        }
        if not payload["by"] or len(payload["reason"]) < 10:
            raise GuardrailError(
                "attesting needs --by and a reason of at least 10 characters: "
                "this records that a person accepted an unverifiable run"
            )
        self.attestation_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    # ------------------------------------------------------------------- data

    def _write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.dir / RUN_FILE
        target.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        held = _LEDGERS.get(str(self.dir))
        if held is not None:
            held.data = data
            held.mtime = target.stat().st_mtime

    def read(self) -> dict[str, Any]:
        """The run's state: the ledger when this process owns it, else the file.

        Preferring memory is what closes the forgery: an agent can edit
        ``run.json`` freely — records *and* verdicts — and once this process holds
        a ledger, none of that reaches a decision.

        There is deliberately no "pick up a newer file" path. An earlier version
        merged the file whenever its mtime moved, so that a wrapper process could
        watch a server process's captures. That also re-imported a hand-edited
        ``verdict`` from disk, which is a forged PASS by the shortest route. The
        two writers are indistinguishable at the file level, so the file is not
        consulted once we are authoritative; a process that did not capture the
        records reports the run ``unverified`` instead.
        """
        held = _LEDGERS.get(str(self.dir))
        if held is not None:
            return held.data
        path = self.dir / RUN_FILE
        if not path.is_file():
            raise GuardrailError(f"no run.json in {self.dir}; start a run first")
        return json.loads(path.read_text(encoding="utf-8"))

    def pinned_plan(self) -> plan_module.Plan:
        """The plan the run recorded, without the source-file check.

        For consumers that only need to describe a run — the report, the TUI,
        ``status`` — where refusing to work because the author's file moved would
        be unhelpful. Judging uses :meth:`plan_for_judging`, which does check.
        """
        if self.plan_pin.is_file():
            return plan_module.load(self.plan_pin)
        return plan_module.load(self.read()["plan_path"])

    def plan_for_judging(self) -> plan_module.Plan:
        """The plan to judge against, and the check that it is still the pinned one.

        Two things could go wrong here and both used to be silent: the author's
        plan file could be rewritten mid-run, and the pinned copy could be
        swapped. The first is refused; the second is detected.
        """
        if not self.plan_pin.is_file():
            raise GuardrailError(
                f"this run has no pinned plan ({self.plan_pin.name}); it was "
                "created by an older HoloQA, so what it judged cannot be "
                "established"
            )
        pinned_bytes = self.plan_pin.read_bytes()
        pinned = hashlib.sha256(pinned_bytes).hexdigest()
        recorded = self.read().get("plan_sha256", "")
        if recorded and pinned != recorded:
            raise IntegrityError(
                f"the pinned plan has been changed (recorded {recorded[:12]}, "
                f"found {pinned[:12]}); this run's contract cannot be trusted"
            )
        data = self.read()
        source = Path(data.get("plan_path", ""))
        if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() != pinned:
            raise IntegrityError(
                f"the plan at {source} has changed since this run started; "
                "judging it would apply a different contract than the one the "
                "run recorded. Restore the plan, or start a new run."
            )
        return plan_module.load(self.plan_pin)

    def _write_vars(self, variables: dict[str, Any]) -> None:
        (self.dir / VARS_FILE).write_text(
            json.dumps(variables, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def vars(self) -> dict[str, Any]:
        path = self.dir / VARS_FILE
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def bind(self, name: str, value: Any) -> None:
        variables = self.vars()
        variables[name] = value
        self._write_vars(variables)

    def step(self, data: dict[str, Any], step_id: str) -> dict[str, Any]:
        if step_id not in data["steps"]:
            raise GuardrailError(f"step {step_id} is not in this run")
        return data["steps"][step_id]

    # ------------------------------------------------------------- evidence

    def attach(
        self,
        step_id: str,
        *,
        kind: str,
        path: Path,
        target: str = "",
        summary: dict[str, Any] | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        """Register a captured file as evidence for a step.

        Guardrail 4: a reference to a missing file is refused. Guardrail 1
        depends on this — an empty file never counts as evidence.

        ``actor`` records which browser session produced the bytes. With more
        than one identity in a run, "which session was this captured in" is part
        of what makes the evidence mean something: a step that checks ownership
        is only a test if it ran as the right actor.
        """
        path = Path(path)
        if not path.is_file():
            raise GuardrailError(f"evidence file does not exist: {path}")
        size = path.stat().st_size
        if size == 0:
            raise GuardrailError(f"evidence file is empty: {path.name}")

        data = self.read()
        step = self.step(data, step_id)
        record = {
            "kind": kind,
            "file": path.name,
            "target": target,
            "actor": actor,
            "bytes": size,
            "sha256": sha256_file(path),
            "captured_at": now(),
            "summary": summary or {},
        }
        step["observations"].append(record)
        if not step["started_at"]:
            step["started_at"] = now()
        # Claim authority over this run *here*, in the process that produced the
        # bytes, and remember which file they are. A record that never passes
        # through this method has no entry, which is what makes a hand-written
        # one detectable while the ledger is held.
        #
        # `adopt=False` matters: adopting would import the whole on-disk index
        # and mark every existing file as ours, which is exactly the claim this
        # mechanism exists to avoid making.
        ledger = self.ledger(adopt=False)
        if ledger is None:
            # First capture this process makes. Everything already in the index
            # becomes the baseline: another HoloQA process may have made those,
            # so they make the run *unverifiable* rather than forged. Anything
            # that appears from here on and is not ours was written behind our
            # back, which is a different thing. The record just appended is
            # excluded — it is ours by construction.
            existing = {
                item["file"]
                for step in data["steps"].values()
                for item in step.get("observations", [])
            }
            ledger = Ledger(
                data=data,
                plan=self.pinned_plan(),
                baseline=existing - {path.name},
            )
            _LEDGERS[str(self.dir)] = ledger
        elif ledger.adopted and not ledger.seen_files:
            # A ledger that was adopted by a *read* (an `integrity()` check, a
            # TUI refresh, `holoqa verify`) carries `adopted=True` and would
            # keep the run `unverified` for the rest of its life — even though
            # this process is now demonstrably the one producing the bytes.
            #
            # Capturing is what takes ownership. Any record that was already in
            # the index when we adopted it stays in the baseline (we did not
            # make it), but the ledger stops claiming to be adopted, because it
            # now has a first-hand capture to vouch for.
            #
            # Without this, merely *looking* at a fresh run before capturing in
            # it — which the workspace picker does on every row — permanently
            # marked it untrusted.
            ledger.baseline = set(ledger.baseline) | {
                item["file"]
                for step in data["steps"].values()
                for item in step.get("observations", [])
                if item["file"] != path.name
            }
            ledger.adopted = False
        ledger.seen_files.add(path.name)
        self._write(data)
        return record

    def observations(self, step_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        step = self.step(self.read(), step_id)
        items = step["observations"]
        return [item for item in items if kind is None or item["kind"] == kind]

    def observations_seen_by_this_process(self, step_id: str) -> list[dict[str, Any]]:
        """This step's captures, restricted to the ones this process made.

        The judge uses this rather than the whole index: an observation that
        arrived some other way is not evidence, and reading it would be the
        forgery the ledger exists to stop.
        """
        ledger = self.ledger()
        items = self.step(self.read(), step_id)["observations"]
        if ledger is None:
            return list(items)
        return [item for item in items if item["file"] in ledger.seen_files]

    # -------------------------------------------------------------- verdicts

    def set_verdict(
        self,
        step_id: str,
        verdict: str,
        *,
        note: str = "",
        assertions: list[dict[str, Any]] | None = None,
        by: str = "holoqa",
    ) -> dict[str, Any]:
        """Write a verdict, enforcing guardrails 1, 2, 3 and 5.

        ``by`` records provenance. Only :data:`holoqa.AGENT_ASSERTABLE`
        verdicts may arrive with ``by="agent"``; everything else must be
        derived by :mod:`holoqa.verdict` from HoloQA's own captures.
        """
        if verdict not in VERDICTS:
            raise GuardrailError(
                f"verdict {verdict!r} is not one of {', '.join(VERDICTS)}"
            )
        if by == "agent" and verdict not in AGENT_ASSERTABLE:
            raise GuardrailError(
                f"an agent may only assert {', '.join(AGENT_ASSERTABLE)}; "
                f"{verdict} is derived by HoloQA from captured evidence"
            )

        data = self.read()
        step = self.step(data, step_id)

        note = (note or "").strip()

        # A derived verdict explains itself in `verdict_note`; a human's own
        # observation lives in `note` and survives re-judging. Keeping them
        # apart stops a stale failure message from trailing a later PASS.
        derived = by != "agent"
        cause = note if derived else (note or step.get("note", ""))

        # Guardrail 1: no evidence, no pass.
        if verdict == PASS and not step["observations"]:
            raise GuardrailError(
                f"step {step_id}: PASS refused, no evidence captured. "
                "Capture an observation first, or record BLOCKED with a cause."
            )
        # Guardrail 2: a non-pass must say why.
        if verdict != PASS and not (cause or step.get("note", "")):
            raise GuardrailError(
                f"step {step_id}: {verdict} requires a note giving the concrete cause"
            )

        # Guardrail 5: a changed verdict leaves the superseded one behind.
        previous = step.get("verdict")
        if previous and previous != verdict:
            step["revisions"].append(
                {
                    "from": previous,
                    "to": verdict,
                    "at": now(),
                    "previous_note": step.get("verdict_note") or step.get("note", ""),
                }
            )

        step["verdict"] = verdict
        if derived:
            step["verdict_note"] = cause
        else:
            step["note"] = cause
            step["verdict_note"] = ""
        step["decided_by"] = by
        # A machine-readable cause, so a report can group N blocked steps by root
        # cause instead of printing N agent sentences. "requires api not
        # satisfied: ..." is the same string for every step that shares a
        # precondition, which is what makes the grouping meaningful.
        step["blocked_cause"] = (
            _blocked_cause(assertions) if verdict == BLOCKED and assertions else ""
        )
        if assertions is not None:
            step["assertions"] = assertions
        step["finished_at"] = now()
        if not step["started_at"]:
            step["started_at"] = now()
        self._write(data)
        return step

    def note(self, step_id: str, note: str, kb_refs: list[str] | None = None) -> dict[str, Any]:
        data = self.read()
        step = self.step(data, step_id)
        if note:
            step["note"] = note.strip()
        for ref in kb_refs or []:
            if ref not in step["kb_refs"]:
                step["kb_refs"].append(ref)
        if not step["started_at"]:
            step["started_at"] = now()
        self._write(data)
        return step

    # --------------------------------------------------------------- status

    def blocked_dependencies(self, plan: plan_module.Plan, step_id: str) -> list[str]:
        """Guardrail 7: a step cannot be judged until its prerequisites passed."""
        data = self.read()
        unmet = []
        for dependency in plan.step(step_id).depends_on:
            if data["steps"].get(dependency, {}).get("verdict") != PASS:
                unmet.append(dependency)
        return unmet

    def status(self, plan: plan_module.Plan) -> dict[str, Any]:
        data = self.read()
        steps = data["steps"]
        counts = {verdict: 0 for verdict in VERDICTS}
        pending: list[str] = []
        for step_id in plan.step_ids:
            verdict = steps.get(step_id, {}).get("verdict")
            if verdict in counts:
                counts[verdict] += 1
            else:
                pending.append(step_id)
        next_step = pending[0] if pending else None
        return {
            "run_id": data["run_id"],
            "run_dir": str(self.dir),
            "app": data.get("app", ""),
            "meta": data.get("meta", {}),
            "total": len(plan.step_ids),
            "counts": counts,
            "pending": pending,
            "next_step": next_step,
            "vars": self.vars(),
            # An incomplete checklist is not releasable. This matters to the
            # agent wrapper: an agent that exits before judging a step must
            # never make an all-zero run look green.
            "decision": "HOLD" if pending else decision(counts),
        }


def _blocked_cause(assertions: list[dict[str, Any]]) -> str:
    """The shared reason a step blocked: a prerequisite that the plan declared.

    Only a ``requires`` failure produces a cause here. An agent-asserted BLOCKED
    has free-text prose, which cannot be grouped — and grouping is the point:
    "64 blocked" is not a finding, "41 blocked on the same missing fixture" is.
    """
    for item in assertions:
        if item.get("kind") == "requires":
            return str(item.get("detail", ""))
    return ""


def decision(counts: dict[str, int]) -> str:
    """One failure holds the release.

    Carried over from the prior tool's release-decision block: *"Satu baris
    Gagal berarti keputusan Tahan."*
    """
    if counts.get("FAIL"):
        return "HOLD"
    if counts.get(BLOCKED):
        return "HOLD"
    return "RELEASE"


def find(directory: str | Path | None = None) -> Run:
    """Resolve the active run: an explicit path, ``$HOLOQA_RUN_DIR``, or the current one.

    Workspace-aware. The order is:

    1. an explicit directory;
    2. ``$HOLOQA_RUN_DIR``;
    3. the **current run of the workspace** the process is standing in — the
       pointer a user set by switching, which is what makes "the run I am in"
       real rather than "whatever is newest";
    4. a legacy flat ``.holoqa/runs/``, newest first, so an old checkout keeps
       working without being migrated.
    """
    import os

    if directory:
        return Run(directory)
    env = os.environ.get("HOLOQA_RUN_DIR")
    if env:
        return Run(env)

    # Imported here rather than at module scope: workspace imports this module.
    from holoqa import workspace as workspace_module

    for ws in workspace_module.find_workspaces(start=Path.cwd()):
        current = ws.current_run_id()
        if current:
            return Run(ws.run_dir(current))
        ids = ws.run_ids()
        if ids:
            return Run(ws.run_dir(ids[-1]))

    root = Path(".holoqa/runs").resolve()
    if root.is_dir():
        candidates = sorted(
            (item for item in root.iterdir() if (item / RUN_FILE).is_file()),
            key=lambda item: item.name,
        )
        if candidates:
            return Run(candidates[-1])
    raise GuardrailError(
        "no active run; pass run_dir, set HOLOQA_RUN_DIR, or start a run first"
    )
