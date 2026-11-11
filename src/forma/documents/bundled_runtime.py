"""Discover embedded workers and launch them with their own native-library search path."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

_SPAWN_LOCK = threading.Lock()
_EXECUTABLES = {"ocr": "ocr/forma-ocr.exe", "babeldoc": "babeldoc/babeldoc.exe"}


def runtime_root() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    for base in (getattr(sys, "_MEIPASS", ""), str(Path(sys.executable).parent)):
        if base and (Path(base) / "runtimes").is_dir():
            return Path(base) / "runtimes"
    return None


def find_bundled(component: str) -> str:
    root = runtime_root()
    if root is None or component not in _EXECUTABLES:
        return ""
    candidate = root / _EXECUTABLES[component]
    return str(candidate) if candidate.is_file() else ""


def worker_environment(executable: str, environment: dict[str, str]) -> dict[str, str]:
    result = dict(environment)
    root = runtime_root()
    if root is None:
        return result
    if Path(executable).resolve() not in {
        (root / relative).resolve() for relative in _EXECUTABLES.values()
    }:
        return result
    result["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if Path(executable).resolve() == (root / _EXECUTABLES["ocr"]).resolve():
        result.update({
            "DOCLING_ARTIFACTS_PATH": str(root / "docling-models"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TESSDATA_PREFIX": str(root / "tesseract/tessdata"),
            "PATH": str(root / "tesseract") + os.pathsep + result.get("PATH", ""),
        })
    return result


def start_worker(arguments, **kwargs):
    # PyInstaller's SetDllDirectory applies to child processes too. Restore the
    # main app's search path immediately after CreateProcess, including on error.
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes

        with _SPAWN_LOCK:
            ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                return subprocess.Popen(arguments, **kwargs)
            finally:
                ctypes.windll.kernel32.SetDllDirectoryW(getattr(sys, "_MEIPASS", None))
    return subprocess.Popen(arguments, **kwargs)
