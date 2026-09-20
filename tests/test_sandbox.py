from __future__ import annotations

from urllib.request import urlopen

from holoqa.cli import _tui_mode, main
from holoqa.sandbox import TuiSandbox


def test_tui_sandbox_is_local_git_workspace_and_serves_fixture() -> None:
    sandbox = TuiSandbox.create()
    try:
        assert sandbox.root.name.startswith("holoqa-tui-sandbox-")
        assert sandbox.plan.is_file()
        assert (sandbox.root / ".git").is_dir()
        with urlopen(sandbox.base_url + "/", timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "Fixture ready" in body
    finally:
        sandbox.cleanup()


def test_demo_and_sandbox_flags_are_explicitly_exclusive() -> None:
    assert main(["tui", "--demo", "--sandbox"]) == 2


def test_sandbox_tui_defaults_to_noninteractive_provider_mode() -> None:
    assert _tui_mode("", True) == "unattended"
    assert _tui_mode("", False) == "supervised"
    assert _tui_mode("supervised", True) == "supervised"
