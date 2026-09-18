"""`holoqa selftest` must itself be tested.

It went red for several commits without anyone noticing, because nothing in the
suite ran it and its failure line goes to stderr — a `tail` of the output looked
green. The guardrail checker is the thing that certifies the guardrails, so if
it rots silently, every checklist this tool produces quietly stops meaning
anything.
"""

from __future__ import annotations

import pytest

from holoqa.cli import main, selftest


def test_selftest_passes(capsys):
    code = selftest()
    out = capsys.readouterr().out
    assert code == 0, f"selftest is failing:\n{out}"
    assert "guardrails verified" in out


def test_selftest_reports_every_check_it_claims(capsys):
    selftest()
    out = capsys.readouterr().out
    summary = next(line for line in out.splitlines() if "guardrails verified" in line)
    claimed = int(summary.split()[0])
    reported = sum(1 for line in out.splitlines() if line.startswith("ok   "))
    assert claimed == reported, "the summary count disagrees with the printed checks"


def test_selftest_covers_each_numbered_guardrail(capsys):
    """All ten documented guardrails, plus the agent-cannot-pass invariant."""
    selftest()
    out = capsys.readouterr().out
    for number in range(1, 10):
        assert f"guardrail {number}" in out or number == 10, f"guardrail {number} is unchecked"
    assert "plan check" in out, "guardrail 10 (static plan validation) is unchecked"
    assert "agent cannot assert PASS" in out


def test_a_failure_is_visible_on_stdout_not_only_stderr(capsys, monkeypatch):
    """A tail of the output must never look green while the run failed."""
    import holoqa.cli as cli

    def explode(*args, **kwargs):
        raise cli.SelftestFailure("deliberate")

    monkeypatch.setattr(cli, "_expect_refusal", explode)
    code = selftest()
    captured = capsys.readouterr()
    assert code == 1
    assert "FAILED" in captured.out, "failures must reach stdout, not stderr alone"


def test_cli_entry_point_returns_the_selftest_exit_code(capsys):
    assert main(["selftest"]) == 0
