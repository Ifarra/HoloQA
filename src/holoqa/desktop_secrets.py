"""Per-user, per-project environment values for the Windows desktop app.

Windows release builds protect the store with DPAPI. Development platforms use
an owner-only data file so the desktop remains usable without a system vault.
The renderer can list names and set/clear values, but never reads saved values.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        return data

    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination))
    else:
        label = ctypes.create_unicode_buffer("HoloQA project environment")
        ok = crypt.CryptProtectData(ctypes.byref(source), label, None, None, None, 1, ctypes.byref(destination))
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not protect the project environment")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel.LocalFree(destination.pbData)


class ProjectEnvironment:
    def __init__(self, state_dir: Path):
        self.directory = Path(state_dir) / "project-environments"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, project: Path) -> Path:
        key = hashlib.sha256(str(Path(project).resolve()).encode("utf-8")).hexdigest()
        return self.directory / f"{key}.json"

    def load(self, project: Path) -> dict[str, str]:
        path = self._path(project)
        try:
            encoded = json.loads(path.read_text("ascii"))
            content = _dpapi(base64.b64decode(encoded["data"], validate=True), decrypt=True)
            values = json.loads(content.decode("utf-8"))
            if not isinstance(values, dict) or any(not _NAME.fullmatch(k) or not isinstance(v, str) for k, v in values.items()):
                raise ValueError("invalid environment store")
            return values
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
            raise ValueError("Saved project environment could not be read. Remove and re-enter its values.") from error

    def save(self, project: Path, values: dict[str, str]) -> None:
        content = json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
        payload = json.dumps({"data": base64.b64encode(_dpapi(content, decrypt=False)).decode("ascii")}, separators=(",", ":"))
        path = self._path(project)
        fd, name = tempfile.mkstemp(prefix=".environment-", dir=self.directory)
        try:
            if os.name != "nt":
                os.chmod(name, 0o600)
            with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def names(self, project: Path) -> list[str]:
        return sorted(self.load(project))

    @staticmethod
    def validate(name: str, value: str) -> None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Environment variable names must use uppercase letters, digits, and underscores, and start with a letter or underscore.")
        if not isinstance(value, str) or len(value) > 16_384 or "\x00" in value:
            raise ValueError("Environment values must be text of 16,384 characters or less, without NUL characters.")

    def digest(self, project: Path) -> str:
        encoded = json.dumps(self.load(project), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
