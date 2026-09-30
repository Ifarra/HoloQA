"""Capture layer — HoloQA drives ``agent-browser`` and owns the bytes.

The agent says *what* to capture; this module performs it. That is the whole
trust boundary: a verdict is computed from files HoloQA wrote, so an agent
cannot report an observation that never happened.

``agent-browser`` is a deterministic CLI (Playwright underneath). Nothing here
calls a model.

Two pieces of prior-art knowledge are preserved deliberately:

* **Windows.** ``agent-browser`` is a ``.cmd`` shim and needs a shell, and
  arguments containing quotes or ``%`` are mangled by ``cmd``. Every JavaScript
  payload is therefore base64-encoded, which reduces it to an alphabet ``cmd``
  cannot misread.
* **Redaction.** Evidence is shipped in a ZIP, so credential-bearing headers are
  replaced before anything touches disk.

API captures run as an in-page ``fetch`` rather than an out-of-band HTTP client.
They inherit the browser's own session — including HttpOnly cookies — which
removes the manual cookie-copying step the prior tool needed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REDACT_KEYS = re.compile(r"cookie|authorization|token|secret|password|set-cookie", re.I)

#: Default seconds to hold an SSE stream open before summarizing it.
SSE_SECONDS = 20


class CaptureError(RuntimeError):
    """The capture did not happen. Never silently degraded into a verdict."""


#: Browser flags the current plan asked for, from ``meta.browser``. Module-level
#: because HoloQA serves one run at a time over stdio, and threading a plan
#: object through every capture helper would add a parameter that only ever
#: carries one value.
_BROWSER_ARGS: list[str] = []


def set_browser_options(ignore_https_errors: bool = False, args: list[str] | None = None) -> None:
    """Apply ``meta.browser`` for this process.

    A staging host with a self-signed certificate is a property of the
    application under test, so it belongs in the plan beside that application
    rather than in an environment variable someone has to remember.
    """
    global _BROWSER_ARGS
    flags: list[str] = []
    if ignore_https_errors:
        flags.append("--ignore-https-errors")
    flags.extend(args or [])
    _BROWSER_ARGS = flags


def _browser_args() -> list[str]:
    return _BROWSER_ARGS


# --------------------------------------------------------------- agent-browser


def _binary() -> str:
    found = shutil.which("agent-browser")
    if not found:
        raise CaptureError(
            "agent-browser is not on PATH. Install it, then retry — HoloQA never "
            "launches a browser by itself."
        )
    return found


def run_cli(
    args: list[str],
    *,
    timeout: int = 120,
    session: str = "",
    browser_args: list[str] | None = None,
) -> str:
    """Invoke agent-browser and return stdout.

    On Windows the resolved binary is a ``.cmd``; CreateProcess cannot execute
    one directly, so it goes through ``cmd /c``. Arguments are passed as a list
    so the runtime quotes them.

    ``session`` isolates the call in its own agent-browser session, which is how
    a multi-actor plan keeps one identity's cookies out of another's.

    It is passed as the ``AGENT_BROWSER_SESSION`` environment variable rather
    than the ``--session`` flag. agent-browser 0.27.0 rejects the flag before a
    subcommand ("unexpected browser command: --session") and hangs on it, and the
    environment variable is the mechanism its own documentation and test suite
    use. The value is scoped to this one subprocess, so it cannot leak into a
    later capture.

    Output goes to a temporary *file*, not a pipe. agent-browser spawns a
    long-lived daemon; when stdout is a pipe the daemon inherits the write end
    and the parent never sees EOF, so a named session hangs even though it
    printed its result. This was observed on Windows with agent-browser 0.27.0:
    the same command with a piped stdout never returned, while redirecting to a
    file completed in two seconds. Reading a file sidesteps it entirely.
    """
    binary = _binary()
    args = list(args)
    command = [binary, *args]
    if os.name == "nt" and binary.lower().endswith((".cmd", ".bat")):
        command = ["cmd", "/c", binary, *args]
    environment = dict(os.environ)
    if session:
        environment["AGENT_BROWSER_SESSION"] = session
    # Plan-declared browser options travel as the documented env var, so they
    # apply to the browser the daemon already runs rather than only to this
    # invocation. A self-signed staging certificate is a property of the
    # application under test, not of one command.
    merged_args = list(browser_args or []) + list(_browser_args())
    if merged_args:
        existing = environment.get("AGENT_BROWSER_ARGS", "").strip()
        environment["AGENT_BROWSER_ARGS"] = (
            f"{existing} {' '.join(merged_args)}".strip()
        )
    handle, capture = tempfile.mkstemp(prefix="holoqa-cli-", suffix=".out")
    os.close(handle)
    try:
        with open(capture, "wb") as sink:
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    env=environment,
                )
            except subprocess.TimeoutExpired as error:
                raise CaptureError(
                    f"agent-browser timed out after {timeout}s: {' '.join(args[:2])}"
                ) from error
        raw = Path(capture).read_bytes().decode("utf-8", errors="replace")
    finally:
        try:
            os.unlink(capture)
        except OSError:
            pass
    if completed.returncode != 0:
        detail = raw.strip()
        raise CaptureError(f"agent-browser {args[0]} failed: {detail or 'no output'}")
    return raw.strip()


def evaluate(js: str, *, timeout: int = 120, session: str = "") -> Any:
    """Run JavaScript in the page and return its decoded result.

    The payload is base64-wrapped so no quoting survives to reach ``cmd``.
    """
    encoded = base64.b64encode(js.encode("utf-8")).decode("ascii")
    output = run_cli(["eval", f"eval(atob('{encoded}'))"], timeout=timeout, session=session)
    if output.startswith("✗"):
        raise CaptureError(f"page rejected eval: {output}")
    value: Any = output
    for _ in range(2):  # agent-browser can double-encode JSON results
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            break
    return value


def redact(headers: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: ("<redacted>" if REDACT_KEYS.search(key) else value)
        for key, value in (headers or {}).items()
    }


def redact_payload(value: Any, depth: int = 0) -> Any:
    """Strip credential-bearing values out of a request or response body.

    Headers are not the only place a secret travels. A captured
    ``POST /api/auth/login`` carries the password in its body, and evidence is
    shipped in a ZIP, so the body is walked too.
    """
    if depth > 12:
        return value
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if REDACT_KEYS.search(str(key)) else redact_payload(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item, depth + 1) for item in value]
    return value


def redact_text(text: str | None) -> str | None:
    """Best-effort redaction for a body that did not parse as JSON."""
    if not text:
        return text
    pattern = re.compile(
        r'("?(?:%s)"?\s*[:=]\s*)("[^"]*"|[^\s,&}]+)' % REDACT_KEYS.pattern, re.I
    )
    return pattern.sub(r"\1<redacted>", text)


# ------------------------------------------------------------------- captures


def screenshot(
    destination: Path, selector: str = "", full_page: bool = True, *, session: str = ""
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = ["screenshot"]
    if selector:
        args.append(selector)
    args.append(str(destination))
    if full_page and not selector:
        args.append("--full")
    run_cli(args, session=session)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise CaptureError(f"screenshot was not produced: {destination}")
    return {"selector": selector or "<page>", "full_page": full_page and not selector}


_DOM_JS = """
(() => {
  const text = (document.body && document.body.innerText) || '';
  return JSON.stringify({
    url: location.href,
    title: document.title,
    text: text.slice(0, 20000),
    text_length: text.length,
    truncated: text.length > 20000
  });
})()
"""


def dom(destination: Path, *, session: str = "") -> dict[str, Any]:
    """Capture URL, title, and visible text as a compact JSON record.

    Deliberately not an accessibility snapshot: the prior tool found that
    snapshots of report and findings pages flood the caller's context. The
    caller gets a summary; the full text lands on disk as evidence.
    """
    result = evaluate(_DOM_JS, session=session)
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise CaptureError(f"unexpected dom capture result: {result!r}")
    _write_json(destination, result)
    return {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "text_length": result.get("text_length", 0),
        "truncated": result.get("truncated", False),
    }


_API_JS = """
(async () => {
  const method = %(method)s;
  const url = %(url)s;
  const body = %(body)s;
  const extra = %(headers)s;
  const started = Date.now();
  const init = { method, credentials: 'include', headers: Object.assign({ accept: 'application/json' }, extra) };
  if (body !== null) {
    if (!init.headers['content-type'] && !init.headers['Content-Type']) {
      init.headers['content-type'] = 'application/json';
    }
    init.body = body;
  }
  let response, text = '', error = null;
  try {
    response = await fetch(url, init);
    text = await response.text();
  } catch (e) {
    error = String(e && e.message ? e.message : e);
  }
  const headers = {};
  if (response) response.headers.forEach((v, k) => { headers[k] = v; });
  let parsed = null;
  try { parsed = JSON.parse(text); } catch (e) { parsed = null; }
  return JSON.stringify({
    request: { method, url, body },
    error,
    status: response ? response.status : 0,
    ok: response ? response.ok : false,
    headers,
    elapsed_ms: Date.now() - started,
    body: parsed,
    body_text: parsed === null ? text.slice(0, 20000) : null
  });
})()
"""


def api(
    destination: Path,
    method: str,
    url: str,
    body: str | None = None,
    *,
    session: str = "",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Capture an API exchange from inside the page, with its session.

    ``headers`` adds request headers beyond ``accept``. This is what makes a
    conditional or ranged request testable — ``Range: bytes=0-99`` returning 206
    versus an unsatisfiable range returning 416 was previously impossible to
    express, because the fetch only ever sent two headers.
    """
    js = _API_JS % {
        "method": json.dumps(method.upper()),
        "url": json.dumps(url),
        "body": json.dumps(body) if body is not None else "null",
        "headers": json.dumps(headers or {}),
    }
    result = evaluate(js, session=session)
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise CaptureError(f"unexpected api capture result: {result!r}")
    if result.get("error"):
        raise CaptureError(f"request failed in page: {result['error']}")

    # Nothing credential-bearing reaches disk: headers, the request body we
    # sent, and the response body are all walked before the file is written.
    result["headers"] = redact(result.get("headers"))
    request = result.get("request") or {}
    if request.get("body"):
        request["body"] = redact_text(str(request["body"]))
    result["body"] = redact_payload(result.get("body"))
    result["body_text"] = redact_text(result.get("body_text"))
    _write_json(destination, result)

    summary = {
        "method": method.upper(),
        "url": url,
        "status": result.get("status", 0),
        "elapsed_ms": result.get("elapsed_ms", 0),
        "keys": sorted(result["body"].keys())[:25] if isinstance(result.get("body"), dict) else [],
    }
    # The prior tool lost a whole run to this: the backend token inside the
    # session cookie expires long before the cookie does, and the symptom
    # disguises itself as empty data rather than an error.
    if result.get("status") == 401:
        summary["warning"] = (
            "401 — the session is probably expired. Re-authenticate in the browser "
            "before treating empty responses as a product defect."
        )
    return summary


