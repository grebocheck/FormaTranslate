"""A second frozen worker must not leak its DLL search path to Tesseract."""

from __future__ import annotations

import ctypes
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("fail", [False, True])
def test_frozen_ocr_restores_dll_directory_even_when_tesseract_cannot_start(monkeypatch, fail):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "tools/ocr/src"))
    fallback = importlib.import_module("forma_ocr.text_fallback")
    changes = []
    monkeypatch.setattr(fallback.sys, "platform", "win32")
    monkeypatch.setattr(fallback.sys, "frozen", True, raising=False)
    monkeypatch.setattr(fallback.sys, "_MEIPASS", "synthetic-ocr-dlls", raising=False)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(SetDllDirectoryW=changes.append)), raising=False)
    marker = object()

    def launch(arguments, **kwargs):
        assert changes == [None]
        assert arguments == ["tesseract.exe", "--version"]
        assert kwargs == {"synthetic": True}
        if fail:
            raise OSError("Synthetic missing DLL")
        return marker

    monkeypatch.setattr(fallback.subprocess, "Popen", launch)
    if fail:
        with pytest.raises(OSError, match="Synthetic"):
            fallback._start_process(["tesseract.exe", "--version"], synthetic=True)
    else:
        assert fallback._start_process(["tesseract.exe", "--version"], synthetic=True) is marker
    assert changes == [None, "synthetic-ocr-dlls"]
