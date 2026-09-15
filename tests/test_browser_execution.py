from pathlib import Path
import subprocess
import time
import urllib.request

from holoqa.browser_runner import execute_cases


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
    finally:
        process.terminate()
        process.wait(timeout=10)
