"""Tier 5: fields that existed only as prose, and the docs that misled.

The reviewer marked 82 steps with a `# UJI LEMAH` comment, set browser flags
through an environment variable, and hit a README install command pointing at
`github.com/USER/holoqa`. None of those is a crash; all of them are friction
that a checklist tool should not add to the work of writing a checklist.
"""

from __future__ import annotations

import pytest

from holoqa import PASS
from holoqa import observe as observe_module
from holoqa import plan as plan_module
from holoqa import report as report_module
from holoqa.run import Run


def _plan(tmp_path, body: str) -> plan_module.Plan:
    path = tmp_path / "t5.plan.yaml"
    path.write_text(body, encoding="utf-8")
    return plan_module.load(path)


# ----------------------------------------------------------------- strength

def test_weak_strength_is_a_field_not_a_comment(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t5
steps:
  - id: W1
    title: a PDF the human must read
    strength: weak
    weak_reason: the PDF has to be read by a person
    expect:
      - screenshot: required
  - id: W2
    title: a normal step
    expect:
      - screenshot: required
""")
    assert plan.step("W1").strength == "weak"
    assert plan.step("W1").weak_reason == "the PDF has to be read by a person"
    assert plan.step("W2").strength == "normal"


def test_unknown_strength_is_refused(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        _plan(tmp_path, """
meta:
  app: t5
steps:
  - id: W1
    title: x
    strength: medium
    expect:
      - screenshot: required
""")
    assert "strength" in str(caught.value)


def test_lint_warns_about_weak_steps_and_a_missing_reason(tmp_path):
    path = tmp_path / "warn.plan.yaml"
    path.write_text("""
meta:
  app: t5
steps:
  - id: W1
    title: weak with no reason
    strength: weak
    expect:
      - screenshot: required
""", encoding="utf-8")
    result = plan_module.lint(path)
    assert any("weak_reason" in w for w in result["warnings"])
    assert any("strength: weak" in w for w in result["warnings"])


def test_the_report_counts_how_many_passes_are_weak(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t5
steps:
  - id: W1
    title: a weak step
    strength: weak
    weak_reason: needs a human
    expect:
      - screenshot: required
  - id: W2
    title: a strong step
    expect:
      - screenshot: required
""")
    run = Run.create(tmp_path / "run", plan)
    for step_id in ("W1", "W2"):
        shot = run.evidence_dir / f"{step_id}.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + step_id.encode())
        run.attach(step_id, kind="screenshot", path=shot)
        run.set_verdict(step_id, PASS, assertions=[], by="holoqa")

    text = report_module.markdown(run).read_text(encoding="utf-8")

    # "2 passed" alone would overstate what was proven.
    assert "1 of 2 passes are marked weak" in text


# ------------------------------------------------------------------ browser

def test_browser_options_are_declared_in_the_plan(tmp_path):
    plan = _plan(tmp_path, """
meta:
  app: t5
  browser:
    ignore_https_errors: true
    args: ["--use-fake-device-for-media-stream"]
steps:
  - id: B1
    title: staging has a self-signed certificate
    expect:
      - screenshot: required
""")
    assert plan.meta.browser.ignore_https_errors is True
    assert plan.meta.browser.args == ["--use-fake-device-for-media-stream"]


def test_browser_options_reach_the_cli_environment(monkeypatch):
    seen: dict[str, str] = {}

    class Done:
        returncode = 0

    def fake_run(command, **kwargs):
        seen.update(kwargs.get("env") or {})
        return Done()

    monkeypatch.setattr(observe_module.subprocess, "run", fake_run)
    monkeypatch.setattr(observe_module, "_binary", lambda: "agent-browser")
    observe_module.set_browser_options(True, ["--use-fake-device-for-media-stream"])
    try:
        observe_module.run_cli(["eval", "1"])
    finally:
        observe_module.set_browser_options()

    assert "--ignore-https-errors" in seen["AGENT_BROWSER_ARGS"]
    assert "--use-fake-device-for-media-stream" in seen["AGENT_BROWSER_ARGS"]


def test_browser_options_do_not_leak_between_runs(monkeypatch):
    observe_module.set_browser_options(True, ["--a"])
    observe_module.set_browser_options()
    assert observe_module._browser_args() == []


# ------------------------------------------------------------------- format

def test_the_format_reference_documents_the_new_surface():
    reference = plan_module.FORMAT_REFERENCE
    assert "header" in reference["assertions"]
    assert "file" in reference["assertions"]
    assert "actors" in reference
    # An agent writing a plan reads this, so every newer field must be visible.
    assert "scope" in reference["scoping"]
    assert "requires" in reference["requires"]
    assert "weak" in reference["strength"]
