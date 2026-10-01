"""`holoqa upgrade` — checking, comparing, and the recovery path.

The command is deliberately thin: it compares the commit uv recorded at install
time with the commit on main, then shells out to `uv tool install`. What is worth
testing is the comparison (a wrong answer means a pointless or missed update) and
the failure message, because the failure it describes — a locked venv on Windows
— leaves the tool broken if the user just retries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from holoqa import cli


OLD = "a" * 40
NEW = "b" * 40


def test_installed_commit_reads_what_uv_recorded(monkeypatch):
    """PEP 610's direct_url.json is the only record of which build is installed."""

    class FakeDistribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            return json.dumps({
                "url": "https://github.com/Ifarra/HoloQA",
                "vcs_info": {"vcs": "git", "commit_id": NEW},
            })

    monkeypatch.setattr(
        "importlib.metadata.distribution", lambda name: FakeDistribution()
    )
    assert cli.installed_commit() == NEW


def test_installed_commit_is_empty_when_metadata_is_missing(monkeypatch):
    def explode(name):
        raise Exception("not installed")

    monkeypatch.setattr("importlib.metadata.distribution", explode)
    assert cli.installed_commit() == ""


def test_installed_commit_is_empty_for_a_local_path_install(monkeypatch):
    """A path install has no vcs_info, and must not look like a git install."""

    class FakeDistribution:
        def read_text(self, name):
            return json.dumps({"url": "file:///C:/code/holoqa"})

    monkeypatch.setattr(
        "importlib.metadata.distribution", lambda name: FakeDistribution()
    )
    assert cli.installed_commit() == ""


def test_remote_commit_prefers_git_ls_remote(monkeypatch):
    """git needs no network quota and no API token, so it goes first."""

    class Done:
        returncode = 0
        stdout = f"{NEW}\trefs/heads/main\n"

    monkeypatch.setattr(cli.shutil, "which", lambda name: "git" if name == "git" else None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Done())

    assert cli.remote_commit() == (NEW, "git ls-remote")


def test_remote_commit_falls_back_to_the_api(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"sha": NEW}).encode()

    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli, "urlopen", lambda *a, **k: Response())

    assert cli.remote_commit() == (NEW, "GitHub API")


def test_remote_commit_reports_unreachable_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    def explode(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(cli, "urlopen", explode)
    assert cli.remote_commit() == ("", "unreachable")


# ------------------------------------------------------------------- the command

@pytest.fixture()
def no_network(monkeypatch):
    """Pin the remote commit so the command is testable offline."""
    monkeypatch.setattr(cli, "remote_commit", lambda *a, **k: (NEW, "test"))


def test_check_reports_an_available_update(no_network, monkeypatch, capsys):
    monkeypatch.setattr(cli, "installed_commit", lambda: OLD)
    assert cli.upgrade(check_only=True) == 1
    out = capsys.readouterr().out
    assert "An update is available" in out
    assert OLD[:12] in out and NEW[:12] in out


def test_check_reports_being_current(no_network, monkeypatch, capsys):
    monkeypatch.setattr(cli, "installed_commit", lambda: NEW)
    assert cli.upgrade(check_only=True) == 0
    assert "Already up to date." in capsys.readouterr().out


def test_unreachable_remote_is_not_an_update(monkeypatch, capsys):
    monkeypatch.setattr(cli, "remote_commit", lambda *a, **k: ("", "unreachable"))
    monkeypatch.setattr(cli, "installed_commit", lambda: OLD)
    assert cli.upgrade(check_only=True) == 2
    assert "unreachable" in capsys.readouterr().out


def test_an_install_with_no_recorded_commit_says_so(no_network, monkeypatch, capsys):
    """Better than comparing against nothing and claiming an update."""
    monkeypatch.setattr(cli, "installed_commit", lambda: "")
    assert cli.upgrade(check_only=True) == 1
    assert "no recorded commit" in capsys.readouterr().out


def test_a_successful_upgrade_tells_the_user_to_restart(no_network, monkeypatch, capsys):
    monkeypatch.setattr(cli, "installed_commit", lambda: OLD)
    monkeypatch.setattr(cli, "_run_upgrade", lambda source, reinstall: (True, "Installed 1 executable: holoqa"))

    assert cli.upgrade() == 0
    out = capsys.readouterr().out
    assert "Restart your MCP client" in out
    assert "selftest" in out


def test_a_failed_upgrade_prescribes_reinstall(no_network, monkeypatch, capsys):
    """The failure that matters: a locked venv left half-replaced.

    A plain retry can report success without repairing the environment, so the
    message has to name `--reinstall` rather than just "try again".
    """
    monkeypatch.setattr(cli, "installed_commit", lambda: OLD)
    monkeypatch.setattr(
        cli, "_run_upgrade",
        lambda source, reinstall: (False, "error: failed to remove directory (os error 145)"),
    )

    assert cli.upgrade() == 2
    out = capsys.readouterr().out
    assert "os error 145" in out
    assert "--reinstall" in out
    assert "Close your MCP client" in out


def test_upgrade_is_wired_into_the_cli():
    from holoqa.cli import main

    with pytest.raises(SystemExit) as caught:
        main(["upgrade", "--help"])
    assert caught.value.code == 0
