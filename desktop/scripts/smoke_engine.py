"""Executable-level smoke: UTF-8 stdio, read-only project discovery, clean exit."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="holoqa-desktop-") as temp:
    root = Path(temp)
    project = root / "project with spaces"
    project.mkdir()
    process = subprocess.run([sys.argv[1], "--state-dir", str(root / "state")],
        input="\n".join(json.dumps(item) for item in [
            {"id": 1, "method": "hello"},
            {"id": 2, "method": "project.open", "params": {"path": str(project)}},
        ]) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert process.returncode == 0, "Sidecar failed to exit cleanly"
    messages = [json.loads(line) for line in process.stdout.splitlines()]
    by_id = {message["id"]: message for message in messages if "id" in message}
    assert by_id[1]["result"]["protocol"] == 1
    assert by_id[2]["result"]["name"] == project.name
    assert not list(project.iterdir()), "Discovery must not write to the project"
    print("Packaged Windows engine smoke passed")