_SSE_JS = """
(() => new Promise((resolve) => {
  const url = %(url)s;
  const seconds = %(seconds)s;
  const events = [];
  let ended = 'timeout';
  const source = new EventSource(url, { withCredentials: true });
  const finish = () => {
    try { source.close(); } catch (e) {}
    resolve(JSON.stringify({
      url, seconds, ended,
      count: events.length,
      types: [...new Set(events.map(e => e.type))],
      events: events.slice(0, 50)
    }));
  };
  source.onmessage = (e) => events.push({ type: 'message', data: String(e.data).slice(0, 500) });
  source.onerror = () => { ended = 'error'; finish(); };
  setTimeout(finish, seconds * 1000);
}))()
"""


def sse(
    destination: Path, url: str, seconds: int = SSE_SECONDS, *, session: str = ""
) -> dict[str, Any]:
    """Hold an SSE stream open briefly and record what arrived."""
    js = _SSE_JS % {"url": json.dumps(url), "seconds": int(seconds)}
    result = evaluate(js, timeout=seconds + 60, session=session)
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise CaptureError(f"unexpected sse capture result: {result!r}")
    _write_json(destination, result)
    return {
        "url": url,
        "count": result.get("count", 0),
        "types": result.get("types", []),
        "ended": result.get("ended", ""),
    }


