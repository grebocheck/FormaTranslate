"""Real EPUB review keeps book text, draft and addresses during UI changes."""
from __future__ import annotations

import copy
from pathlib import Path

import httpx
import pytest
from epub_support import make_epub
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from support import TextProvider, local_settings, pump, settle
from ui_support import item, visual_item

from forma.localization.catalog import (
    LANGUAGE_METADATA,
    CatalogTranslator,
    current_language,
    set_language,
    translate,
)
from forma.presentation.backend import Backend
from forma.translation.engine import TranslationEngine


@pytest.mark.parametrize("version", ("2.0", "3.0"), ids=("epub2", "epub3"))
def test_epub_review_draft_survives_all_languages_and_themes(qapp, monkeypatch, tmp_path, version):
    previous_language = current_language()
    providers = []

    class OfflineProvider(TextProvider):
        input_tokens = output_tokens = 0

    class OfflineEngine(TranslationEngine):
        def __init__(self, settings, control):
            provider = OfflineProvider()
            providers.append(provider)
            super().__init__(settings, control, provider=provider)

    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *args, **kwargs: "")
    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", OfflineEngine)
    source = make_epub(tmp_path / "Налаштування {project}.epub", version=version)
    backend = Backend()
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    window = None
    try:
        settle(qapp, backend)
        backend.setOutputFolder(str(tmp_path / "out"))
        backend.addFiles([source.as_uri()])
        pump(qapp, lambda: backend.documents and backend.documents[0]["status"] == "ready")
        backend.start("en", "uk", "Загальна тематика", False, False)
        pump(qapp, lambda: not backend.busy and backend.documents[0]["status"] == "done")
        settle(qapp, backend)
        backend.setWorkspaceMode("review")
        pump(qapp, lambda: bool(backend.reviewEntries))
        entry = next(row for row in backend.reviewEntries if row["kind"] == "segment" and "Hello" in row["source"] and "world" in row["source"])
        backend.selectReviewEntry(entry["id"])
        backend.setReviewFilter("all")
        assert backend.selectedReviewEntry["canEdit"]
        raw_entry = copy.deepcopy(next(row for row in backend._review_all_entries if row["id"] == entry["id"]))
        assert raw_entry["location"] == "EPUB · document 1 · fragment 2"
        settings_before = copy.deepcopy(backend.settings)
        report = Path(backend.reviewDocument["report"])
        output = Path(backend.reviewDocument["output"])
        artifacts = {path: path.read_bytes() for path in (source, output, report, report.with_suffix(".html"))}
        calls_before = [len(provider.calls) for provider in providers]

        def no_api(*args, **kwargs):
            pytest.fail("Changing an EPUB interface language or theme must not call a provider")

        monkeypatch.setattr(httpx, "Client", no_api)
        monkeypatch.setattr("forma.providers.client.Provider.complete", no_api)
        backend.languageChanged.connect(engine.retranslate)
        engine.rootContext().setContextProperty("backend", backend)
        engine.load(QUrl.fromLocalFile(str(Path("src/forma/ui/Main.qml").resolve())))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.setWidth(960)
        window.setHeight(680)
        window.setProperty("page", 1)
        pump(qapp, lambda: visual_item(window.contentItem(), "reviewEditButton") is not None)
        backend.beginReviewEdit()
        draft = backend.reviewDraftText.replace("Вітаю", "Привіт")
        assert "Привіт" in draft and draft.count("\n") == 1
        assert "PRIVATE_" not in draft and "<" not in draft
        backend.updateReviewDraft(draft)
        pump(qapp, lambda: visual_item(window.contentItem(), "reviewTranslationText") is not None)
        editor = item(window, "reviewTranslationText")
        editor.forceActiveFocus()
        editor.setProperty("cursorPosition", 1)
        for _ in range(3):
            QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        for theme in ("light", "dark"):
            backend.setUiTheme(theme)
            for language in LANGUAGE_METADATA:
                backend.setUiLanguage(language)
                QTest.qWait(20)
                assert backend.uiTheme == theme and backend.uiLanguage == language
                assert backend.reviewEditing and backend.reviewDraftText == draft
                assert backend.selectedReviewId == entry["id"]
                assert backend.selectedReviewEntry["fingerprint"] == raw_entry["fingerprint"]
                assert backend.selectedReviewEntry["source"] == raw_entry["source"]
                assert backend.selectedReviewEntry["location"] == translate(
                    "EPUB · document {document} · fragment {fragment}", document=1, fragment=2,
                )
                current = item(window, "reviewTranslationText")
                assert current is editor and current.property("text") == draft
                assert current.property("cursorPosition") == 4
                assert current.property("selectionStart") == 1 and current.property("selectionEnd") == 4
                underlying = next(row for row in backend._review_all_entries if row["id"] == entry["id"])
                assert underlying["location"] == raw_entry["location"]
                assert backend.selectedDocument["format"] == "EPUB"
                assert backend.selectedDocument["name"] == source.name
                assert backend.settings == settings_before
        assert [len(provider.calls) for provider in providers] == calls_before
        assert all(path.read_bytes() == content for path, content in artifacts.items())
        assert not warnings, "\n".join(warnings)
    finally:
        backend.cancelReviewEdit()
        backend.cancel()
        settle(qapp, backend)
        if window is not None:
            window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
        set_language(previous_language)
