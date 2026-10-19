"""Versioned desktop JSON-lines bridge. Stdio only; stdout is protocol-only.

Run this as a long-lived process so the evidence ledger is never reconstructed
on screen navigation. The native shell, not web content, chooses project roots.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any, get_type_hints
import uuid

from pydantic import create_model, ConfigDict

from holoqa import browser, plan as plans, report, workspace
from holoqa.desktop_codex import CodexSession
from holoqa.run import Run
from holoqa.storage import atomic_json

PROTOCOL = 1
LIMIT = 1024 * 1024
SKIP = {".git", "node_modules", ".venv", "venv", "target", "dist", "build", ".holoqa", ".next"}
TOOLS = ("holoqa_browser", "holoqa_observe", "holoqa_judge", "holoqa_block", "holoqa_note", "holoqa_run_status")


class DesktopError(ValueError):
    pass


class Engine:
    def __init__(self, state_dir: Path, emit=lambda *_: None):
        self.state_dir, self.emit = state_dir, emit
        self.root: Path | None = None
        self.reviews: dict[str, tuple] = {}
        self.run: Run | None = None
        self.run_states: dict[str, str] = {}
        self.provider: CodexSession | None = None
        self.lifecycle = "idle"
        self.lock = threading.RLock()
        self.tool_lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.deadline = 0.0
        self.closing = False
        self.provider_status = {"installed": bool(shutil.which("codex")), "authentication": "unchecked"}

    @property
    def lifecycle(self):
        return self._lifecycle

    @lifecycle.setter
    def lifecycle(self, value):
        self._lifecycle = value
        if self.run:
            self.run_states[str(self.run.dir)] = value

    def run_state(self, run):
        return self.run_states.get(str(run.dir), "historical")

    def gated_status(self, run):
        status = run.status(run.pinned_plan())
        # Proven assertions and a completed provider lifecycle are separate.
        # Never display RELEASE while running, interrupted, or failed.
        if self.run_state(run) in {"starting", "running", "stopping", "interrupted", "failed"}:
            status["decision"] = "HOLD"
        return status

    def recent(self):
        try:
            values = json.loads((self.state_dir / "recent.json").read_text("utf-8"))
            return [str(v) for v in values if isinstance(v, str)][:12] if isinstance(values, list) else []
        except (OSError, ValueError):
            return []

    def contained(self, relative: str, *, exists=True):
        if self.root is None:
            raise DesktopError("Open a project folder first")
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise DesktopError("Path leaves the selected project")
        if exists and not candidate.exists():
            raise DesktopError("The selected file is missing")
        return candidate

    def _idle(self):
        if self.lifecycle in {"starting", "running", "stopping"}:
            raise DesktopError("Stop the active run before changing projects or starting another")

    def open_project(self, path):
        self._idle()
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise DesktopError("Select an existing project directory")
        self.root, self.reviews, self.run = root, {}, None
        recent = [str(root)] + [p for p in self.recent() if p != str(root)]
        atomic_json(self.state_dir / "recent.json", recent[:12])
        return self.project()

    def project(self):
        if not self.root:
            return None
        found, scanned = [], 0
        for parent, dirs, files in os.walk(self.root, followlinks=False):
            rel = Path(parent).relative_to(self.root)
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(parent) / d).is_symlink())
            if len(rel.parts) >= 3:
                dirs[:] = []
            for name in sorted(files):
                scanned += 1
                if scanned > 5000 or len(found) >= 50:
                    break
                if name == "holoqa.plan.yaml" or name.endswith((".plan.yaml", ".plan.yml")):
                    candidate = Path(parent) / name
                    if candidate.resolve().is_relative_to(self.root) and candidate.stat().st_size <= LIMIT:
                        found.append(str(candidate.relative_to(self.root)))
            if scanned > 5000 or len(found) >= 50:
                break
        framework = "Local project"
        try:
            package = json.loads(self.contained("package.json").read_text("utf-8"))
            dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            framework = "Next.js" if "next" in dependencies else "React" if "react" in dependencies else "Node.js"
        except (OSError, ValueError, DesktopError):
            pass
        return {"name": self.root.name, "path": str(self.root), "framework": framework,
                "plans": found, "scanLimited": scanned > 5000 or len(found) >= 50}

    def load_plan(self, path):
        candidate = self.contained(path)
        if not candidate.is_file() or candidate.stat().st_size > LIMIT:
            raise DesktopError("Plan must be a file smaller than 1 MiB")
        plan = plans.load(candidate)
        summary = plans.lint(candidate)
        return plan, {"path": str(candidate.relative_to(self.root)), "sha256": plan.source_sha256,
            "app": plan.meta.app, "target": plan.meta.base_url, "stages": [s.model_dump() for s in plan.stages],
            "steps": [s.model_dump() for s in plan.steps], "warnings": summary["warnings"]}

    def review(self, path, digest):
        _, summary = self.load_plan(path)
        if summary["sha256"] != digest:
            raise DesktopError("The plan changed. Reload and review it again.")
        key = uuid.uuid4().hex
        self.reviews[key] = (summary["path"], digest, summary["target"])
        return {"token": key}

    def create_plan(self, app, url):
        self._idle()
        from urllib.parse import urlparse
        if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname:
            raise DesktopError("Enter a valid HTTP or HTTPS application URL")
        # Strict app identifiers also keep the YAML scaffold a simple scalar.
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", app):
            raise DesktopError("Use a short app name with letters, numbers, hyphens, or underscores")
        candidate = self.contained("holoqa.plan.yaml", exists=False)
        # Render URL through YAML to prevent quotes/newlines altering the scaffold.
        import yaml
        content = plans.scaffold(app, "https://example.invalid")
        data = yaml.safe_load(content)
        data["meta"]["base_url"] = url
        with candidate.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
        return self.load_plan("holoqa.plan.yaml")[1]

    def check_provider(self):
        self._idle()
        self.provider_status = {"installed": bool(shutil.which("codex")), "authentication": "unchecked"}
        if not self.provider_status["installed"]:
            return self.provider_status
        session = CodexSession(self.root or Path.home(), self.emit)
        try:
            self.provider_status["authentication"] = session.initialize()
        finally:
            session.close()
        return self.provider_status

    def _specs(self):
        from holoqa import mcp
        specs = []
        self.tool_models = {}
        for name in TOOLS:
            fn = getattr(mcp, name)
            hints = get_type_hints(fn)
            fields = {key: (hints[key], ... if p.default is inspect.Parameter.empty else p.default)
                      for key, p in inspect.signature(fn).parameters.items() if key != "run_dir"}
            model = create_model(name, __config__=ConfigDict(extra="forbid"), **fields)
            self.tool_models[name] = model
            specs.append({"type": "function", "name": name, "description": inspect.getdoc(fn) or name,
                          "inputSchema": model.model_json_schema()})
        return specs

    def tool(self, name, arguments):
        with self.tool_lock:
            if self.lifecycle != "running" or not self.run:
                raise DesktopError("No active QA run")
            if name not in TOOLS or not isinstance(arguments, dict):
                raise DesktopError("Unsupported QA tool")
            # Agents cannot redirect captures to another run or mutate the plan.
            args = self.tool_models[name].model_validate(arguments).model_dump()
            from holoqa import mcp
            result = getattr(mcp, name)(**args, run_dir=str(self.run.dir))
            self.emit("activity", {"message": name + " completed"})
            self.emit("run.updated", {})
            return result

    def start(self, token):
        self._idle()
        reviewed = self.reviews.get(token)
        if not reviewed:
            raise DesktopError("Review the complete plan before starting")
        path, digest, target = reviewed
        plan, summary = self.load_plan(path)
        if summary["sha256"] != digest or summary["target"] != target:
            self.reviews.pop(token, None)
            raise DesktopError("Plan or resolved target changed. Review again.")
        if not target.startswith(("http://", "https://")) or "${" in target:
            raise DesktopError("Set a resolved HTTP or HTTPS base_url in the plan")
        if not shutil.which("agent-browser"):
            raise DesktopError("Install agent-browser and its browser runtime before running QA")
        specs = self._specs()
        provider = CodexSession(self.root, self.emit, self.tool)
        try:
            if provider.initialize() != "ready":
                raise DesktopError("Sign into Codex CLI with `codex login`, then check the connection")
            self.run = workspace.workspace_for(plan, root=self.contained(".holoqa", exists=False)).start_run(
                plan, meta={"base_url": target, "desktop": True})
            self.provider = provider
            self.lifecycle = "starting"
            # Pin the exact plan object loaded and checked above, not a later read.
            from holoqa.agent import workflow_prompt
            prompt = workflow_prompt(self.run.plan_pin, self.run.dir)
            prompt += "\nUse the supplied holoqa_browser dynamic tool for ALL browser actions. "
            prompt += "Do not run agent-browser through a shell. No other HoloQA process owns this run."
            self.lifecycle = "running"
            provider.start(prompt, specs)
        except Exception:
            self.lifecycle = "failed"
            provider.close()
            with self.tool_lock:
                if self.run:
                    browser.cleanup(self.run)
            self.provider = None
            raise
        self.reviews.pop(token, None)
        self.deadline = time.monotonic() + 1800
        self.worker = threading.Thread(target=self._wait, args=(provider,), daemon=True)
        self.worker.start()
        self.emit("run.updated", {})
        return self.snapshot()

    def _wait(self, provider):
        while not provider.finished.wait(.5):
            if time.monotonic() >= self.deadline:
                self.stop()
                return
        # Freeze tool dispatch before cleanup; never destroy a capture in flight.
        with self.lock:
            if self.provider is not provider:
                return
            self.lifecycle = "completed" if provider.turn_status == "completed" else "failed"
            provider.close()
            with self.tool_lock:
                if self.run:
                    errors = browser.cleanup(self.run)
                    if errors:
                        self.emit("activity", {"message": "Browser cleanup needs attention"})
            self.provider = None
            self.emit("approvals.cleared", {})
            self.emit("run.updated", {})

    def stop(self):
        with self.lock:
            provider, self.provider = self.provider, None
            if provider:
                self.lifecycle = "stopping"
                provider.close()
                with self.tool_lock:
                    if self.run:
                        browser.cleanup(self.run)
                self.lifecycle = "interrupted"
                self.emit("approvals.cleared", {})
                self.emit("run.updated", {})
        return {"lifecycle": self.lifecycle}

    def history(self):
        self.contained(".")
        result = []
        for ws in workspace.find_workspaces(root=self.contained(".holoqa", exists=False)):
            for run_id in ws.run_ids()[-50:]:
                run = ws.run(run_id)
                if not run.dir.is_relative_to(self.root):
                    continue
                try:
                    status = self.gated_status(run)
                    result.append({"path": str(run.dir.relative_to(self.root)),
                        "id": run_id, "app": status["app"], "counts": status["counts"],
                        "decision": status["decision"], "integrity": status["integrity"],
                        "total": status["total"]})
                except (OSError, ValueError):
                    continue
        return sorted(result, key=lambda r: r["id"], reverse=True)[:100]

    def selected_run(self, path=""):
        if not path and self.run:
            return self.run
        candidate = self.contained(path)
        known = {r["path"] for r in self.history()}
        if str(candidate.relative_to(self.root)) not in known:
            raise DesktopError("Select a run from this project's history")
        return Run(candidate)

    def snapshot(self, path=""):
        if not path and not self.run:
            return None
        with self.tool_lock:
            run = self.selected_run(path)
            status = self.gated_status(run)
            # Captured variable values are deliberately excluded from the UI.
            status.pop("vars", None)
            data = run.read()
            return {"status": status, "path": str(run.dir.relative_to(self.root)),
                    "steps": list(data["steps"].values()),
                    "lifecycle": self.run_state(run)}

    def artifact(self, path, file):
        run = self.selected_run(path)
        candidate = (run.evidence_dir / file).resolve()
        known = {o["file"] for s in run.read()["steps"].values() for o in s.get("observations", [])}
        if (file not in known or not candidate.is_relative_to(run.evidence_dir.resolve())
                or not candidate.is_relative_to(self.root)):
            raise DesktopError("Only captured artifacts inside this run can be viewed")
        if not candidate.is_file() or candidate.stat().st_size > 4 * LIMIT:
            raise DesktopError("Artifact missing or too large for the inline viewer")
        contents = candidate.read_bytes()
        recorded = next(o for s in run.read()["steps"].values() for o in s.get("observations", []) if o["file"] == file)
        if hashlib.sha256(contents).hexdigest() != recorded.get("sha256"):
            raise DesktopError("Artifact bytes changed after capture; refusing the preview")
        # Only inert formats; never embed HTML or SVG from a target application.
        if contents.startswith(b"\x89PNG\r\n\x1a\n"):
            return {"image": "data:image/png;base64," + base64.b64encode(contents).decode()}
        if contents.startswith(b"\xff\xd8\xff"):
            return {"image": "data:image/jpeg;base64," + base64.b64encode(contents).decode()}
        text = contents.decode("utf-8", errors="replace")
        if len(text) > 200_000:
            text = text[:200_000] + "\n[Preview truncated; inspect the original artifact for the full capture.]"
        return {"text": text}

    def dispatch(self, method, params):
        if method == "hello":
            return {"protocol": PROTOCOL, "recent": self.recent(), "project": self.project(),
                    "provider": self.provider_status, "browserInstalled": bool(shutil.which("agent-browser")),
                    "qaTools": [tool["name"] for tool in self._specs()]}
        if method == "run.stop":
            return self.stop()
        if method == "approval.respond":
            if not self.provider:
                raise DesktopError("Provider is no longer running")
            self.provider.approve(params["id"], params["decision"])
            return {"ok": True}
        if method == "shutdown":
            self.closing = True
            return self.stop()
        with self.lock:
            if self.closing:
                raise DesktopError("Desktop engine is shutting down")
            if method == "project.open":
                return self.open_project(params["path"])
            if method == "project.recent_open":
                if params["path"] not in self.recent():
                    raise DesktopError("Project is not in the recent list; use Open folder")
                return self.open_project(params["path"])
            if method == "project.refresh":
                return self.project()
            if method == "plan.load":
                return self.load_plan(params["path"])[1]
            if method == "plan.review":
                return self.review(params["path"], params["sha256"])
            if method == "plan.create":
                return self.create_plan(params["app"], params["url"])
            if method == "provider.check":
                return self.check_provider()
            if method == "run.start":
                return self.start(params["token"])
            if method == "run.snapshot":
                return self.snapshot(params.get("path", ""))
            if method == "run.history":
                return self.history()
            if method == "artifact.read":
                return self.artifact(params["path"], params["file"])
            if method == "run.export":
                self._idle()
                selected = self.selected_run(params.get("path", ""))
                if self.run_state(selected) in {"interrupted", "failed"}:
                    raise DesktopError("This provider run did not complete. Inspect its evidence or start a new run before exporting.")
                # Workbooks are outside this milestone; do not follow a plan path.
                result = report.package(selected)
                return {"message": "Report saved in the run's out folder", "result": result}
            raise DesktopError("Unsupported desktop method")


def main():
    # Windows pipes otherwise inherit a legacy console code page. The Rust
    # transport and Codex protocol are UTF-8 regardless of the user's locale.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    write_lock = threading.Lock()
    def write(message):
        with write_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    engine = Engine(args.state_dir, lambda event, data: write({"event": event, "data": data}))
    def handle(message):
        request_id = message.get("id")
        try:
            result = engine.dispatch(message["method"], message.get("params") or {})
            write({"id": request_id, "result": result})
        except Exception as error:
            # No tracebacks/locals/credentials on the protocol stream.
            write({"id": request_id, "error": {"message": str(error)[:3000]}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        try:
            while line := sys.stdin.readline(LIMIT + 1):
                if len(line) > LIMIT:
                    break
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict) or "id" not in message:
                        continue
                except ValueError:
                    continue
                if message.get("method") == "shutdown":
                    handle(message)
                    break
                pool.submit(handle, message)
        finally:
            pool.shutdown(wait=True)
            engine.closing = True
            engine.stop()


if __name__ == "__main__":
    main()
