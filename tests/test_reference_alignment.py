"""Explicit appendix-label edits preserve draft state and original documents."""

from pathlib import Path

import pytest
from docx import Document
from support import pump

from forma.core.models import FormaError, Options, Segment, TranslationResult
from forma.documents.editor import edit_translation_markup
from forma.documents.ooxml import OfficeDocument
from forma.persistence.review_store import ReviewStore
from forma.presentation.backend import Backend
from forma.settings.config import Settings
from forma.translation.references import (
    check_document_references,
    plan_reference_alignment,
)
from forma.translation.reports import write_translation_report


def segment(index, source, part="word/document.xml"):
    return Segment(f"{part}#{index}", "Heading", f"<r0>{source}</r0>")


def test_selected_label_changes_only_heading_letters_across_runs_and_toc_anchors():
    segments = [segment(1, "Додаток Д. Дані"), segment(2, "Додаток Д. Таблиці"),
                segment(3, "Див. Додаток Д."), segment(4, "Додаток Д. Заголовок", "word/header1.xml")]
    translations = {
        segments[0].id: "<r0>Appendix Zh. Data</r0>",
        segments[1].id: "<r0>App</r0><r1>endix </r1><r2>D</r2><r3>. Manually edited tables</r3><k0/><r4>15</r4>",
        segments[2].id: "<r0>See Appendix D.</r0>",
        segments[3].id: "<r0>Appendix D. Header</r0>",
    }
    original = dict(translations)
    planned = plan_reference_alignment(segments, translations, segments[0].id)
    assert set(planned) == {segments[1].id}
    revised = edit_translation_markup(translations[segments[1].id], planned[segments[1].id])
    assert revised == translations[segments[1].id].replace("<r2>D</r2>", "<r2>Zh</r2>")
    assert not check_document_references(segments, {**translations, segments[1].id: revised})
    assert translations == original


@pytest.mark.parametrize("other_source,other_target,error", [
    ("Додаток Б. Звіт", "Appendix Е. Report", "іншого додатка"),
    ("Додаток Д. Звіт", "Appendix D–F. Report", "однозначне"),
])
def test_label_plan_refuses_collision_or_ambiguous_peer_without_partial_changes(other_source, other_target, error):
    segments = [segment(1, "Додаток Д. Дані"), segment(2, "Додаток Д. Таблиці"),
                segment(3, other_source)]
    translations = {segments[0].id: "<r0>Appendix E. Data</r0>",
                    segments[1].id: "<r0>Appendix D. Tables</r0>",
                    segments[2].id: f"<r0>{other_target}</r0>"}
    original = dict(translations)
    with pytest.raises(FormaError, match=error):
        plan_reference_alignment(segments, translations, segments[0].id)
    assert translations == original


@pytest.fixture
def appendix_review(qapp, tmp_path, monkeypatch):
    source = tmp_path / "appendices.docx"
    document = Document()
    document.add_paragraph("Додаток Д. Дані")
    document.add_paragraph("Додаток Д. Таблиці")
    document.add_paragraph("Звичайний текст")
    document.save(source)
    office = OfficeDocument(source)
    selected, peer, unrelated = office.segments
    translations = {selected.id: "<r0>Appendix E. Data</r0>",
                    peer.id: "<r0>Appendix D. Tables</r0>",
                    unrelated.id: "<r0>Ordinary text</r0>"}
    output = tmp_path / "appendices.en.docx"
    office.write(translations, output)
    result = TranslationResult(source, output, office.segments, translations, [],
                               extraction_profile=office.extraction_profile)
    write_translation_report(result, Options("uk", "en", review=False, auto_glossary=False), "offline")
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend._library.add_history({
        "id": "appendix-fixture", "name": source.name, "format": "DOCX", "source": str(source),
        "output": str(output), "report": str(result.report), "status": "done",
        "sourceLanguage": "uk", "targetLanguage": "en", "model": "offline",
    })
    backend._history = backend._library.list_history()
    backend.reviewHistory("appendix-fixture")
    pump(qapp, lambda: not backend._threads)
    backend.setReviewFilter("all")
    backend.selectReviewEntry(selected.id)
    yield backend, result, selected, peer, unrelated
    backend.cancelReviewEdit()
    pump(qapp, lambda: not backend._threads)


