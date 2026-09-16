from pathlib import Path
import subprocess
import os
import sys
import time
import urllib.request

from holoqa.browser_runner import execute_cases


port = 8766


def _wait_for_demo(port: int) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise AssertionError("demo app did not start")


def test_execute_cases_runs_demo_user_creation(tmp_path: Path):
    process = subprocess.Popen(["uv", "run", "holoqa-demo"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("demo app did not start")

        cases = [
            {
                "test_id": "TC-001",
                "title": "Create user",
                "steps": ["Open /demo", "Create user alice@example.com"],
                "expected_result": "User alice@example.com appears",
            }
        ]
        result = execute_cases(cases, "http://127.0.0.1:8765", tmp_path)

        assert result[0], result
        assert result[0]["status"] == "PASS", result
        assert result[0]["evidence"]
        assert (tmp_path / "TC-001" / "final.png").exists()
        assert result[0]["steps"]
        assert result[0]["network"]
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_execute_cases_supports_root_navigation_and_structured_assertions(tmp_path: Path):
    process = subprocess.Popen(
        [sys.executable, "-m", "holoqa.demo_app"],
        env={**os.environ, "PYTHONPATH": "src", "HOLOQA_DEMO_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_demo(port)
        results = execute_cases(
            [
                {"test_id": "TC-ROOT", "steps": ["Open /"], "expected_result": "PASS_IF URL contains /"},
                {"test_id": "TC-PASS", "steps": ["Open /demo"], "expected_result": "PASS_IF TEXT contains Create user"},
                {"test_id": "TC-FAIL", "steps": ["Open /demo"], "expected_result": "PASS_IF TEXT contains definitely-not-present"},
            ],
            f"http://127.0.0.1:{port}",
            tmp_path / "artifacts",
        )
        assert [result["status"] for result in results] == ["PASS", "PASS", "FAIL"]
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_unsupported_browser_action_is_blocked(tmp_path: Path):
    process = subprocess.Popen(
        [sys.executable, "-m", "holoqa.demo_app"],
        env={**os.environ, "PYTHONPATH": "src", "HOLOQA_DEMO_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_demo(port)
        results = execute_cases(
            [{"test_id": "TC-BLOCKED", "steps": ["Click the search icon"], "expected_result": "BLOCKED: search action requires a supported adapter"}],
            f"http://127.0.0.1:{port}",
            tmp_path / "artifacts",
        )
        assert results[0]["status"] == "BLOCKED"
        assert results[0]["actual_result"].startswith("BLOCKED:")
    finally:
        process.terminate()
        process.wait(timeout=10)
