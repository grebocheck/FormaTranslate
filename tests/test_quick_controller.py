from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFileDialog
from support import pump

from forma.core.models import FormaError
from forma.presentation.backend import Backend
from forma.presentation.quick_controller import (
    MAX_IMAGE_BYTES,
    MAX_TEXT_CHARACTERS,
    checked_image,
    read_image,
)
from forma.settings.config import Settings
from forma.translation.images import ImageTranslation


@pytest.fixture
def controller(qapp, monkeypatch):
    monkeypatch.setattr(
        Backend, "_read_settings", lambda _: Settings(base_url="http://localhost/v1", model="fake")
    )
    backend = Backend()
    yield backend, backend.quickTranslator
    backend.quickTranslator.cancel()
    wait_for(lambda: not backend._threads)
    for dialog in backend._pickers.dialogs[:]:
        dialog.reject()
    qapp.clipboard().clear()


def wait_for(predicate, limit=300):
    pump(QCoreApplication.instance(), predicate, timeout=limit * 0.01)


def test_text_input_clipboard_limit_and_language_invalidation(controller, qapp):
    backend, quick = controller
    assert not quick.canTranslate
    qapp.clipboard().setText("Hello <b>plain text</b>")
    quick.pasteText()
    assert quick.sourceText == "Hello <b>plain text</b>"
    assert quick.canTranslate and quick.sourceCount == len(quick.sourceText)
    quick._translated_text = "Вітаю <b>звичайний текст</b>"
    quick.copyText()
    assert qapp.clipboard().text() == quick.translatedText
    quick.setSourceLanguage("en")
    assert quick.translatedText == ""
    quick._translated_text = "Вітаю"
    quick.swapLanguages()
    assert (quick.sourceLanguage, quick.targetLanguage) == ("uk", "en")
    assert quick.sourceText == "Вітаю" and quick.translatedText == ""
    over_limit = "я" * (MAX_TEXT_CHARACTERS + 1)
    quick.setSourceText(over_limit)
    assert quick.sourceText == over_limit  # Never truncate the user's clipboard silently.
    assert not quick.canTranslate and quick.error
    quick.setSourceText("short")
    assert quick.canTranslate and not quick.error
    quick.setTargetLanguage("uk")
    assert not quick.canTranslate
    quick.setTargetLanguage("en")
    backend._busy = True
    assert not quick.canTranslate


def test_image_clipboard_preview_save_and_original_protection(controller, qapp, tmp_path):
    backend, quick = controller
    image = QImage(90, 60, QImage.Format.Format_ARGB32)
    image.fill(QColor("#abcdef"))
    image.setText("private_comment", "must not be copied")
    image.setDevicePixelRatio(2)
    quick.setSourceText("Keep my text")
    quick._translated_text = "Збережи мій текст"
    qapp.clipboard().setImage(image)
    quick.pasteImage()
    assert quick.sourceText == "Keep my text" and quick.translatedText == "Збережи мій текст"
    assert quick.mode == "image" and quick.hasImage and quick.canTranslate
    assert quick.originalImageUrl.startswith("data:image/png;base64,")
    assert quick._image.devicePixelRatio() == 1 and not quick._image.textKeys()
    quick._image_result = quick._image.copy()
    quick._image_result.setPixelColor(0, 0, QColor("#ff0000"))
    quick._image_translated_text = "Переклад"
    quick.copyImage()
    assert qapp.clipboard().image().pixelColor(0, 0) == QColor("#ff0000")
    quick.copyText()
    assert qapp.clipboard().text() == "Переклад"
    assert quick.saveImageTo(str(tmp_path / "translated"))
    output = QImage(str(tmp_path / "translated.png"))
    assert output.size() == image.size() and output.pixelColor(0, 0) == QColor("#ff0000")
    assert not quick.saveImageTo(str(tmp_path / "translated.jpg"))
    original = tmp_path / "source.png"
    assert image.save(str(original))
    data = original.read_bytes()
    quick.loadImage(QUrl.fromLocalFile(str(original)).toString())
    assert quick.imageName == "source.png" and not quick.hasImageResult
    quick._image_result = quick._image.copy()
    assert not quick.saveImageTo(str(original))
    assert original.read_bytes() == data
    quick.clearImage()
    assert not quick.hasImage and not quick.hasImageResult and quick.originalImageUrl == ""
    assert not quick.canTranslate