def test_alignment_persists_preserves_existing_edits_and_exports_only_a_copy(appendix_review, qapp, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("Manual alignment called API"))
    backend, result, selected, peer, unrelated = appendix_review
    original = result.output.read_bytes()
    report = result.report.read_bytes()
    backend.selectReviewEntry(unrelated.id)
    backend.beginReviewEdit()
    backend.updateReviewDraft("Previously reviewed ordinary text")
    assert backend.applyReviewEdit()
    backend.selectReviewEntry(peer.id)
    backend.beginReviewEdit()
    backend.updateReviewDraft("Appendix D. Carefully edited tables")
    assert backend.applyReviewEdit()
    backend.selectReviewEntry(selected.id)
    assert backend.selectedReviewEntry["canAlignReferences"]
    assert backend.selectedReviewEntry["referenceLabel"] == "E"
    assert backend.alignReviewReferences()
    state = ReviewStore().load(result.report)
    assert state["edits"][peer.id] == "<r0>Appendix E. Carefully edited tables</r0>"
    assert state["edits"][unrelated.id] == "<r0>Previously reviewed ordinary text</r0>"
    assert result.output.read_bytes() == original and result.report.read_bytes() == report
    backend.reviewHistory("appendix-fixture")
    pump(qapp, lambda: not backend._threads)
    backend.selectReviewEntry(peer.id)
    assert backend.selectedReviewEntry["translation"] == "Appendix E. Carefully edited tables"
    backend.exportReviewEdits()
    pump(qapp, lambda: not backend._threads)
    assert not backend.reviewActionError
    assert result.output.read_bytes() == original
    exported = Document(backend.reviewDocument["output"])
    assert [paragraph.text for paragraph in exported.paragraphs] == [
        "Appendix E. Data", "Appendix E. Carefully edited tables", "Previously reviewed ordinary text",
    ]


def test_real_alignment_menu_localizes_and_keeps_selection_before_explicit_action(appendix_review, qapp):
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui_support import item as ui_item

    from forma.localization.catalog import CatalogTranslator

    backend, result, selected, peer, _ = appendix_review
    engine = QQmlApplicationEngine()
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    backend.languageChanged.connect(engine.retranslate)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    try:
        window.setProperty("page", 1)
        pump(qapp, lambda: not backend._threads)
        backend.selectReviewEntry(selected.id)
        QTest.qWait(20)
        card = ui_item(window, "reviewCard_" + selected.id)
        item = card.findChild(QObject, "reviewAlignReferencesButton")
        more = ui_item(window, "reviewMoreActionsButton")
        assert item is not None
        labels = set()
        for language, theme in (("uk", "light"), ("en", "dark"), ("pl", "light"),
                                ("de", "dark"), ("fr", "light"), ("es", "dark")):
            backend.setUiLanguage(language)
            backend.setUiTheme(theme)
            qapp.processEvents()
            labels.add(item.property("text"))
            assert "E" in item.property("text")
            assert backend.selectedReviewId == selected.id and not backend.reviewHasEdits
            assert more.isVisible() and more.isEnabled()
        assert len(labels) == 6
        point = more.mapToScene(QPointF(more.width() / 2, more.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
        QTest.qWait(20)
        assert item.isVisible() and item.isEnabled()
        assert item.mapToScene(QPointF()).x() >= 0
        assert item.mapToScene(QPointF(item.width(), 0)).x() <= window.width()
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
        qapp.processEvents()
        assert ReviewStore().load(result.report)["edits"][peer.id] == "<r0>Appendix E. Tables</r0>"
        assert not warnings, "\n".join(warnings)
    finally:
        backend.languageChanged.disconnect(engine.retranslate)
        qapp.removeTranslator(translator)
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
