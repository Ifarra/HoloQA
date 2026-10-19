"""Run on Windows after `uv sync --dev --group desktop` from the repository root."""
from pathlib import Path
import platform
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
    raise SystemExit("Build the x64 Windows sidecar on an x64 Windows host.")
output = root / ".desktop-build"
name = "holoqa-desktop-engine-x86_64-pc-windows-msvc"
subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
    "--name", name, "--distpath", str(root / "desktop/src-tauri/binaries"),
    "--workpath", str(output / "work"), "--specpath", str(output),
    "--collect-submodules", "holoqa", "--collect-submodules", "mcp.server",
    "--collect-data", "holoqa", str(root / "desktop/scripts/engine_entry.py"),
], cwd=root, check=True)
