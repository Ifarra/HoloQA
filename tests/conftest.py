from __future__ import annotations

import json
from pathlib import Path

import pytest

from holoqa import plan as plan_module
from holoqa.run import Run

PLAN = """
meta:
  app: fixture
  base_url: http://localhost:9999
known_behaviors:
  - id: KB-001
    title: a known quirk
    applies_to: [T1]
stages:
  - id: A
    title: Alpha
steps:
  - id: T1
    stage: A
    title: Create a thing
    expect:
      - api: { method: POST, path: /things, status: 201 }
      - json: { status: PENDING }
      - capture: { thing_id: $.id }
      - screenshot: required
  - id: T2
    stage: A
    title: Read the thing back
    depends_on: [T1]
    expect:
      - api: { method: GET, path: "/things/{thing_id}", status: 200 }
      - url_contains: /things
      - text_contains: Ready
  - id: T3
    stage: A
    title: Progress moves
    expect:
      - changed: dom
"""


@pytest.fixture()
def plan(tmp_path: Path) -> plan_module.Plan:
    path = tmp_path / "plan.yaml"
    path.write_text(PLAN, encoding="utf-8")
    return plan_module.load(path)


@pytest.fixture()
def run(tmp_path: Path, plan: plan_module.Plan) -> Run:
    return Run.create(tmp_path / ".holoqa" / "runs" / "r1", plan, meta={"tester": "pytest"})


def api_evidence(
    run: Run,
    step: str,
    name: str,
    *,
    method: str = "GET",
    url: str = "http://localhost:9999/things",
    status: int = 200,
    body: dict | None = None,
) -> Path:
    path = run.evidence_dir / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "request": {"method": method, "url": url, "body": None},
                "status": status,
                "ok": 200 <= status < 300,
                "headers": {"content-type": "application/json"},
                "elapsed_ms": 12,
                "body": body,
                "body_text": None,
            }
        ),
        encoding="utf-8",
    )
    run.attach(step, kind="api", path=path, target=url)
    return path


def dom_evidence(
    run: Run,
    step: str,
    name: str,
    *,
    url: str = "http://localhost:9999/things/1",
    text: str = "Ready",
) -> Path:
    path = run.evidence_dir / f"{name}.json"
    path.write_text(
        json.dumps(
            {"url": url, "title": "fixture", "text": text, "text_length": len(text), "truncated": False}
        ),
        encoding="utf-8",
    )
    run.attach(step, kind="dom", path=path, target=url)
    return path


def screenshot_evidence(run: Run, step: str, name: str) -> Path:
    path = run.evidence_dir / f"{name}.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    run.attach(step, kind="screenshot", path=path, target="<page>")
    return path
