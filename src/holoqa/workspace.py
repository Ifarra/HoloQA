"""Workspaces — one home per application, many runs inside it.

A **workspace** is a directory that owns every run of one application:

    .holoqa/workspaces/<app>/
      workspace.json          the index: plan, current run, run list
      runs/
        001-20261001-1430/    run #1 — kept, even after it failed
        002-20261001-1512/    retest — a NEW run, never an overwrite
        003-20261001-1604/

Why this exists
---------------

A run used to *be* a directory named ``<YYYYMMDD-HHMM>-<app>``, and
``Run.create`` refused an existing name. So:

* two runs in the same minute collided and the second was **refused**, and
* a developer who fixed a bug and retested had to **delete the failing run**
  first — destroying the evidence that the fix was needed.

That is the opposite of what a QA tool should teach. A failing run is a record,
not garbage: it is what a regression is measured against. So a retest appends a
new run and the old one stays.

Identity vs location
--------------------

``run_id`` used to be derived from the folder name (``run.py``: ``run_id =
run.dir.name``). That coupled identity to location, which is what made a retest
impossible. Here the workspace **allocates** a run id and derives the folder from
it, so two runs can never collide and neither has to be deleted.

Nothing here relaxes the trust model. The in-process ledger, the pinned plan and
the hash checks are untouched: a switched-to old run is still ``unverified`` in a
process that did not capture it, and the picker says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from holoqa import BLOCKED, FAIL, PASS
from holoqa import plan as plan_module
from holoqa import run as run_module
from holoqa.run import GuardrailError, Run, decision, now

WORKSPACES_DIR = "workspaces"
INDEX_FILE = "workspace.json"
RUNS_DIR = "runs"
TRASH_DIR = ".trash"
#: Legacy layout: ``.holoqa/runs/<stamp>-<app>``. Read, never written.
LEGACY_RUNS_DIR = "runs"

_SEQ = re.compile(r"^(\d{3})-")


def default_root(start: Path | None = None) -> Path:
    """The ``.holoqa`` directory a workspace lives under.

    Resolved by walking up from ``start`` (a plan file or a directory) looking
    for an **established** ``.holoqa``, then for a **project boundary** (a
    ``.git`` directory), then falling back to ``<start>/.holoqa``.

    This is the single resolver the whole codebase needs. Previously the TUI
    (``request.cwd.resolve()/.holoqa/runs``), the wrapper (``request.cwd/.holoqa/runs``)
    and ``run.find()`` (process-relative ``.holoqa/runs``) each computed their own
    root, so a listing built on one silently missed runs made by another.

    Three details matter, and all three were found by getting them wrong:

    * A candidate must actually *hold a layout* (``workspaces/`` or ``runs/``).
      A bare ``.holoqa`` directory is not evidence of a project — a stray one
      anywhere above would otherwise capture every project beneath it.
    * The walk stops at the filesystem root and never turns a project inside a
      temp directory into a workspace in the user's home. An unbounded walk is
      how a test run ended up writing to ``~/.holoqa``.
    * Falling back to ``<start>/.holoqa`` put a ``.holoqa`` in **every plan's
      directory**, so a monorepo with ``shop/`` and ``admin/`` got two isolated
      workspaces and no picker could see both. When no root exists yet, anchor on
      the project boundary instead: that is what makes one console able to own
      several applications.
    """
    base = Path(start or Path.cwd())
    if base.is_file():
        base = base.parent
    base = base.expanduser().resolve()

    fallback = base
    for candidate in (base, *base.parents):
        root = candidate / ".holoqa"
        if root.is_dir() and ((root / WORKSPACES_DIR).is_dir() or (root / LEGACY_RUNS_DIR).is_dir()):
            return root
        # Remember the outermost project boundary seen on the way up, so a
        # monorepo shares one root rather than one per subdirectory.
        if (candidate / ".git").exists():
            fallback = candidate
    return fallback / ".holoqa"



def slug_app(app: str) -> str:
    """A directory-safe workspace name."""
    return run_module.slugify(app) or "app"


def _legacy_base(root: Path | None, workspace_dir: Path) -> Path:
    """The legacy ``runs/`` directory, from either a project or a ``.holoqa`` root.

    Accepting both spellings is deliberate: two call sites naturally hold
    different things (``migrate`` has the project directory, a workspace has its
    own path), and a mismatch here does not raise — it returns an empty list that
    reads as "there are no legacy runs", which is the worst possible failure for
    a migration that is supposed to find them.
    """
    if root is None:
        return workspace_dir.parent.parent / LEGACY_RUNS_DIR
    root = Path(root).expanduser().resolve()
    if root.name == ".holoqa":
        return root / LEGACY_RUNS_DIR
    if (root / ".holoqa").is_dir():
        return root / ".holoqa" / LEGACY_RUNS_DIR
    return root / LEGACY_RUNS_DIR


class Workspace:
    """Every run of one application, and a pointer to the current one."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory).expanduser().resolve()
        self.runs_dir = self.dir / RUNS_DIR
        self.trash_dir = self.dir / TRASH_DIR

    # ------------------------------------------------------------------ paths

    @property
    def index_path(self) -> Path:
        return self.dir / INDEX_FILE

    @property
    def app(self) -> str:
        return self.dir.name

    # ------------------------------------------------------------------ index

    def exists(self) -> bool:
        return self.index_path.is_file()

    def read(self) -> dict[str, Any]:
        """The index, or a minimal in-memory one for a workspace not yet written."""
        if not self.index_path.is_file():
            return {
                "app": self.app,
                "plan_path": "",
                "created_at": now(),
                "current_run": "",
                "runs": [],
            }
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise GuardrailError(
                f"{self.index_path} is not valid JSON: {error}. The runs themselves "
                "are unaffected; rebuild the index from the run directories."
            ) from error

    def _write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------- run lookup

    def run_dir(self, run_id: str) -> Path:
        candidate = (self.runs_dir / run_id).resolve()
        if not candidate.is_relative_to(self.runs_dir.resolve()):
            raise GuardrailError(f"run id {run_id!r} escapes the workspace")
        return candidate

    def run(self, run_id: str) -> Run:
        return Run(self.run_dir(run_id))

    def run_ids(self) -> list[str]:
        """Run ids on disk, numbered runs first in order, then legacy ones."""
        if not self.runs_dir.is_dir():
            return []
        numbered, other = [], []
        for item in self.runs_dir.iterdir():
            if not (item / run_module.RUN_FILE).is_file():
                continue
            (numbered if _SEQ.match(item.name) else other).append(item.name)
        return sorted(numbered) + sorted(other)

    def next_sequence(self) -> int:
        used = [
            int(match.group(1))
            for name in self.run_ids()
            if (match := _SEQ.match(name))
        ]
        return (max(used) + 1) if used else 1

    def current_run_id(self) -> str:
        data = self.read()
        recorded = str(data.get("current_run") or "")
        if recorded and (self.runs_dir / recorded / run_module.RUN_FILE).is_file():
            return recorded
        # Fall back to the newest, so a hand-edited or missing pointer cannot
        # strand the user with no run at all.
        ids = self.run_ids()
        return ids[-1] if ids else ""

    def switch(self, run_id: str) -> str:
        """Point the workspace at ``run_id``. Returns the new current run id."""
        if not (self.runs_dir / run_id / run_module.RUN_FILE).is_file():
            raise GuardrailError(f"no run {run_id!r} in workspace {self.app!r}")
        data = self.read()
        data["current_run"] = run_id
        data.setdefault("app", self.app)
        self._write(data)
        return run_id

    # ------------------------------------------------------------- start runs

    def start_run(
        self,
        plan: plan_module.Plan,
        meta: dict[str, Any] | None = None,
        *,
        retest_of: str = "",
        run_id: str = "",
    ) -> Run:
        """Allocate a run and create it. Never refuses for a name collision.

        The workspace names the run, so a second retest in the same minute is
        simply run 002 rather than a refused creation. ``Run.create`` keeps its
        refusal for a caller that names its own directory — that guardrail is
        still correct; what was wrong was guessing a name that could collide.
        """
        if not run_id:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            run_id = f"{self.next_sequence():03d}-{stamp}"
        meta = dict(meta or {})
        if retest_of:
            meta["retest_of"] = retest_of
        run = Run.create(self.runs_dir / run_id, plan, meta=meta)

        data = self.read()
        data.setdefault("app", self.app)
        data["plan_path"] = plan.source_path
        data.setdefault("created_at", now())
        data["current_run"] = run_id
        data["runs"] = [item for item in data.get("runs", []) if item.get("run_id") != run_id]
        data["runs"].append(self._summary(run, run_id, retest_of=retest_of))
        data["runs"].sort(key=lambda item: str(item.get("run_id", "")))
        self._write(data)
        return run

    def retest(self, plan: plan_module.Plan, meta: dict[str, Any] | None = None) -> Run:
        """Start a new run from the same plan, recording what it retests.

        The previous run is left exactly where it is. That is the point: the
        failing run is the baseline a fix is measured against.
        """
        return self.start_run(plan, meta=meta, retest_of=self.current_run_id())

    # ---------------------------------------------------------------- summary

    def _summary(self, run: Run, run_id: str, *, retest_of: str = "") -> dict[str, Any]:
        """A picker row: cheap facts, no evidence walk."""
        try:
            data = run.read()
        except (OSError, ValueError, GuardrailError):
            return {"run_id": run_id, "at": "", "decision": "UNREADABLE",
                    "counts": {}, "retest_of": retest_of or None, "integrity": "unverified"}
        counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
        pending = 0
        for step in data.get("steps", {}).values():
            verdict = step.get("verdict")
            if verdict in counts:
                counts[verdict] += 1
            else:
                pending += 1
        meta = data.get("meta", {})
        return {
            "run_id": run_id,
            "at": meta.get("started_at", ""),
            "tag": meta.get("tag", ""),
            "tester": meta.get("tester", ""),
            "counts": counts,
            "pending": pending,
            "total": sum(counts.values()) + pending,
            "decision": "HOLD" if pending else decision(counts),
            "retest_of": retest_of or meta.get("retest_of") or None,
            "integrity": run.integrity(adopt=False)["status"],
        }

    def refresh(self, run_id: str) -> dict[str, Any]:
        """Re-read one run and update its index row. ``run.json`` is authority."""
        run = self.run(run_id)
        data = self.read()
        prior = next(
            (item for item in data.get("runs", []) if item.get("run_id") == run_id), {}
        )
        summary = self._summary(run, run_id, retest_of=str(prior.get("retest_of") or ""))
        data["runs"] = [
            item for item in data.get("runs", []) if item.get("run_id") != run_id
        ] + [summary]
        data["runs"].sort(key=lambda item: str(item.get("run_id", "")))
        self._write(data)
        return summary

    def list_runs(self) -> list[dict[str, Any]]:
        """Every run row, newest first, reconciled against disk.

        The index is a cache for a picker; a run directory that the index does
        not know about is still listed (and adopted), because losing a run from
        the list is worse than a slow list.
        """
        data = self.read()
        known = {str(item.get("run_id")): item for item in data.get("runs", [])}
        rows: list[dict[str, Any]] = []
        on_disk = self.run_ids()
        for run_id in on_disk:
            if run_id in known and known[run_id].get("decision") not in (None, "", "UNREADABLE"):
                rows.append(known[run_id])
            else:
                rows.append(
                    self._summary(
                        self.run(run_id), run_id,
                        retest_of=str((known.get(run_id) or {}).get("retest_of") or ""),
                    )
                )
        rows.sort(key=lambda item: str(item.get("run_id", "")), reverse=True)
        return rows

    def legacy_runs(self, root: Path | None = None) -> list[dict[str, Any]]:
        """Runs in the old flat layout that belong to this workspace's app.

        ``root`` is the **project directory** (the one holding ``.holoqa``), or
        the ``.holoqa`` directory itself — both spellings are accepted, because
        a caller naturally has one or the other to hand and silently looking in
        the wrong one returns an empty list that reads as "no legacy runs".

        Read-only. They can be viewed and retested from; they are never moved
        without an explicit ``migrate``.
        """
        base = _legacy_base(root, self.dir)
        if not base.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for item in sorted(base.iterdir()):
            if not (item / run_module.RUN_FILE).is_file():
                continue
            try:
                data = json.loads((item / run_module.RUN_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(data.get("app", "")) != self.app:
                continue
            run = Run(item)
            rows.append({**self._summary(run, item.name), "legacy": True,
                         "path": str(item)})
        return rows

    # ----------------------------------------------------------------- trash

    def delete(self, run_id: str, *, force: bool = False) -> Path:
        """Move a run to ``.trash/``. Never unlinks, never touches the current run.

        Deleting is housekeeping a human chose, never a step required to retest.
        The bytes are kept so an accidental delete is recoverable by hand, which
        is the difference between a cleanup and a data loss.
        """
        if not (self.runs_dir / run_id / run_module.RUN_FILE).is_file():
            raise GuardrailError(f"no run {run_id!r} in workspace {self.app!r}")
        if run_id == self.current_run_id() and not force:
            raise GuardrailError(
                f"{run_id} is the current run; switch to another run before deleting it"
            )
        source = self.runs_dir / run_id
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = self.trash_dir / run_id
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%H%M%S")
            target = self.trash_dir / f"{run_id}-{stamp}"
        shutil.move(str(source), str(target))

        data = self.read()
        data["runs"] = [item for item in data.get("runs", []) if item.get("run_id") != run_id]
        if data.get("current_run") == run_id:
            data["current_run"] = self.run_ids()[-1] if self.run_ids() else ""
        self._write(data)
        return target

    # ------------------------------------------------------------------ misc

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Workspace({self.dir})"


def workspace_for(
    plan_or_path: plan_module.Plan | str | Path,
    *,
    root: Path | None = None,
) -> Workspace:
    """The workspace for a plan, resolved from the plan's own directory.

    Anchoring on the plan — not the process cwd — is what makes the same
    workspace reachable from any directory, which is the property the old
    process-relative lookup lacked.
    """
    if isinstance(plan_or_path, plan_module.Plan):
        app = plan_or_path.meta.app
        source = plan_or_path.source_path
    else:
        app = ""
        source = str(plan_or_path)
    base = root or default_root(source or Path.cwd())
    name = slug_app(app or Path(source).stem.replace(".plan", ""))
    return Workspace(base / WORKSPACES_DIR / name)


def find_workspaces(root: Path | None = None, *, start: Path | None = None) -> list[Workspace]:
    """Every workspace under a root, newest activity first.

    Also returns **synthetic** workspaces for legacy runs: a project that has
    only ``.holoqa/runs/<stamp>-<app>`` and no ``workspace.json`` yet still has
    runs a user must be able to list. Returning nothing there would read as "you
    have no runs", which is the worst possible answer for a migration that is
    supposed to find them.
    """
    base = (root or default_root(start)) / WORKSPACES_DIR
    found: list[Workspace] = []
    if base.is_dir():
        for item in base.iterdir():
            if not item.is_dir():
                continue
            # Accept a workspace that has an index, OR one that has a runs/
            # directory: a workspace whose first run has not started yet is
            # still a workspace, and hiding it would make a real application
            # look like it does not exist.
            if (item / INDEX_FILE).is_file() or (item / RUNS_DIR).is_dir():
                found.append(Workspace(item))

    known = {ws.app for ws in found}
    legacy_root = (root or default_root(start)) / LEGACY_RUNS_DIR
    if legacy_root.is_dir():
        apps: set[str] = set()
        for item in legacy_root.iterdir():
            run_file = item / run_module.RUN_FILE
            if not run_file.is_file():
                continue
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            app = slug_app(str(data.get("app", "")))
            if app and app not in known:
                apps.add(app)
        for app in sorted(apps):
            found.append(Workspace(base / app))

    # A synthetic workspace has no directory yet, so sort on the nearest real
    # path rather than the (missing) workspace dir.
    def _mtime(ws: Workspace) -> float:
        for candidate in (ws.dir, ws.runs_dir):
            try:
                return candidate.stat().st_mtime
            except OSError:
                continue
        return 0.0

    found.sort(key=_mtime, reverse=True)
    return found


def migrate(workspace: Workspace, *, root: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Move legacy ``.holoqa/runs/<app>`` runs into the workspace layout.

    Opt-in and printed. Every byte is preserved under a new name; nothing is
    rewritten. Migration is a command a human runs, never something that happens
    to their evidence directories on its own.
    """
    legacy = workspace.legacy_runs(root)
    plan: list[tuple[Path, Path]] = []
    sequence = workspace.next_sequence()
    for index, row in enumerate(sorted(legacy, key=lambda r: str(r["run_id"]))):
        source = Path(str(row["path"]))
        stamp = source.name
        target = workspace.runs_dir / f"{sequence + index:03d}-{stamp}"
        plan.append((source, target))

    if dry_run:
        return {
            "status": "dry_run",
            "workspace": str(workspace.dir),
            "moves": [{"from": str(a), "to": str(b)} for a, b in plan],
        }

    moved = []
    for source, target in plan:
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append({"from": str(source), "to": str(target)})

    data = workspace.read()
    data.setdefault("app", workspace.app)
    data.setdefault("created_at", now())
    workspace._write(data)
    for run_id in workspace.run_ids():
        workspace.refresh(run_id)
    if not workspace.current_run_id():
        ids = workspace.run_ids()
        if ids:
            workspace.switch(ids[-1])
    return {"status": "migrated", "workspace": str(workspace.dir), "moved": moved}
