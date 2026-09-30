"""Tier 3: the assertion kinds a real checklist kept needing.

Each of these was a step the reviewer could only half-test:
  - a ranged download (Range/206/416) could not be expressed at all;
  - an export was verified by "status 200", which an error page also returns;
  - a guard-driven redirect needed a prose "wait 3-5 seconds".
"""

from __future__ import annotations

import json

import pytest

from holoqa import BLOCKED, FAIL, PASS
from holoqa import plan as plan_module
from holoqa import verdict as verdict_module
from holoqa.run import Run
from tests.conftest import api_evidence


def _plan(tmp_path, body: str) -> plan_module.Plan:
    path = tmp_path / "t3.plan.yaml"
    path.write_text(body, encoding="utf-8")
    return plan_module.load(path)


def _api_with_headers(run, step, name, headers, status=200, url="http://x/api"):
    path = run.evidence_dir / f"{name}.json"
    path.write_text(json.dumps({
        "request": {"method": "GET", "url": url, "body": None},
        "status": status, "ok": 200 <= status < 300,
        "headers": headers, "elapsed_ms": 5, "body": {}, "body_text": None,
    }), encoding="utf-8")
    run.attach(step, kind="api", path=path, target=url)
    return path


# ------------------------------------------------------------------- header

def test_header_contains_is_checkable(tmp_path):
    """`Range` returning 206 proves partial content; the header says so."""
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: H1
    title: a ranged request returns partial content
    expect:
      - api: { method: GET, path: /file, status: 206 }
      - header: { name: content-range, contains: bytes 0-99/ }
""")
    run = Run.create(tmp_path / "run", plan)
    _api_with_headers(
        run, "H1", "ranged",
        {"content-range": "bytes 0-99/1000", "content-type": "application/octet-stream"},
        status=206, url="http://x/file",
    )

    outcome, results, _ = verdict_module.evaluate(plan, run, "H1")

    assert outcome == PASS, results


def test_a_missing_header_fails_rather_than_blocks(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: H1
    title: the header must be there
    expect:
      - header: { name: content-range, contains: bytes }
""")
    run = Run.create(tmp_path / "run", plan)
    _api_with_headers(run, "H1", "plain", {"content-type": "text/plain"})

    outcome, results, _ = verdict_module.evaluate(plan, run, "H1")

    assert outcome == FAIL
    assert "content-range" in results[0]["detail"]


def test_header_equals_and_matches(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: H1
    title: exact and regex header checks
    expect:
      - header: { name: x-request-id, matches: "^req-[0-9]+$" }
      - header: { name: content-type, equals: application/json }
""")
    run = Run.create(tmp_path / "run", plan)
    _api_with_headers(
        run, "H1", "h",
        {"x-request-id": "req-4821", "content-type": "application/json"},
    )

    outcome, results, _ = verdict_module.evaluate(plan, run, "H1")

    assert outcome == PASS, results


def test_header_name_alone_asserts_presence(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: H1
    title: presence only
    expect:
      - header: etag
""")
    run = Run.create(tmp_path / "run", plan)
    _api_with_headers(run, "H1", "h", {"etag": '"abc"'})

    outcome, _, _ = verdict_module.evaluate(plan, run, "H1")

    assert outcome == PASS


def test_header_without_an_api_capture_blocks(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: H1
    title: nothing captured
    expect:
      - header: { name: etag }
""")
    run = Run.create(tmp_path / "run", plan)

    outcome, results, _ = verdict_module.evaluate(plan, run, "H1")

    assert outcome == BLOCKED
    assert results[0]["ok"] is None


# --------------------------------------------------------------------- file

def _download(run, step, name, payload: bytes):
    path = run.evidence_dir / name
    path.write_bytes(payload)
    run.attach(step, kind="download", path=path, target="export")
    return path


def test_file_assertion_proves_an_export_is_real(tmp_path):
    """Status 200 alone cannot tell a CSV export from an error page."""
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: F1
    title: the CSV export has content
    expect:
      - file: { name_matches: '\\.csv$', size_gt: 10, contains: "order_id" }
""")
    run = Run.create(tmp_path / "run", plan)
    _download(run, "F1", "export.csv", b"order_id,status\nord_1,PENDING\n")

    outcome, results, _ = verdict_module.evaluate(plan, run, "F1")

    assert outcome == PASS, results


def test_an_error_page_saved_as_a_csv_fails_the_file_assertion(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: F1
    title: the export must be a real CSV
    expect:
      - file: { contains: "order_id" }
""")
    run = Run.create(tmp_path / "run", plan)
    _download(run, "F1", "export.csv", b"<html>500 Internal Server Error</html>")

    outcome, results, _ = verdict_module.evaluate(plan, run, "F1")

    assert outcome == FAIL
    assert "does not contain" in results[0]["detail"]


def test_file_magic_bytes_prove_the_format(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: F1
    title: a PDF download is really a PDF
    expect:
      - file: { magic: "%PDF", size_gt: 8 }
""")
    run = Run.create(tmp_path / "run", plan)
    _download(run, "F1", "report.pdf", b"%PDF-1.7\nrest of the document\n")

    outcome, _, _ = verdict_module.evaluate(plan, run, "F1")

    assert outcome == PASS


def test_a_pdf_assertion_fails_on_an_html_error_page(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: F1
    title: a PDF download is really a PDF
    expect:
      - file: { magic: "%PDF" }
""")
    run = Run.create(tmp_path / "run", plan)
    _download(run, "F1", "report.pdf", b"<html>not a pdf</html>")

    outcome, results, _ = verdict_module.evaluate(plan, run, "F1")

    assert outcome == FAIL
    assert "magic" in results[0]["detail"]


def test_file_assertion_without_a_download_blocks(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t3
steps:
  - id: F1
    title: nothing downloaded
    expect:
      - file: { size_gt: 1 }
""")
    run = Run.create(tmp_path / "run", plan)

    outcome, results, _ = verdict_module.evaluate(plan, run, "F1")

    assert outcome == BLOCKED
    assert results[0]["ok"] is None


# --------------------------------------------------------------- wait / settle

def test_settle_sleeps():
    import time
    from holoqa import observe as observe_module

    started = time.monotonic()
    observe_module.settle(120)
    assert time.monotonic() - started >= 0.1


def test_wait_for_raises_rather_than_capturing_early(monkeypatch):
    """A capture taken before the condition holds is not evidence."""
    import pytest as _pytest
    from holoqa import observe as observe_module

    monkeypatch.setattr(observe_module, "evaluate", lambda *a, **k: False)
    with _pytest.raises(observe_module.CaptureError) as caught:
        observe_module.wait_for("window.ready === true", timeout_ms=300)
    assert "did not become true" in str(caught.value)


def test_wait_for_returns_when_the_condition_holds(monkeypatch):
    from holoqa import observe as observe_module

    monkeypatch.setattr(observe_module, "evaluate", lambda *a, **k: True)
    assert observe_module.wait_for("true", timeout_ms=300)["ok"] is True


def test_headers_argument_is_parsed_strictly():
    from holoqa import mcp as mcp_module
    from holoqa.run import GuardrailError

    assert mcp_module._parse_headers('{"Range": "bytes=0-99"}') == {"Range": "bytes=0-99"}
    assert mcp_module._parse_headers("") == {}
    with pytest.raises(GuardrailError):
        mcp_module._parse_headers("not json")
    with pytest.raises(GuardrailError):
        mcp_module._parse_headers('["a"]')