_BLOB_HOOK_JS = """
(() => {
  if (window.__holoqaBlobs) return 'already';
  window.__holoqaBlobs = [];
  const original = URL.createObjectURL.bind(URL);
  URL.createObjectURL = (obj) => {
    try {
      const reader = new FileReader();
      reader.onload = () => window.__holoqaBlobs.push({
        size: obj.size, type: obj.type, data: String(reader.result).split(',')[1] || ''
      });
      reader.readAsDataURL(obj);
    } catch (e) {}
    return original(obj);
  };
  return 'installed';
})()
"""

_BLOB_READ_JS = """
(() => JSON.stringify((window.__holoqaBlobs || []).map(b => ({
  size: b.size, type: b.type, data: b.data
}))))()
"""


def arm_download(*, session: str = "") -> str:
    """Install a blob interceptor before the click that triggers a download.

    Client-side exports are produced as a Blob and clicked through an anchor;
    CDP download interception reports them as ``canceled`` every time. Reading
    the Blob directly is the only path the prior tool found that works.
    """
    return str(evaluate(_BLOB_HOOK_JS, session=session))


def collect_download(destination: Path, *, session: str = "") -> dict[str, Any]:
    """Write the most recent intercepted blob to disk."""
    result = evaluate(_BLOB_READ_JS, session=session)
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, list) or not result:
        raise CaptureError(
            "no download was intercepted. Call arm_download before the click that "
            "starts the export, and allow a few seconds for PDF/PPTX rendering."
        )
    blob = result[-1]
    payload = base64.b64decode(blob.get("data") or "")
    if not payload:
        raise CaptureError("intercepted download was empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return {"bytes": len(payload), "content_type": blob.get("type", "")}


def _write_json(destination: Path, payload: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def settle(milliseconds: int) -> None:
    """Pause before a capture, for a page that animates into its final state."""
    time.sleep(max(0, int(milliseconds)) / 1000.0)


_WAIT_JS = """
(() => {
  try { return Boolean(%(expr)s); } catch (e) { return false; }
})()
"""


def wait_for(expression: str, *, session: str = "", timeout_ms: int = 0) -> dict[str, Any]:
    """Poll a JavaScript condition until it is truthy, then return.

    A plan that says "wait 3-5 seconds before capturing" in its `do:` text is
    stating a timing requirement nothing enforces — which is how a flaky step
    gets recorded as a product defect. This makes the condition machine-checked:
    the capture happens when the page is actually ready, or not at all.

    Raises rather than capturing anyway. A capture taken before the condition
    held is worse than no capture, because it looks like evidence.
    """
    deadline = time.monotonic() + (timeout_ms or 15000) / 1000.0
    js = _WAIT_JS % {"expr": expression}
    last = None
    while True:
        try:
            last = evaluate(js, timeout=30, session=session)
        except CaptureError as error:
            last = f"error: {error}"
        if last is True or str(last).lower() == "true":
            return {"waited_for": expression, "ok": True}
        if time.monotonic() >= deadline:
            raise CaptureError(
                f"wait_for({expression!r}) did not become true within "
                f"{timeout_ms or 15000}ms (last value: {last!r}). The capture was "
                "not taken; a capture before the condition holds is not evidence."
            )
        time.sleep(0.25)
