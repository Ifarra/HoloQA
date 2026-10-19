"""Codex app-server stdio adapter. No global config edits or unattended fallback.

Protocol baseline: codex-cli 0.159.2 (generated JSON schemas). Dynamic tools are
experimental and negotiated explicitly. Keep QA tools in the owning process.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
from typing import Any, Callable

from holoqa.agent import _provider_command


class ProtocolError(ValueError):
    pass


def _kill_on_close(job):
    """If the desktop/sidecar crashes, Windows closes this handle and its tree."""
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Limits(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_mem", ctypes.c_size_t),
                    ("job_mem", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t)]
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    job.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    job.kernel.SetInformationJobObject.restype = w.BOOL
    if not job.kernel.SetInformationJobObject(job.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())


class CodexSession:
    def __init__(self, cwd: Path, emit: Callable, tool: Callable | None = None,
                 command: list[str] | None = None):
        binary = shutil.which("codex")
        if command is None and not binary:
            raise ProtocolError("Codex CLI is not installed. Install it and sign in with `codex login`.")
        self.emit, self.tool = emit, tool
        self.cwd = cwd
        self.pending: dict[int, Future] = {}
        self.approvals: dict[str, Any] = {}
        self.lock, self.write_lock = threading.RLock(), threading.Lock()
        self.approval_lock = threading.Lock()
        self.sequence, self.closed = 0, False
        self.thread_id, self.turn_id = "", ""
        self.finished = threading.Event()
        self.turn_status = ""
        self.disconnected = False
        self.pausing = False
        self._starting_turn = False
        self._early_completions: dict[str, str] = {}
        self._early_requests: list[dict] = []
        self._tool_generation = 0
        self._tool_slots = threading.BoundedSemaphore(8)
        self._tool_dispatch = threading.Lock()
        self.tools = ThreadPoolExecutor(max_workers=1, thread_name_prefix="holoqa-tools")
        self.job = None
        self.process = subprocess.Popen(
            command or _provider_command(binary, ["app-server", "--listen", "stdio://"]),
            cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=os.name != "nt",
            # CREATE_SUSPENDED establishes Job ownership before execution;
            # CREATE_NO_WINDOW keeps console CLIs inside the desktop UI.
            creationflags=(0x00000004 | 0x08000000) if os.name == "nt" else 0,
        )
        if os.name == "nt":
            from holoqa.process import WindowsJob
            try:
                self.job = WindowsJob(self.process._handle)
                _kill_on_close(self.job)
                self.job.resume(self.process.pid)
            except Exception:
                self.close()
                raise
        self.reader = threading.Thread(target=self._read, daemon=True, name="codex-stdio")
        self.reader.start()

    def send(self, message):
        with self.write_lock:
            if self.closed:
                raise ProtocolError("Codex connection is closed")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def request(self, method, params=None, timeout=25):
        future = Future()
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            self.pending[request_id] = future
        try:
            self.send({"id": request_id, "method": method, "params": params or {}})
            try:
                return future.result(timeout=timeout)
            except TimeoutError as error:
                raise ProtocolError("Codex did not respond in time. Check CLI compatibility and sign-in, then retry.") from error
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def initialize(self):
        self.request("initialize", {"clientInfo": {"name": "holoqa_desktop", "version": "0.1.0"},
                                    "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})
        result = self.request("account/read", {"refreshToken": False})
        # Never forward account identifiers, tokens, or provider stderr to UI.
        return "ready" if result.get("account") or not result.get("requiresOpenaiAuth", True) else "signed_out"

    def start(self, prompt, specs):
        response = self.request("thread/start", {
            "cwd": str(self.process_cwd), "approvalPolicy": "untrusted",
            "approvalsReviewer": "user", "sandbox": "read-only", "ephemeral": True,
            "dynamicTools": specs,
            "developerInstructions": "Run QA only. Do not edit source, plans, configuration, or fixtures. "
                "Use the provided holoqa tools to operate the browser and gather evidence. "
                "Do not launch another HoloQA MCP process. HoloQA alone computes results.",
        })
        self.thread_id = response["thread"]["id"]
        self._start_turn(prompt)

    def _start_turn(self, prompt):
        with self.lock:
            if self.closed or self.disconnected:
                raise ProtocolError("Codex connection is closed")
            self._starting_turn = True
            self._early_completions.clear()
            self._early_requests.clear()
            self.turn_status = ""
            self.finished.clear()
            self.pausing = False
        try:
            result = self.request("turn/start", {"threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt, "text_elements": []}]})
            with self.lock:
                self.turn_id = result["turn"]["id"]
                if self.turn_id in self._early_completions:
                    self.turn_status = self._early_completions[self.turn_id]
                    self.finished.set()
                # Switch to normal event routing atomically with assigning the
                # ID; otherwise a completion between this block and finally
                # could be buffered and then discarded.
                self._starting_turn = False
                self._early_completions.clear()
                early_requests, self._early_requests = self._early_requests, []
            # A fast server may request tools before the caller applies the
            # turn/start reply. Admit them only after validating that reply's ID.
            for message in early_requests:
                self.handle(message)
        finally:
            with self.lock:
                self._starting_turn = False
                self._early_completions.clear()
                self._early_requests.clear()

    def interrupt(self):
        """Request interruption; only turn/completed confirms a paused turn."""
        # An answer already being sent must precede the interruption; an
        # answer arriving afterward must see pausing and be refused. Keep this
        # separate from self.lock so close can still kill a stalled pipe.
        with self.approval_lock:
            return self._interrupt()

    def _interrupt(self):
        with self.lock:
            if self.closed or self.disconnected or self._starting_turn:
                raise ProtocolError("No active Codex turn to pause")
            if self.finished.is_set():
                return False
            self.pausing = True
            self._tool_generation += 1
            approvals = list(self.approvals.items())
            self.approvals.clear()
            turn_id = self.turn_id
        for key, request_id in approvals:
            self.send({"id": request_id, "result": {"decision": "decline"}})
            self.emit("approval.resolved", {"id": key})
        self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": turn_id}, timeout=10)
        # Drain callbacks accepted before interruption before Engine can resume.
        with self._tool_dispatch:
            pass
        return True

    def resume(self, prompt):
        """Continue the existing thread only after a confirmed interruption."""
        with self.lock:
            if (self.closed or self.disconnected or not self.pausing or
                    not self.finished.is_set() or self.turn_status != "interrupted"):
                raise ProtocolError("Codex is not paused on a resumable turn")
        self._start_turn(prompt)

    @property
    def process_cwd(self):
        return self.cwd

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 2 * 1024 * 1024:
                    raise ProtocolError("Codex exceeded the protocol frame size")
                message = json.loads(line)
                self.handle(message)
        except Exception:
            self.emit("provider.error", {"message": "Codex protocol connection failed. Check CLI compatibility."})
        finally:
            with self.lock:
                self.disconnected = True
                for f in list(self.pending.values()):
                    if not f.done():
                        f.set_exception(ProtocolError("Codex disconnected"))
            self.finished.set()

    def handle(self, message):
        if "method" not in message:
            with self.lock:
                future = self.pending.get(message.get("id"))
                if future and not future.done():
                    if "error" in message:
                        future.set_exception(ProtocolError("Codex rejected a protocol request; update to a compatible CLI."))
                    else:
                        future.set_result(message.get("result", {}))
            return
        method, params = message["method"], message.get("params") or {}
        if "id" in message:
            request_id = message["id"]
            if method in {"item/tool/call", "item/commandExecution/requestApproval"}:
                with self.lock:
                    if self._starting_turn and len(self._early_requests) < 8:
                        self._early_requests.append(message)
                        return
            if method == "item/tool/call" and self.tool:
                with self.lock:
                    generation = self._tool_generation
                    accepted = (not self._starting_turn and self._current_turn(params) and
                                not (self.closed or self.pausing or self.finished.is_set()))
                    accepted = accepted and self._tool_slots.acquire(blocking=False)
                    if accepted:
                        self.tools.submit(self._tool_reply, request_id, params, generation)
                if not accepted and not self.closed:
                    self.send({"id": request_id, "result": {"success": False,
                        "contentItems": [{"type": "inputText", "text": "QA tool is unavailable or busy; retry only during the active turn."}]}})
            elif method == "item/commandExecution/requestApproval":
                import uuid
                key = uuid.uuid4().hex
                command = params.get("command")
                with self.lock:
                    refuse = (self.closed or self.pausing or self.finished.is_set() or
                              self._starting_turn or not self._current_turn(params) or
                              not isinstance(command, str) or not command.strip() or len(command) > 6000)
                    if not refuse:
                        self.approvals[key] = request_id
                        # Serialize event publication with interrupt/close so a
                        # stale dialog cannot appear after approvals are cleared.
                        self.emit("approval.request", {"id": key, "command": command,
                            "cwd": params.get("cwd"), "reason": params.get("reason"),
                            "network": params.get("networkApprovalContext"),
                            "permissions": params.get("additionalPermissions")})
                if refuse and not self.closed:
                    self.send({"id": request_id, "result": {"decision": "decline"}})
            elif method == "item/fileChange/requestApproval":
                self.send({"id": request_id, "result": {"decision": "decline"}})
                self.emit("activity", {"message": "Source edits refused: this is a QA run."})
            else:
                self.send({"id": request_id, "error": {"code": -32601,
                    "message": "This desktop does not support that request; no permission granted."}})
                self.emit("activity", {"message": "Unsupported provider request refused: " + method[:100]})
        elif method == "turn/completed":
            turn = params.get("turn", {})
            turn_id = turn.get("id")
            with self.lock:
                if params.get("threadId") != self.thread_id or not turn_id:
                    return
                if self._starting_turn:
                    if len(self._early_completions) < 8:
                        self._early_completions.setdefault(turn_id, turn.get("status", "unknown"))
                elif turn_id == self.turn_id and not self.finished.is_set():
                    self.turn_status = turn.get("status", "unknown")
                    self.finished.set()
        elif method == "item/completed":
            item = params.get("item", {})
            self.emit("activity", {"message": "Codex completed: " + str(item.get("type", "activity"))[:100]})

    def _current_turn(self, params):
        return (bool(self.thread_id and self.turn_id) and
                params.get("threadId") == self.thread_id and params.get("turnId") == self.turn_id)

    def _tool_reply(self, request_id, params, generation):
        try:
            with self._tool_dispatch:
                with self.lock:
                    if (generation != self._tool_generation or not self._current_turn(params) or self.closed or
                            self.pausing or self.finished.is_set()):
                        raise ProtocolError("This QA tool request expired before execution")
                result = self.tool(params.get("tool", ""), params.get("arguments", {}))
            response = {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(result)}]}
        except Exception as error:
            response = {"success": False, "contentItems": [{"type": "inputText", "text": str(error)[:3000]}]}
        finally:
            self._tool_slots.release()
        try:
            self.send({"id": request_id, "result": response})
        except (OSError, ProtocolError):
            pass

    def approve(self, key, decision):
        with self.approval_lock:
            return self._approve(key, decision)

    def _approve(self, key, decision):
        if decision not in ("accept", "decline"):
            raise ProtocolError("Only Allow once or Deny is supported")
        with self.lock:
            if self.closed or self.pausing or self.finished.is_set():
                raise ProtocolError("Approval expired or the run is paused")
            if key not in self.approvals:
                raise ProtocolError("Approval expired or already answered")
            request_id = self.approvals.pop(key)
        self.send({"id": request_id, "result": {"decision": decision}})
        self.emit("approval.resolved", {"id": key})

    def close(self):
        # Never wait behind a blocked pipe writer before killing the reader.
        # Terminating the process first releases any in-flight stdin write.
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.approvals.clear()
        try:
            if self.job:
                self.job.terminate()
            elif os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
        finally:
            if self.job:
                self.job.close()
            self.tools.shutdown(wait=True, cancel_futures=True)
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()
            self.finished.set()
