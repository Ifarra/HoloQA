from pathlib import Path
import subprocess
import socket
import time
import urllib.request
import os

from openpyxl import Workbook

from holoqa.browser_runner import execute_cases


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Test ID", "Title", "Steps", "Expected Result"])
    sheet.append(["TC-001", "Create valid user", "Open /demo; Create user valid@example.com", "User created successfully"])
    sheet.append(["TC-002", "Reject invalid email", "Open /demo; Create user invalid-email", "Invalid email"])
    sheet.append(["TC-003", "Prevent duplicate user", "Open /demo; Create user duplicate@example.com; Create user duplicate@example.com", "User already exists"])
    workbook.save(path)


def test_demo_workbook_has_three_real_browser_scenarios(tmp_path: Path):
    source = tmp_path / "demo_cases.xlsx"
    _workbook(source)
    from holoqa.workbook import import_workbook

    cases = import_workbook(source)
    assert len(cases) == 3
    assert cases[2]["steps"][-1].endswith("duplicate@example.com")


def test_demo_workbook_executes_real_pass_scenarios(tmp_path: Path):
    source = tmp_path / "demo_cases.xlsx"
    _workbook(source)
    from holoqa.workbook import import_workbook

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment["HOLOQA_DEMO_PORT"] = str(port)
    process = subprocess.Popen(["uv", "run", "holoqa-demo"], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("demo app did not start")
        results = execute_cases(import_workbook(source), f"http://127.0.0.1:{port}", tmp_path / "artifacts")
        assert [result["status"] for result in results] == ["PASS", "PASS", "PASS"], results
        assert all(result["network"] for result in results)
        assert all(result["steps"] for result in results)
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_demo_failure_mode_produces_fail_with_server_evidence(tmp_path: Path):
    port = 0
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment["HOLOQA_DEMO_PORT"] = str(port)
    environment["HOLOQA_DEMO_FAILURE"] = "1"
    process = subprocess.Popen(["uv", "run", "holoqa-demo"], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("demo app did not start")
        results = execute_cases([{"test_id": "TC-004", "steps": ["Create user failure@example.com"], "expected_result": "Simulated server failure"}], f"http://127.0.0.1:{port}", tmp_path / "artifacts")
        assert results[0]["status"] == "FAIL", results
        assert results[0]["steps"][0]["status"] == "FAIL", results
        assert any(response["status"] == 500 for response in results[0]["network"]), results
    finally:
        process.terminate()
        process.wait(timeout=10)
