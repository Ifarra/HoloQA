"""Multi-actor plans: one plan, several browser identities.

The bug these close is the one the reviewer hit in a real run: an anonymous
step returned the admin session's DOM with an identical hash, because every
capture shared one cookie jar. A step that tests ownership captured as the
wrong identity is not a weaker test — it is no test at all, and it looks like
perfectly good evidence.
"""

from __future__ import annotations

import json

import pytest

from holoqa import BLOCKED, PASS
from holoqa import plan as plan_module
from holoqa import verdict as verdict_module
from holoqa.run import GuardrailError, Run
from tests.conftest import api_evidence, dom_evidence


MULTI_ACTOR_PLAN = """
meta:
  app: multi
  actors:
    admin:
      as: admin@example.test
      password: ${ADMIN_PASSWORD}
    proctor:
      as: proctor@example.test
      password: ${PROCTOR_PASSWORD}
    anon: {}
steps:
  - id: A1
    title: The admin sees the admin console
    actor: admin
    expect:
      - text_contains: Admin console
  - id: B1
    title: A proctor cannot see another proctor's session
    actor: proctor
    expect:
      - text_contains: My sessions
  - id: C1
    title: An anonymous visitor is redirected
    actor: anon
    expect:
      - url_contains: /login
  - id: D1
    title: No actor named, so the first declared actor applies
    expect:
      - text_contains: Admin console
"""


@pytest.fixture()
def plan(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("PROCTOR_PASSWORD", "proctor-secret")
    path = tmp_path / "multi.plan.yaml"
    path.write_text(MULTI_ACTOR_PLAN, encoding="utf-8")
    return plan_module.load(path)


def test_actor_for_resolves_step_and_default(plan):
    assert plan.actor_for("A1") == "admin"
    assert plan.actor_for("B1") == "proctor"
    assert plan.actor_for("C1") == "anon"
    # The first declared actor is the default, so a plan can declare identities
    # without tagging every single step.
    assert plan.actor_for("D1") == "admin"


def test_actor_credentials_expand_from_the_environment(plan, monkeypatch):
    assert plan.actor("admin").password == "admin-secret"
    assert plan.actor("proctor").password == "proctor-secret"
    assert plan.actor("anon").anonymous


def test_a_plan_with_no_actors_is_unchanged(tmp_path):
    """Every existing single-actor plan must keep working, with no edits."""
    path = tmp_path / "single.plan.yaml"
    path.write_text("""
meta:
  app: single
steps:
  - id: A1
    title: one identity
    expect:
      - screenshot: required
""", encoding="utf-8")
    plan = plan_module.load(path)
    assert plan.default_actor == "default"
    assert plan.actor_for("A1") == "default"


def test_unknown_actor_is_refused_at_load(tmp_path):
    with pytest.raises(plan_module.PlanError) as caught:
        path = tmp_path / "bad.plan.yaml"
        path.write_text("""
meta:
  app: bad
  actors:
    admin: {as: a@b.test, password: "${P}"}
steps:
  - id: A1
    title: typo in the actor name
    actor: amdin
    expect:
      - screenshot: required
""", encoding="utf-8")
        plan_module.load(path)
    assert "actor" in str(caught.value)


def test_a_literal_password_in_a_plan_is_refused(tmp_path):
    """A plan is committed beside the code, so a literal secret is a leaked one."""
    with pytest.raises(plan_module.PlanError) as caught:
        path = tmp_path / "leak.plan.yaml"
        path.write_text("""
meta:
  app: leak
  actors:
    admin: {as: a@b.test, password: hunter2}
steps:
  - id: A1
    title: x
    expect:
      - screenshot: required
""", encoding="utf-8")
        plan_module.load(path)
    assert "literal password" in str(caught.value)


# ------------------------------------------------------- wrong-session guard

def test_evidence_from_the_wrong_actor_blocks_the_step(plan, tmp_path):
    """The reviewer's bug: an anon step judged on the admin session's capture."""
    run = Run.create(tmp_path / "run", plan)
    path = run.evidence_dir / "admin-dom.json"
    path.write_text(json.dumps({
        "url": "http://x/admin", "title": "t", "text": "Admin console",
        "text_length": 13, "truncated": False,
    }), encoding="utf-8")
    # Captured as `admin`, but C1 runs as `anon` — the content would otherwise
    # satisfy no assertion, yet the wrong identity is the real problem.
    run.attach("C1", kind="dom", path=path, actor="admin")

    outcome, results, _ = verdict_module.evaluate(plan, run, "C1")

    assert outcome == BLOCKED
    assert results[0]["kind"] == "actor"
    assert "anon" in results[0]["detail"]


def test_evidence_from_the_matching_actor_is_judged_normally(plan, tmp_path):
    run = Run.create(tmp_path / "run", plan)
    path = run.evidence_dir / "admin-dom.json"
    path.write_text(json.dumps({
        "url": "http://x/admin", "title": "t", "text": "Admin console",
        "text_length": 13, "truncated": False,
    }), encoding="utf-8")
    run.attach("A1", kind="dom", path=path, actor="admin")

    outcome, _, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == PASS


def test_untagged_evidence_is_not_treated_as_foreign(plan, tmp_path):
    """A capture with no actor recorded must not start blocking old runs."""
    run = Run.create(tmp_path / "run", plan)
    dom_evidence(run, "A1", "untagged", text="Admin console")

    outcome, _, _ = verdict_module.evaluate(plan, run, "A1")

    assert outcome == PASS


# --------------------------------------------------------------- mcp surface

def test_observe_refuses_an_actor_the_plan_did_not_assign(plan, tmp_path, monkeypatch):
    """The agent cannot choose the identity; the plan decides it."""
    from holoqa import mcp as mcp_module

    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))

    with pytest.raises(GuardrailError) as caught:
        mcp_module.holoqa_observe("C1", "dom", actor="admin")
    assert "runs as actor" in str(caught.value)


def test_run_status_reports_the_next_actor_without_a_password(plan, tmp_path, monkeypatch):
    from holoqa import mcp as mcp_module

    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))

    status = mcp_module.holoqa_run_status()
    assert status["next"]["actor"] == "admin"
    assert status["next"]["actor_login"] == "admin@example.test"
    assert "admin-secret" not in json.dumps(status)
    assert sorted(status["actors"]) == ["admin", "anon", "proctor"]
