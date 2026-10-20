"""Claude Code session adapter for the supervised desktop runner.

Claude's CLI has no app-server approval channel. Desktop scans therefore use
its non-interactive mode with an explicit allowlist of HoloQA tools only.
Plan approval remains human-gated in the UI, and
the run can be paused, resumed in the same Claude session, or stopped.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from holoqa import agent


class ClaudeSession:
    def __init__(self, cwd: Path, emit, run, *, model: str = ""):
        self.cwd, self.emit, self.run = Path(cwd), emit, run
        self.model = model
        self.session_id = str(uuid.uuid4())
        self.directory = tempfile.TemporaryDirectory(prefix="holoqa-desktop-claude-")
        self.finished = threading.Event()
        self.cancel_event = threading.Event()
        self.request = agent.AgentRunRequest(
            provider="claude", cwd=self.cwd, mode="unattended", model=model,
            output_callback=self._output, cancel_event=self.cancel_event, desktop_run=True,
        )
        self.turn_status = ""
        self.result_error = False
        self.disconnected = False
        self.closed = False
        self.worker: threading.Thread | None = None
        self.lock = threading.RLock()

    def initialize(self):
        binary = agent.provider_binary("claude")
        if not binary:
            raise agent.AgentError("Claude Code CLI is not installed. Install it and sign in with `claude`.")
        try:
            result = subprocess.run(agent._provider_command(binary, ["auth", "status"]),
                                    cwd=self.cwd, capture_output=True, text=True, timeout=15,
                                    encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return "unchecked"
        try:
            import json
            status = json.loads(result.stdout)
            if isinstance(status, dict) and status.get("loggedIn") is False:
                return "signed_out"
        except (TypeError, ValueError):
            pass
        return "ready" if result.returncode == 0 else "signed_out"

    def start(self, prompt, specs=None, *, developer_instructions=None):
        if developer_instructions:
            prompt = developer_instructions + "\n\n" + prompt
        self._start_turn(prompt, resume=False)

    def _start_turn(self, prompt, *, resume):
        with self.lock:
            if self.closed:
                raise agent.AgentError("Claude session is closed")
            self.cancel_event = threading.Event()
            self.request = agent.AgentRunRequest(
                provider="claude", cwd=self.cwd, mode="unattended", model=self.model,
                output_callback=self._output, cancel_event=self.cancel_event, desktop_run=True,
            )
            self.finished.clear()
            self.turn_status = ""
            self.result_error = False
            self.disconnected = False
            worker = threading.Thread(target=self._turn, args=(prompt, resume),
                                      name="holoqa-claude", daemon=True)
            self.worker = worker
            worker.start()

    def _turn(self, prompt, resume):
        try:
            spec = agent.build_launch(self.request, self.run, prompt, Path(self.directory.name))
            args = list(spec.command)
            if not args or args[-1] != prompt:
                raise agent.AgentError("Cannot safely prepare Claude's session prompt")
            if resume:
                args[-1:-1] = ["--resume", self.session_id]
            else:
                args[-1:-1] = ["--session-id", self.session_id]
            spec = agent.LaunchSpec(args, spec.env, spec.config_path)
            code = agent._stream_process(spec, self.request, self.run)
            reason = getattr(code, "reason", "")
            if reason == "interrupted" or (code == 130 and self.cancel_event.is_set()):
                self.turn_status = "interrupted"
            elif code == 0 and not self.result_error:
                self.turn_status = "completed"
            else:
                self.turn_status = "failed"
                self.disconnected = True
                self.emit("provider.error", {"message": "Claude Code ended without completing the QA turn. Check its sign-in and retry."})
        except Exception as error:
            self.turn_status = "failed"
            self.disconnected = True
            message = str(error)
            self.emit("provider.error", {"message": message[:500] if "auth" in message.lower() or "install" in message.lower()
                else "Claude Code could not start or continue the QA turn."})
        finally:
            self.finished.set()

    def _output(self, channel, line):
        if channel != "stdout":
            return
        # Keep provider transcripts private. The UI receives only coarse
        # activity labels and never raw prompts, tool arguments, or responses.
        import json
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.emit("activity", {"message": "Claude Code session started"})
        elif kind == "assistant":
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool = str(block.get("name", "tool"))
                    label = "HoloQA tool" if tool.startswith("mcp__holoqa__") else "Browser action"
                    self.emit("activity", {"message": f"Claude Code requested a {label}"})
        elif kind == "result":
            if event.get("is_error") or event.get("subtype") not in {None, "success"}:
                self.result_error = True
            self.emit("activity", {"message": "Claude Code finished its current turn"})

    def interrupt(self):
        with self.lock:
            if self.closed or self.finished.is_set():
                return False
            self.cancel_event.set()
            return True

    def resume(self, prompt):
        self._start_turn(prompt, resume=True)

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=10)
        self.directory.cleanup()
