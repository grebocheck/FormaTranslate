"""User-visible glossary drafts stay local until explicit generation/apply actions."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from docx import Document
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

import forma.presentation.glossary_controller as glossary_module
from forma.localization.catalog import CatalogTranslator
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def settle(qapp, backend):
    deadline = time.monotonic() + 5
    while backend._threads and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert not backend._threads


def visual_item(parent, name):
    if parent.objectName() == name:
        return parent
    for child in parent.childItems():
        result = visual_item(child, name)
        if result is not None:
            return result
    return None


@pytest.fixture
def backend(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(base_url="http://127.0.0.1:1/v1", model="offline-synthetic", api_key="fake-offline"))
    value = Backend()
    qapp.processEvents()
    yield value
    value.glossaryEditor.cancel()
    settle(qapp, value)


def prepare(backend):
    editor = backend.glossaryEditor
    editor.prepare("en", "uk", "Технічна документація")
    return editor


def add(editor, source, target):
    editor.addTerm()
    index = len(editor.rows.rows) - 1
    editor.setTerm(index, "source", source)
    editor.setTerm(index, "target", target)


def test_manual_list_needs_no_csv_and_is_not_active_until_apply(backend):
    editor = prepare(backend)
    assert not editor.rows.rows
    add(editor, "heat meter", "лічильник тепла")
    assert backend.glossaryCount == 0 and editor.dirty
    editor.prepare("en", "uk", "Загальна тематика")
    assert editor.rows.rows == [{"source": "heat meter", "target": "лічильник тепла"}]
    assert editor.apply()
    assert backend._glossary == {"heat meter": "лічильник тепла"}
    assert not editor.dirty
    editor.removeTerm(0)
    assert editor.apply() and backend.glossaryCount == 0  # Empty glossary is valid.


def test_incomplete_or_conflicting_rows_cannot_replace_active_glossary(backend):
    editor = prepare(backend)
    add(editor, "heat meter", "лічильник тепла")
    assert editor.apply()
    add(editor, "flow sensor", "")
    assert not editor.apply()
    assert "Заповніть" in editor.message
    assert backend.glossaryCount == 1
    editor.setTerm(1, "source", "heat meter")
    editor.setTerm(1, "target", "інший переклад")
    assert not editor.apply()
    assert "різні переклади" in editor.message
    assert backend._glossary["heat meter"] == "лічильник тепла"


def test_project_drafts_remain_separate_and_language_switch_keeps_rows(backend):
    assert backend.createMemoryProject("First synthetic project", "en", "uk")
    first = backend.memoryProject["id"]
    editor = prepare(backend)
    add(editor, "heat meter", "лічильник тепла")
    assert backend.createMemoryProject("Second synthetic project", "en", "uk")
    editor.prepare("en", "uk", "Технічна документація")
    assert not editor.rows.rows
    add(editor, "flow sensor", "датчик витрати")
    assert backend.selectMemoryProject(first)
    editor.prepare("en", "uk", "Технічна документація")
    for language in ("en", "pl", "de", "uk"):
        backend.setUiLanguage(language)
        assert editor.rows.rows == [{"source": "heat meter", "target": "лічильник тепла"}]
        assert editor.dirty
    assert editor.apply()
    assert backend._memory.get_project(first).glossary == {"heat meter": "лічильник тепла"}


def test_project_revision_conflict_preserves_the_unsaved_draft(backend):
    backend.createMemoryProject("Synthetic CAS", "en", "uk")
    editor = prepare(backend)
    add(editor, "heat meter", "лічильник тепла")
    backend._memory.update_glossary(backend.memoryProject["id"], {"external": "зовнішнє"}, expected_revision=0)
    assert not editor.apply()
    assert editor.dirty and editor.rows.rows[0]["source"] == "heat meter"
    assert backend._memory.get_project(backend.memoryProject["id"]).glossary == {"external": "зовнішнє"}
    editor.discard()
    assert editor.rows.rows == [{"source": "external", "target": "зовнішнє"}]
    assert not editor.dirty


def test_csv_import_is_preview_merge_and_export_round_trips_unicode(backend, tmp_path):
    editor = prepare(backend)
    add(editor, "heat meter", "ваш узгоджений термін")
    csv = tmp_path / "terms.csv"
    csv.write_text('source,target\nheat meter,do not replace\n"flow, sensor","датчик, витрати"\n', encoding="utf-8")
    editor.importCsv(str(csv))
    assert "1" in editor.message
    assert backend.glossaryCount == 0
    assert editor.rows.rows == [
        {"source": "heat meter", "target": "ваш узгоджений термін"},
        {"source": "flow, sensor", "target": "датчик, витрати"},
    ]
    output = tmp_path / "exported.csv"
    editor.exportCsv(str(output))
    assert output.read_bytes().startswith(b"\xef\xbb\xbfsource,target")
    editor.discard()
    assert not editor.rows.rows
    editor.importCsv(str(output))
    assert editor.apply()
    assert backend._glossary["flow, sensor"] == "датчик, витрати"


def test_generation_is_explicit_bounded_reviewable_and_preserves_existing_preferences(qapp, backend, tmp_path, monkeypatch):
    path = tmp_path / "synthetic.docx"
    document = Document()
    document.add_paragraph("A heat meter uses a flow sensor for metering hot water.")
    document.save(path)
    backend.addFiles([str(path)])
    settle(qapp, backend)
    instances = []

    class Provider:
        def __init__(self, settings, control):
            self.calls = []
            self.closed = False
            instances.append(self)

        def complete(self, instruction, payload, schema, **kwargs):
            assert kwargs["retain_completed_on_cancel"] is True
            self.calls.append((instruction, payload, schema))
            return {"terms": [
                {"source": "heat meter", "target": "не замінювати"},
                {"source": "flow sensor", "target": "датчик витрати"},
                {"source": "invented outside excerpt", "target": "вигадано"},
            ]}

        def close(self):
            self.closed = True

    monkeypatch.setattr(glossary_module, "Provider", Provider)
    editor = prepare(backend)
    add(editor, "heat meter", "погоджений лічильник")
    assert not instances  # Opening/editing/importing never requests the API.
    editor.suggest()
    assert editor.working and backend.busy
    settle(qapp, backend)
    assert len(instances) == len(instances[0].calls) == 1
    assert "1" in editor.message
    assert instances[0].closed
    assert instances[0].calls[0][1]["source_language"] == "en"
    assert editor.rows.rows == [
        {"source": "heat meter", "target": "погоджений лічильник"},
        {"source": "flow sensor", "target": "датчик витрати"},
    ]
    assert backend.glossaryCount == 0 and editor.dirty and not editor.working
    assert editor.apply() and backend.glossaryCount == 2


def test_generation_without_office_documents_does_not_create_a_provider(backend, monkeypatch):
    monkeypatch.setattr(glossary_module, "Provider", lambda *_: pytest.fail("No documents must mean no API call"))
    editor = prepare(backend)
    editor.suggest()
    assert editor.error and "Додайте DOCX" in editor.message
    assert not editor.working


def test_generation_errors_do_not_leak_provider_exception_content(qapp, backend, tmp_path, monkeypatch):
    path = tmp_path / "synthetic.docx"
    document = Document()
    document.add_paragraph("A heat meter uses a flow sensor.")
    document.save(path)
    backend.addFiles([str(path)])
    settle(qapp, backend)

    class Provider:
        def __init__(self, *_):
            pass

        def complete(self, *_, **kwargs):
            raise RuntimeError("SECRET_SYNTHETIC_REQUEST_HEADER")

        def close(self):
            pass

    monkeypatch.setattr(glossary_module, "Provider", Provider)
    editor = prepare(backend)
    add(editor, "heat meter", "лічильник")
    editor.suggest()
    settle(qapp, backend)
    assert editor.error and "SECRET_SYNTHETIC" not in editor.message
    assert editor.rows.rows[0]["target"] == "лічильник"


def test_glossary_dialog_loads_and_term_editing_does_not_reset_delegate(qapp, backend, tmp_path):
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml"))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    editor = prepare(backend)
    add(editor, "heat meter", "лічильник")
    dialog = window.findChild(QObject, "glossaryDialog")
    dialog.open()
    QTest.qWait(30)
    qapp.processEvents()
    view = window.findChild(QObject, "glossaryTermList")
    view.forceLayout()
    qapp.processEvents()
    try:
        suggest = window.findChild(QObject, "suggestGlossaryButton")
        assert not suggest.property("enabled")
        document = Document()
        document.add_paragraph("A heat meter measures hot water.")
        path = tmp_path / "newly-added.docx"
        document.save(path)
        backend.addFiles([str(path)])
        settle(qapp, backend)
        assert suggest.property("enabled")
        source = visual_item(view.property("contentItem"), "glossarySourceTerm")
        assert source is not None
        source.setProperty("text", "heat meter revised")
        source.textEdited.emit()
        qapp.processEvents()
        assert source is visual_item(view.property("contentItem"), "glossarySourceTerm")
        assert source.property("text") == "heat meter revised"
        for language in ("en", "uk", "pl", "de"):
            backend.setUiLanguage(language)
            qapp.processEvents()
            assert editor.rows.rows[0]["source"] == "heat meter revised"
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        qapp.removeTranslator(translator)


def test_diagnostic_report_link_is_separate_from_completed_translation(qapp, backend, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    source = tmp_path / "document.docx"
    source.write_bytes(b"synthetic path only")
    document = {"id": "synthetic", "path": str(source), "format": "DOCX", "status": "translating"}
    backend._documents = [document]
    backend._selected = "synthetic"
    report = tmp_path / "document.validation-test.html"
    report.write_text("<html>Escaped synthetic diagnostic</html>")
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    backend._handle_event("file_error", {"id": "synthetic", "error": "Synthetic numeric mismatch", "diagnosticReport": str(report)})
    assert backend.selectedDocument["diagnosticReport"] == str(report)
    backend.openFailureReport()
    assert len(opened) == 1 and opened[0].isLocalFile() and opened[0].toLocalFile() == str(report)
    assert not document.get("output") and not document.get("report")
