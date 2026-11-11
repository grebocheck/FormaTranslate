from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FORMA_UI_LANGUAGE", "uk")
    from forma.localization.catalog import set_language

    set_language("uk")
    yield
    set_language("uk")


@pytest.fixture(scope="session")
def generators():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/make_examples.py"))


@pytest.fixture
def docx_file(tmp_path, generators):
    path = tmp_path / "brief.docx"
    generators["make_docx"](path)
    return path


@pytest.fixture
def xlsx_file(tmp_path, generators):
    path = tmp_path / "checklist.xlsx"
    generators["make_xlsx"](path)
    return path


@pytest.fixture
def pdf_file(tmp_path, generators):
    path = tmp_path / "brief.pdf"
    generators["make_pdf"](path)
    return path
