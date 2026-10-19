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
        self.sequence, self.closed = 0, False
        self.thread_id, self.turn_id = "", ""
        self.finished = threading.Event()
        self.turn_status = ""
        self.tools = ThreadPoolExecutor(max_workers=1, thread_name_prefix="holoqa-tools")
        self.job = None
        self.process = subprocess.Popen(
            command or _provider_command(binary, ["app-server", "--listen", "stdio://"]),
            cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=os.name != "nt",
            creationflags=0x00000004 if os.name == "nt" else 0,
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
        result = self.request("turn/start", {"threadId": self.thread_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}]})
        self.turn_id = result["turn"]["id"]

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
            if method == "item/tool/call" and self.tool:
                self.tools.submit(self._tool_reply, request_id, params)
            elif method == "item/commandExecution/requestApproval":
                import uuid
                key = uuid.uuid4().hex
                with self.lock:
                    self.approvals[key] = request_id
                # No session-wide or persistent policy approval is offered.
                self.emit("approval.request", {"id": key, "command": str(params.get("command") or "")[:6000],
                    "cwd": params.get("cwd"), "reason": params.get("reason"),
                    "network": params.get("networkApprovalContext"),
                    "permissions": params.get("additionalPermissions")})
            elif method == "item/fileChange/requestApproval":
                self.send({"id": request_id, "result": {"decision": "decline"}})
                self.emit("activity", {"message": "Source edits refused: this is a QA run."})
            else:
                self.send({"id": request_id, "error": {"code": -32601,
                    "message": "This desktop does not support that request; no permission granted."}})
                self.emit("activity", {"message": "Unsupported provider request refused: " + method[:100]})
        elif method == "turn/completed":
            self.turn_status = params.get("turn", {}).get("status", "unknown")
            self.finished.set()
        elif method == "item/completed":
            item = params.get("item", {})
            self.emit("activity", {"message": "Codex completed: " + str(item.get("type", "activity"))[:100]})

    def _tool_reply(self, request_id, params):
        try:
            result = self.tool(params.get("tool", ""), params.get("arguments", {}))
            response = {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(result)}]}
        except Exception as error:
            response = {"success": False, "contentItems": [{"type": "inputText", "text": str(error)[:3000]}]}
        try:
            self.send({"id": request_id, "result": response})
        except (OSError, ProtocolError):
            pass

    def approve(self, key, decision):
        if decision not in ("accept", "decline"):
            raise ProtocolError("Only Allow once or Deny is supported")
        with self.lock:
            if key not in self.approvals:
                raise ProtocolError("Approval expired or already answered")
            request_id = self.approvals.pop(key)
        self.send({"id": request_id, "result": {"decision": decision}})
        self.emit("approval.resolved", {"id": key})

    def close(self):
        with self.write_lock:
            if self.closed:
                return
            self.closed = True
        with self.lock:
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