def test_image_url_size_decode_and_edge_limits(controller, tmp_path):
    _, quick = controller
    quick.loadImage("https://example.org/private.png")
    assert not quick.hasImage and "локальний" in quick.error
    quick.loadImage("file://example.org/private.png")
    assert not quick.hasImage and "локальний" in quick.error
    invalid = tmp_path / "broken.png"
    invalid.write_bytes(b"not an image")
    quick.loadImage(str(invalid))
    assert not quick.hasImage and quick.error
    with invalid.open("wb") as f:
        f.truncate(MAX_IMAGE_BYTES + 1)
    with pytest.raises(FormaError, match="30"):
        read_image(invalid)
    huge_header = tmp_path / "long.png"
    long = QImage(20001, 1, QImage.Format.Format_ARGB32)
    long.fill(QColor("white"))
    assert long.save(str(huge_header))
    with pytest.raises(FormaError, match="20 000"):
        read_image(huge_header)
    with pytest.raises(FormaError, match="20 000"):
        checked_image(long)


def test_save_picker_retains_native_dialog_defaults(controller, tmp_path):
    backend, quick = controller
    quick._image_result = QImage(20, 10, QImage.Format.Format_ARGB32)
    quick._image_result.fill(QColor("white"))
    backend._folder = str(tmp_path)
    quick.saveImage()
    dialog = backend._pickers.dialogs[-1]
    try:
        assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
        assert dialog.fileMode() == QFileDialog.FileMode.AnyFile
        assert dialog.defaultSuffix() == "png"
        assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        assert not dialog.testOption(QFileDialog.Option.DontConfirmOverwrite)
        assert not dialog.styleSheet()
        assert Path(dialog.selectedFiles()[0]).name == "image.uk.png"
    finally:
        dialog.reject()


class FakeProvider:
    instances = []

    def __init__(self, settings, control):
        self.settings, self.control, self.closed = settings, control, False
        self.instances.append(self)

    def close(self):
        self.closed = True


def test_background_text_job_is_cancelled_on_close_without_stale_output(controller, monkeypatch):
    import forma.translation.text as module

    backend, quick = controller
    quick._provider_factory = FakeProvider
    entered, release = threading.Event(), threading.Event()

    def translate(provider, text, source, target, *, progress):
        entered.set()
        progress(40, "Перекладаємо текст…")
        assert release.wait(3)
        return "STALE RESULT"

    monkeypatch.setattr(module, "translate_text", translate)
    quick.setSourceText("Hello")
    quick.translate()
    wait_for(entered.is_set)
    assert quick.busy and backend.busy
    quick.setSourceText("changed")
    quick.setTargetLanguage("de")
    assert quick.sourceText == "Hello" and quick.targetLanguage == "uk"
    assert not backend.saveSettings({"model": "unexpected"})
    assert not backend.requestClose()
    assert quick._control.cancelled.is_set()
    release.set()
    wait_for(lambda: not backend._threads)
    assert not quick.busy and not quick.translatedText
    assert "зупинено" in quick.status.lower()
    assert FakeProvider.instances[-1].closed


def test_success_and_failure_job_paths_preserve_source_and_do_not_persist_content(
    controller, monkeypatch, tmp_path
):
    import forma.translation.text as module

    backend, quick = controller
    quick._provider_factory = FakeProvider
    secret = "Session-only text — no cache or history"
    monkeypatch.setattr(module, "translate_text", lambda *args, **kwargs: "Переклад")
    quick.setSourceText(secret)
    quick.translate()
    wait_for(lambda: not backend._threads)
    assert quick.translatedText == "Переклад" and not quick.error
    assert quick.sourceText == secret and not quick.busy
    assert FakeProvider.instances[-1].closed

    def fail(*args, **kwargs):
        raise FormaError("Модель повернула порожній переклад.")

    monkeypatch.setattr(module, "translate_text", fail)
    quick.translate()
    wait_for(lambda: not backend._threads)
    assert not quick.translatedText and quick.error
    for path in (tmp_path / "appdata").rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    assert backend.historyEntries == []


def test_image_worker_publishes_result_preview_and_readable_text(controller, monkeypatch, qapp):
    import forma.translation.images as module

    backend, quick = controller
    quick._provider_factory = FakeProvider
    image = QImage(70, 40, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    quick._set_image(image, "test.png")
    result = ImageTranslation(image.copy(), [], "Hello", "Вітаю", ["Перевірте текст."])
    monkeypatch.setattr(module, "translate_image", lambda *args, **kwargs: result)
    quick.translate()
    wait_for(lambda: not backend._threads)
    assert quick.hasImageResult and quick.translatedImageUrl.startswith("data:image/png;base64,")
    assert (quick.imageSourceText, quick.imageTranslatedText) == ("Hello", "Вітаю")
    assert quick.warnings == ["Перевірте текст."]
    assert FakeProvider.instances[-1].closed
