"""The three ways an agent could have written its own PASS.

Each test here reproduces one of them against the pre-fix behaviour:

  F-1  write a record straight into ``run.json`` and a file into ``evidence/``
  F-3  edit a capture after it was hashed, then judge on the new bytes
  F-4  rewrite the plan file mid-run, deleting the assertion that was failing

They are the reason the ledger exists. If one of them ever goes green while the
mechanism is reverted, an agent can grade its own release again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from holoqa import BLOCKED, FAIL, PASS
from holoqa import mcp as mcp_module
from holoqa import plan as plan_module
from holoqa import report as report_module
from holoqa import run as run_module
from holoqa import verdict as verdict_module
from holoqa.run import GuardrailError, IntegrityError, Run

PLAN = """
meta:
  app: integrity
steps:
  - id: A1
    title: the orders endpoint returns 200
    expect:
      - api: { method: GET, path: /api/orders, status: 200 }
      - json: { status: OK }
      - screenshot: required
"""


def _plan(tmp_path, body: str = PLAN) -> plan_module.Plan:
    path = tmp_path / "plan.yaml"
    path.write_text(body, encoding="utf-8")
    return plan_module.load(path)


def _api_evidence(run: Run, step: str, name: str, status: int = 200) -> Path:
    path = run.evidence_dir / f"{name}.json"
    path.write_text(json.dumps({
        "request": {"method": "GET", "url": "http://x/api/orders", "body": None},
        "status": status, "ok": status < 400, "headers": {},
        "elapsed_ms": 1, "body": {"status": "OK"}, "body_text": None,
    }), encoding="utf-8")
    run.attach(step, kind="api", path=path, target="http://x/api/orders")
    return path


def _screenshot(run: Run, step: str, name: str = "shot") -> Path:
    path = run.evidence_dir / f"{name}.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"bytes" * 20)
    run.attach(step, kind="screenshot", path=path, target="<page>")
    return path


@pytest.fixture()
def run(tmp_path) -> Run:
    created = Run.create(tmp_path / "run", _plan(tmp_path))
    yield created
    run_module.forget_ledger(created.dir)


def _judge(run: Run, plan: plan_module.Plan, step: str = "A1"):
    return verdict_module.evaluate(plan, run, step)


# ------------------------------------------------------------------- F-4 pin

def test_the_plan_is_pinned_into_the_run_directory(run, tmp_path):
    """Judging applies the reviewed contract, not whatever the file says now."""
    assert run.plan_pin.is_file()
    assert run.plan_pin.read_text(encoding="utf-8") == (
        tmp_path / "plan.yaml"
    ).read_text(encoding="utf-8")


def test_editing_the_plan_mid_run_cannot_change_the_verdict(tmp_path):
    """This was how a FAIL became a PASS with no new evidence at all."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    try:
        _api_evidence(run, "A1", "orders", status=503)
        _screenshot(run, "A1")
        outcome, _, _ = _judge(run, plan)
        assert outcome == FAIL, "a 503 on the required endpoint is a defect"

        # The agent deletes the assertions it is failing.
        (tmp_path / "plan.yaml").write_text(
            "meta:\n  app: integrity\nsteps:\n  - id: A1\n    title: t\n"
            "    expect:\n      - screenshot: required\n",
            encoding="utf-8",
        )

        with pytest.raises(IntegrityError) as caught:
            run.plan_for_judging()
        assert "has changed since this run started" in str(caught.value)
    finally:
        run_module.forget_ledger(run.dir)


def test_a_swapped_pinned_plan_is_detected(tmp_path):
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    try:
        run.plan_pin.write_text("meta: { app: integrity }\nsteps: []\n", encoding="utf-8")
        with pytest.raises(IntegrityError) as caught:
            run.plan_for_judging()
        assert "pinned plan has been changed" in str(caught.value)
    finally:
        run_module.forget_ledger(run.dir)


def test_a_run_without_a_pinned_plan_refuses_to_be_judged(tmp_path):
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    try:
        run.plan_pin.unlink()
        with pytest.raises(GuardrailError) as caught:
            run.plan_for_judging()
        assert "no pinned plan" in str(caught.value)
    finally:
        run_module.forget_ledger(run.dir)


# --------------------------------------------------------------- F-1 forgery

