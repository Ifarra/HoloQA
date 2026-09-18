"""End-to-end through a real browser against a real server.

Everything else in the suite uses synthetic evidence. This test exercises the
part that can only be proven by running it: agent-browser is invoked, bytes land
on disk, and the verdict is computed from those bytes.

Skipped when agent-browser is absent, so the offline suite stays green.
"""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from holoqa import PASS
from holoqa import observe as observe_module
from holoqa import plan as plan_module
from holoqa import report as report_module
from holoqa import verdict as verdict_module
from holoqa.run import Run

pytestmark = pytest.mark.skipif(
    shutil.which("agent-browser") is None,
    reason="agent-browser is not installed",
)

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>HoloQA fixture</title></head>
<body><main><h1>Order status</h1><p id="state">Ready</p>
<button id="go" onclick="document.getElementById('state').textContent='Complete'">Continue</button>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("set-cookie", "session=supersecret; Path=/")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/things/"):
            return self._json(200, {"id": self.path.rsplit("/", 1)[-1], "status": "DONE"})
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):  # noqa: N802
        self._json(201, {"id": "thing_42", "status": "PENDING"})

    def log_message(self, *args):  # silence the test output
        pass


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


E2E_PLAN = """
meta:
  app: e2e
  base_url: {base}
steps:
  - id: E1
    title: Page loads and the order is ready
    expect:
      - url_contains: 127.0.0.1
      - text_contains: Order status
      - screenshot: required
  - id: E2
    title: Creating a thing returns 201 PENDING
    expect:
      - api: {{ method: POST, path: /things, status: 201 }}
      - json: {{ id: thing_42, status: PENDING }}
      - capture: {{ thing_id: $.id }}
  - id: E3
    title: The created thing can be read back
    depends_on: [E2]
    expect:
      - api: {{ method: GET, path: "/things/{{thing_id}}", status: 200 }}
      - json: {{ status: DONE }}
"""


def test_real_browser_capture_drives_a_real_verdict(tmp_path: Path, server: str):
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(E2E_PLAN.format(base=server), encoding="utf-8")
    plan = plan_module.load(plan_path)
    run = Run.create(tmp_path / ".holoqa" / "runs" / "e2e", plan, meta={"base_url": server})

    observe_module.run_cli(["open", server])

    # E1 — page state, captured by HoloQA rather than described by a caller.
    observe_module.dom(run.evidence_dir / "e1-page.json")
    run.attach("E1", kind="dom", path=run.evidence_dir / "e1-page.json")
    observe_module.screenshot(run.evidence_dir / "e1-page.png")
    run.attach("E1", kind="screenshot", path=run.evidence_dir / "e1-page.png")

    outcome, results, _ = verdict_module.evaluate(plan, run, "E1")
    assert outcome == PASS, results
    run.set_verdict("E1", outcome, assertions=results)

    # E2 — an in-page fetch, so the browser's own session is used.
    observe_module.api(run.evidence_dir / "e2-create.json", "POST", f"{server}/things")
    run.attach("E2", kind="api", path=run.evidence_dir / "e2-create.json")

    outcome, results, captures = verdict_module.evaluate(plan, run, "E2")
    assert outcome == PASS, results
    assert captures == {"thing_id": "thing_42"}
    run.set_verdict("E2", outcome, assertions=results)

    # Guardrail 6 — the response set a session cookie and it is not on disk.
    # The browser refuses to expose Set-Cookie to fetch at all, so an in-page
    # capture cannot leak it even before redaction runs.
    raw = (run.evidence_dir / "e2-create.json").read_text(encoding="utf-8")
    stored = json.loads(raw)
    assert "set-cookie" not in {key.lower() for key in stored["headers"]}
    assert "supersecret" not in raw

    # E3 — the captured id interpolates into the next request.
    url = f"{server}/things/{run.vars()['thing_id']}"
    observe_module.api(run.evidence_dir / "e3-read.json", "GET", url)
    run.attach("E3", kind="api", path=run.evidence_dir / "e3-read.json")

    outcome, results, _ = verdict_module.evaluate(plan, run, "E3")
    assert outcome == PASS, results
    run.set_verdict("E3", outcome, assertions=results)

    result = report_module.package(run, strict=True)
    assert result["decision"] == "RELEASE"
    assert result["counts"][PASS] == 3
    assert Path(result["archive"]).is_file()


def test_credentials_in_a_request_body_never_reach_evidence(tmp_path: Path, server: str):
    """A captured login must not write the password into the shipped ZIP."""
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(E2E_PLAN.format(base=server), encoding="utf-8")
    plan = plan_module.load(plan_path)
    run = Run.create(tmp_path / ".holoqa" / "runs" / "creds", plan)

    observe_module.run_cli(["open", server])
    destination = run.evidence_dir / "login.json"
    observe_module.api(
        destination,
        "POST",
        f"{server}/things",
        json.dumps({"email": "tester@example.test", "password": "hunter2-do-not-ship"}),
    )

    raw = destination.read_text(encoding="utf-8")
    assert "hunter2-do-not-ship" not in raw
    assert "<redacted>" in raw
    assert "tester@example.test" in raw, "non-secret fields stay readable as evidence"


def test_screenshot_bytes_are_real_and_hashed(tmp_path: Path, server: str):
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(E2E_PLAN.format(base=server), encoding="utf-8")
    plan = plan_module.load(plan_path)
    run = Run.create(tmp_path / ".holoqa" / "runs" / "shots", plan)

    observe_module.run_cli(["open", server])
    destination = run.evidence_dir / "shot.png"
    observe_module.screenshot(destination)
    record = run.attach("E1", kind="screenshot", path=destination)

    assert destination.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert record["bytes"] > 1000
    assert len(record["sha256"]) == 64
