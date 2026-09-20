"""Disposable localhost sandbox for manually exercising the HoloQA TUI."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SandboxError(RuntimeError):
    """The local TUI sandbox could not be prepared."""


@dataclass
class TuiSandbox:
    root: Path
    plan: Path
    base_url: str
    server: ThreadingHTTPServer
    thread: threading.Thread

    @classmethod
    def create(cls) -> "TuiSandbox":
        source = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent-smoke"
        if not source.is_dir():
            raise SandboxError(f"sandbox fixture is missing: {source}")
        root = Path(tempfile.mkdtemp(prefix="holoqa-tui-sandbox-"))
        try:
            for item in source.iterdir():
                target = root / item.name
                shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=root, check=True,
                capture_output=True, text=True, timeout=15,
            )

            def handler_init(self, *args, **kwargs):
                SimpleHTTPRequestHandler.__init__(
                    self, *args, directory=str(root), **kwargs
                )

            handler = type(
                "SandboxHandler",
                (SimpleHTTPRequestHandler,),
                {"__init__": handler_init},
            )
            handler.log_message = lambda self, *args: None
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address[:2]
            return cls(root, root / "plan.yaml", f"http://{host}:{port}", server, thread)
        except Exception as error:
            shutil.rmtree(root, ignore_errors=True)
            if isinstance(error, SandboxError):
                raise
            raise SandboxError(f"could not prepare TUI sandbox: {error}") from error

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def cleanup(self) -> None:
        """Remove only this generated sandbox directory."""
        self.close()
        if self.root.name.startswith("holoqa-tui-sandbox-"):
            shutil.rmtree(self.root, ignore_errors=True)