def test_a_hand_written_record_is_not_evidence(tmp_path, monkeypatch):
    """The whole attack, in one test: no `holoqa_observe`, only file writes."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))
    try:
        # The agent authors the bytes itself.
        forged = run.evidence_dir / "step-a1-api-orders.json"
        forged.write_text(json.dumps({
            "request": {"method": "GET", "url": "http://x/api/orders", "body": None},
            "status": 200, "ok": True, "headers": {},
            "elapsed_ms": 1, "body": {"status": "OK"}, "body_text": None,
        }), encoding="utf-8")
        shot = run.evidence_dir / "step-a1-screenshot.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not a real screenshot" * 5)

        # ... and appends its own index records to run.json.
        data = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
        for kind, name in (("api", forged.name), ("screenshot", shot.name)):
            data["steps"]["A1"]["observations"].append({
                "kind": kind, "file": name, "target": "x", "actor": "default",
                "bytes": (run.evidence_dir / name).stat().st_size,
                "sha256": "0" * 64, "captured_at": "2026-01-01T00:00:00+00:00",
                "summary": {},
            })
        (run.dir / "run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

        outcome, results, _ = _judge(run, plan)

        assert outcome == BLOCKED, (
            "a record this process never made must not produce a verdict"
        )
        assert all(item["ok"] is None for item in results)
        assert run.integrity()["status"] == "tampered"
    finally:
        run_module.forget_ledger(run.dir)


def test_a_forgery_with_a_correct_hash_is_still_not_evidence(tmp_path, monkeypatch):
    """The discriminating case: the forger computes the hash of their own bytes.

    A hash check alone cannot catch this — the record agrees with the file. What
    catches it is that this process never made that capture, which is the ledger's
    job. Without the ownership check this test passes a forged PASS.
    """
    import hashlib

    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))
    try:
        forged = run.evidence_dir / "step-a1-api-orders.json"
        payload = json.dumps({
            "request": {"method": "GET", "url": "http://x/api/orders", "body": None},
            "status": 200, "ok": True, "headers": {}, "elapsed_ms": 1,
            "body": {"status": "OK"}, "body_text": None,
        })
        forged.write_text(payload, encoding="utf-8")
        shot = run.evidence_dir / "step-a1-screenshot.png"
        shot_payload = b"\x89PNG\r\n\x1a\n" + b"a convincing screenshot" * 5
        shot.write_bytes(shot_payload)

        data = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
        for kind, path, blob in (
            ("api", forged, payload.encode()),
            ("screenshot", shot, shot_payload),
        ):
            data["steps"]["A1"]["observations"].append({
                "kind": kind, "file": path.name, "target": "x", "actor": "default",
                "bytes": path.stat().st_size,
                # The real hash, computed by the forger.
                "sha256": hashlib.sha256(blob).hexdigest(),
                "captured_at": "2026-01-01T00:00:00+00:00", "summary": {},
            })
        (run.dir / "run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

        # The hashes agree with the bytes, so only ownership can catch this.
        assert run.verify_evidence() == [], "the hash check cannot see this one"
        judged = mcp_module.holoqa_judge("A1")

        assert judged["verdict"] == BLOCKED, (
            "a self-consistent forgery must still not be evidence"
        )
        # "unverified" rather than "tampered" is the honest label here: the agent
        # never called holoqa_observe, so this process never took a ledger and
        # cannot prove the records were planted. It still refuses to judge them.
        assert judged["integrity"] in {"tampered", "unverified"}, judged
    finally:
        run_module.forget_ledger(run.dir)


def test_forged_evidence_through_the_mcp_tools_is_blocked(tmp_path, monkeypatch):
    """Same attack through the real tool entry points, which is how it is used."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))
    try:
        forged = run.evidence_dir / "step-a1-api-orders.json"
        forged.write_text(json.dumps({
            "request": {"method": "GET", "url": "http://x/api/orders"},
            "status": 200, "ok": True, "headers": {}, "elapsed_ms": 1,
            "body": {"status": "OK"}, "body_text": None,
        }), encoding="utf-8")
        data = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
        data["steps"]["A1"]["observations"].append({
            "kind": "api", "file": forged.name, "target": "x", "actor": "default",
            "bytes": forged.stat().st_size, "sha256": "f" * 64,
            "captured_at": "2026-01-01T00:00:00+00:00", "summary": {},
        })
        (run.dir / "run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

        judged = mcp_module.holoqa_judge("A1")

        assert judged["verdict"] == BLOCKED, judged
        assert judged["integrity"] == "tampered"
        # Packaging refuses outright: a record whose hash matches nothing is not
        # a run to be decided, it is a run to be investigated.
        with pytest.raises(report_module.PackageError) as caught:
            mcp_module.holoqa_run_package(strict=False)
        assert "does not match its recorded hash" in str(caught.value)
    finally:
        run_module.forget_ledger(run.dir)


def test_a_real_capture_still_passes(run):
    """The guard must not block the honest path."""
    _api_evidence(run, "A1", "orders", status=200)
    _screenshot(run, "A1")
    outcome, results, _ = _judge(run, _plan(run.dir.parent))
    assert outcome == PASS, results
    assert run.integrity()["status"] == "verified"


def test_editing_a_verdict_in_run_json_is_ignored(tmp_path, monkeypatch):
    """The shortest forgery: no evidence games, just write the answer.

    `run.json` is a projection, not the record. Once this process holds a ledger,
    an edited verdict in the file never reaches a decision — otherwise a run whose
    evidence says FAIL packages as RELEASE with `integrity: verified`.
    """
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    monkeypatch.setenv("HOLOQA_RUN_DIR", str(run.dir))
    try:
        _api_evidence(run, "A1", "orders", status=500)
        _screenshot(run, "A1")
        assert mcp_module.holoqa_judge("A1")["verdict"] == FAIL

        # The agent rewrites the verdict and the note, leaving the evidence alone.
        data = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
        data["steps"]["A1"]["verdict"] = PASS
        data["steps"]["A1"]["verdict_note"] = ""
        (run.dir / "run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

        assert run.read()["steps"]["A1"]["verdict"] == FAIL, (
            "the file must not override the ledger"
        )
        packaged = mcp_module.holoqa_run_package(strict=False)
        assert packaged["decision"] == "HOLD", "a hand-edited verdict must not release"
        assert packaged["counts"][PASS] == 0
    finally:
        run_module.forget_ledger(run.dir)


# ----------------------------------------------------------------- F-3 hashes

def test_editing_a_capture_after_the_fact_is_detected(run):
    """The hash was recorded and never checked; it is checked now."""
    path = _api_evidence(run, "A1", "orders", status=503)
    _screenshot(run, "A1")
    plan = _plan(run.dir.parent)

    # The capture as recorded is genuine, so it is judged on its merits.
    outcome, _, _ = _judge(run, plan)
    assert outcome == FAIL, "a genuine 503 is a defect, not a tamper"

    # Now the bytes are swapped for a healthy-looking capture.
    path.write_text(json.dumps({
        "request": {"method": "GET", "url": "http://x/api/orders"},
        "status": 200, "ok": True, "headers": {}, "elapsed_ms": 1,
        "body": {"status": "OK"}, "body_text": None,
    }), encoding="utf-8")
    assert run.verify_evidence(), "the mismatch must be reported"
    with pytest.raises(IntegrityError) as caught:
        _judge(run, plan)
    assert "does not match the hash" in str(caught.value)


def test_a_deleted_capture_is_reported(run):
    _api_evidence(run, "A1", "orders")
    (run.evidence_dir / "orders.json").unlink()
    problems = run.verify_evidence()
    assert any("missing" in item for item in problems), problems


# ------------------------------------------------------- unverified and attest

def test_a_run_without_a_ledger_is_unverified_not_trusted(tmp_path):
    """A fresh process cannot tell a genuine run.json from a forged one."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    _api_evidence(run, "A1", "orders")
    _screenshot(run, "A1")
    verdict_module.evaluate(plan, run, "A1")
    run.set_verdict("A1", PASS, note="", assertions=[], by="holoqa")

    # A different process, with no memory of making those captures.
    run_module.forget_ledger(run.dir)
    fresh = Run(run.dir)

    assert fresh.integrity()["status"] == "unverified"
    checked = report_module.validate(fresh)
    assert checked["ok"], checked["problems"]
    packaged = report_module.package(fresh)
    assert packaged["decision"] == "HOLD", (
        "an unverifiable run must not decide a release"
    )
    assert packaged["integrity"] == "unverified"
    report = (fresh.out_dir / "report.md").read_text(encoding="utf-8")
    assert "HOLD (unverified)" in report
    assert "holoqa attest" in report


def test_attesting_records_that_a_human_reviewed_it(tmp_path):
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    _api_evidence(run, "A1", "orders")
    _screenshot(run, "A1")
    verdict_module.evaluate(plan, run, "A1")
    run.set_verdict("A1", PASS, note="", assertions=[], by="holoqa")
    run_module.forget_ledger(run.dir)
    fresh = Run(run.dir)
    assert fresh.integrity()["status"] == "unverified"

    with pytest.raises(GuardrailError):
        fresh.attest(by="Fauzan", reason="ok")
    fresh.attest(by="Fauzan", reason="read all 3 evidence files by hand")

    assert fresh.integrity()["status"] == "attested"
    packaged = report_module.package(fresh)
    assert packaged["decision"] == "RELEASE"
    assert packaged["integrity"] == "attested"


def test_attest_works_on_a_run_this_process_never_created(tmp_path):
    """The upgrade path: a run from an older HoloQA has no ledger to adopt.

    `attest` is the only way a human can accept such a run, so it has to work
    with no ledger present at all — otherwise the upgrade strands every existing
    run as permanently unverifiable.
    """
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    _api_evidence(run, "A1", "orders")
    _screenshot(run, "A1")
    verdict_module.evaluate(plan, run, "A1")
    run.set_verdict("A1", PASS, note="", assertions=[], by="holoqa")
    run_module.forget_ledger(run.dir)

    # A run with no pinned plan, the way an older HoloQA left it.
    run.plan_pin.unlink()
    fresh = Run(run.dir)
    assert fresh.integrity()["status"] == "unverified"

    fresh.attest(by="Fauzan", reason="read the evidence by hand before upgrading")
    assert fresh.integrity()["status"] == "attested"

    # And it still refuses to be judged, because there is no pinned contract.
    with pytest.raises(GuardrailError) as caught:
        fresh.plan_for_judging()
    assert "no pinned plan" in str(caught.value)

    # But it can be packaged and decided, because a human vouched for it.
    packaged = report_module.package(fresh)
    assert packaged["decision"] == "RELEASE"
    assert packaged["integrity"] == "attested"


def test_tampering_is_reported_even_without_a_ledger(tmp_path):
    """The hash check needs no ledger, so it is the one that always works."""
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    _api_evidence(run, "A1", "orders")
    run_module.forget_ledger(run.dir)
    fresh = Run(run.dir)
    (fresh.evidence_dir / "orders.json").write_text("{}", encoding="utf-8")

    assert fresh.integrity()["status"] == "tampered"
    assert not report_module.validate(fresh)["ok"]


def test_packaging_refuses_evidence_that_does_not_match(run):
    _api_evidence(run, "A1", "orders")
    _screenshot(run, "A1")
    verdict_module.evaluate(_plan(run.dir.parent), run, "A1")
    run.set_verdict("A1", PASS, note="", assertions=[], by="holoqa")
    (run.evidence_dir / "orders.json").write_text("{}", encoding="utf-8")

    with pytest.raises(report_module.PackageError) as caught:
        report_module.package(run)
    assert "does not match its recorded hash" in str(caught.value)


# -------------------------------------------------------------- capture kinds

def test_a_second_process_writing_a_capture_is_seen(tmp_path):
    """The wrapper's server captures while the wrapper watches: not a forgery.

    Ownership is per capture, not per run, so a file written by another HoloQA
    process is picked up rather than mistaken for a hand-edit.
    """
    plan = _plan(tmp_path)
    run = Run.create(tmp_path / "run", plan)
    _api_evidence(run, "A1", "orders")
    # Simulate the other process's write by clearing the local ledger and
    # letting a fresh Run attach the next capture.
    run_module.forget_ledger(run.dir)
    other = Run(run.dir)
    _screenshot(other, "A1")

    # The earlier capture came from a process this one cannot vouch for, so the
    # run is unverifiable — but it is not *tampered*, because that file was
    # already in the index when this process took over. Treating a hand-off as
    # forgery would break every resumed run.
    assert other.integrity()["status"] == "unverified"
    assert other.verify_evidence() == []


def test_missing_evidence_file_still_blocks_rather_than_crashes(run):
    _api_evidence(run, "A1", "orders")
    (run.evidence_dir / "orders.json").unlink()
    outcome, results, _ = _judge(run, _plan(run.dir.parent))
    assert outcome == BLOCKED
