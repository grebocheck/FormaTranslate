"""Session-only text and image translation, clipboard and safe PNG export."""

from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import Property, QBuffer, QByteArray, QIODevice, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColorSpace, QGuiApplication, QImage, QImageReader, QPixmap

from forma.i18n import translate_ui_message as tr
from forma.image_translation import MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS
from forma.models import Cancelled, FormaError
from forma.provider import JobControl, Provider
from forma.quick_translation import MAX_TEXT_CHARACTERS
from forma.storage import atomic_bytes

MAX_IMAGE_BYTES = 30 * 1024 * 1024
LANGUAGES = {"auto", "en", "uk", "pl", "de", "fr", "es", "it", "pt", "cs", "ro", "zh", "ja"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def checked_image(image: QImage) -> QImage:
    if image.isNull():
        raise FormaError("Не вдалося прочитати зображення. Виберіть PNG, JPEG, WebP, BMP або TIFF.")
    if (
        image.width() * image.height() > MAX_IMAGE_PIXELS
        or max(image.width(), image.height()) > MAX_IMAGE_EDGE
    ):
        raise FormaError(
            "Зображення завелике. Максимум — 24 мегапікселі та 20 000 пікселів на сторону."
        )
    result = (
        image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        if image.colorSpace().isValid()
        else image.copy()
    )
    result.setDevicePixelRatio(1)
    # Raster copies omit source metadata (EXIF, comments and clipboard provenance).
    clean = QImage(result.size(), QImage.Format.Format_ARGB32)
    clean.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter

    painter = QPainter(clean)
    painter.drawImage(0, 0, result)
    painter.end()
    clean.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    return clean


def read_image(path: Path) -> QImage:
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        raise FormaError("Виберіть файл зображення PNG, JPEG, WebP, BMP або TIFF.")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise FormaError("Файл зображення завеликий. Максимум — 30 МБ.")
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (
        size.width() * size.height() > MAX_IMAGE_PIXELS
        or max(size.width(), size.height()) > MAX_IMAGE_EDGE
    ):
        raise FormaError(
            "Зображення завелике. Максимум — 24 мегапікселі та 20 000 пікселів на сторону."
        )
    return checked_image(reader.read())


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise FormaError("Не вдалося підготувати зображення для збереження.")
    return bytes(data)


def preview_url(image: QImage) -> str:
    if image.isNull():
        return ""
    preview = (
        image.scaled(
            1600,
            1600,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if max(image.width(), image.height()) > 1600
        else image
    )
    return "data:image/png;base64," + base64.b64encode(png_bytes(preview)).decode("ascii")


class QuickTranslator(QObject):
    changed = Signal()
    draftChanged = Signal()
    activityChanged = Signal()

    def __init__(self, parent, *, settings, launch, available, pickers, output_folder):
        super().__init__(parent)
        self._settings, self._launch, self._available = settings, launch, available
        self._pickers, self._output_folder = pickers, output_folder
        self._provider_factory = Provider
        self._mode, self._source_language, self._target_language = "text", "auto", "uk"
        self._source_text = self._translated_text = ""
        self._image, self._image_result = QImage(), QImage()
        self._image_name = self._image_path = ""
        self._original_url = self._translated_url = ""
        self._image_source_text = self._image_translated_text = ""
        self._warnings = []
        self._busy = False
        self._control = None
        self._status = self._error = ""
        self._progress = 0
        self._serial = 0

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(str, notify=draftChanged)
    def sourceText(self):
        return self._source_text

    @Property(str, notify=changed)
    def translatedText(self):
        return self._translated_text

    @Property(str, notify=changed)
    def sourceLanguage(self):
        return self._source_language

    @Property(str, notify=changed)
    def targetLanguage(self):
        return self._target_language

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return tr(self._status)

    @Property(str, notify=changed)
    def error(self):
        return tr(self._error)

    @Property(int, notify=changed)
    def progress(self):
        return self._progress

    @Property(int, constant=True)
    def maximumTextLength(self):
        return MAX_TEXT_CHARACTERS

    @Property(int, notify=draftChanged)
    def sourceCount(self):
        return len(self._source_text)

    @Property(str, notify=changed)
    def imageName(self):
        return self._image_name if self._image_path else tr(self._image_name)

    @Property(str, notify=changed)
    def originalImageUrl(self):
        return self._original_url

    @Property(str, notify=changed)
    def translatedImageUrl(self):
        return self._translated_url

    @Property(bool, notify=changed)
    def hasImage(self):
        return not self._image.isNull()

    @Property(bool, notify=changed)
    def hasImageResult(self):
        return not self._image_result.isNull()

    @Property(str, notify=changed)
    def imageSourceText(self):
        return self._image_source_text

    @Property(str, notify=changed)
    def imageTranslatedText(self):
        return self._image_translated_text

    @Property("QStringList", notify=changed)
    def warnings(self):
        return [tr(value) for value in self._warnings] if self._mode == "image" else []

    @Property(bool, notify=changed)
    def canTranslate(self):
        if self._busy or not self._available() or self._source_language == self._target_language:
            return False
        try:
            self._settings().validate()
        except FormaError:
            return False
        return (
            bool(self._source_text.strip()) and len(self._source_text) <= MAX_TEXT_CHARACTERS
            if self._mode == "text"
            else self.hasImage
        )

    def _clear_results(self):
        self._translated_text = self._image_source_text = self._image_translated_text = ""
        self._image_result, self._translated_url = QImage(), ""
        self._warnings = []
        self._error = self._status = ""
        self._progress = 0

    @Slot(str)
    def setSourceText(self, text):
        if self._busy or text == self._source_text:
            return
        self._source_text, self._translated_text = text, ""
        self._status = ""
        self._error = (
            "Текст задовгий. Максимум — 20 000 символів за один переклад."
            if len(text) > MAX_TEXT_CHARACTERS
            else ""
        )
        self.draftChanged.emit()
        self.changed.emit()

    @Slot(str)
    def setMode(self, mode):
        if self._busy or mode not in {"text", "image"} or mode == self._mode:
            return
        self._mode, self._error, self._status = mode, "", ""
        self.changed.emit()

    @Slot(str)
    def setSourceLanguage(self, code):
        if not self._busy and code in LANGUAGES and code != self._source_language:
            self._source_language = code
            self._clear_results()
            self.changed.emit()

    @Slot(str)
    def setTargetLanguage(self, code):
        if not self._busy and code in LANGUAGES - {"auto"} and code != self._target_language:
            self._target_language = code
            self._clear_results()
            self.changed.emit()

    @Slot()
    def swapLanguages(self):
        if self._busy or self._source_language == "auto":
            return
        self._source_language, self._target_language = self._target_language, self._source_language
        if self._mode == "text" and self._translated_text:
            self._source_text = self._translated_text
            self.draftChanged.emit()
        self._clear_results()
        self.changed.emit()

    def _clipboard(self):
        app = QGuiApplication.instance()
        if app is None:
            raise FormaError("Буфер обміну недоступний.")
        return app.clipboard()

    @Slot()
    def pasteText(self):
        if self._busy:
            return
        text = self._clipboard().text()
        if text:
            self.setSourceText(text)
        else:
            self._error = "У буфері обміну немає тексту."
            self.changed.emit()

    @Slot()
    def copyText(self):
        text = self._translated_text if self._mode == "text" else self._image_translated_text
        if text:
            self._clipboard().setText(text)

    @Slot()
    def pickImage(self):
        if not self._busy:
            self._pickers.choose(
                "Відкрити зображення",
                lambda paths: self.loadImage(paths[0]),
                name_filter="Зображення (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)",
            )

    @Slot(str)
    def loadImage(self, url):
        if self._busy:
            return
        try:
            value = QUrl(url)
            if value.host() or url.startswith(("//", "\\\\")):
                raise FormaError("Виберіть локальний файл зображення.")
            if (
                value.scheme()
                and not value.isLocalFile()
                and not (len(value.scheme()) == 1 and len(url) > 2 and url[1] == ":")
            ):
                raise FormaError("Виберіть локальний файл зображення.")
            path = Path(value.toLocalFile() if value.isLocalFile() else url).expanduser().resolve()
            image = read_image(path)
            self._set_image(image, path.name, str(path))
        except (FormaError, OSError, ValueError) as exc:
            self._error = (
                str(exc) if isinstance(exc, FormaError) else "Не вдалося відкрити зображення."
            )
            self.changed.emit()

    def _set_image(self, image, name, path=""):
        prepared = checked_image(image)
        url = preview_url(prepared)
        self._image_result, self._translated_url = QImage(), ""
        self._image_source_text = self._image_translated_text = ""
        self._warnings, self._error, self._status = [], "", ""
        self._progress = 0
        self._image, self._image_name, self._image_path = prepared, name, path
        self._original_url = url
        self._mode = "image"
        self.changed.emit()

    @Slot()
    def pasteImage(self):
        if self._busy:
            return
        try:
            clipboard = self._clipboard()
            mime = clipboard.mimeData()
            if mime and mime.hasImage():
                image = mime.imageData()
                if isinstance(image, QPixmap):
                    image = image.toImage()
                if not isinstance(image, QImage):
                    image = clipboard.image()
                self._set_image(image, "Зображення з буфера обміну")
            elif mime and mime.hasUrls() and len(mime.urls()) == 1 and mime.urls()[0].isLocalFile():
                self.loadImage(mime.urls()[0].toString())
            else:
                raise FormaError(
                    "У буфері обміну немає зображення. Скопіюйте фото або знімок екрана."
                )
        except FormaError as exc:
            self._error = str(exc)
            self.changed.emit()

    @Slot()
    def clearImage(self):
        if self._busy:
            return
        self._image, self._image_result = QImage(), QImage()
        self._image_name = self._image_path = self._original_url = self._translated_url = ""
        self._image_source_text = self._image_translated_text = ""
        self._warnings, self._error, self._status = [], "", ""
        self.changed.emit()

    @Slot()
    def copyImage(self):
        if self.hasImageResult:
            self._clipboard().setImage(self._image_result.copy())

    @Slot()
    def saveImage(self):
        if self.hasImageResult and not self._busy:
            stem = Path(self._image_name).stem if self._image_path else "image"
            suggested = Path(self._output_folder()) / f"{stem}.{self._target_language}.png"
            self._pickers.choose(
                "Зберегти перекладене зображення",
                lambda paths: self.saveImageTo(paths[0]),
                directory=str(suggested),
                name_filter="PNG (*.png)",
                save=True,
            )

    @Slot(str, result=bool)
    def saveImageTo(self, value):
        if not self.hasImageResult or self._busy:
            return False
        try:
            url = QUrl(value)
            path = Path(url.toLocalFile() if url.isLocalFile() else value).expanduser().resolve()
            if not path.suffix:
                path = path.with_suffix(".png")
            if path.suffix.lower() != ".png":
                raise FormaError("Збережіть перекладене зображення у форматі PNG.")
            if self._image_path and path == Path(self._image_path):
                raise FormaError("Виберіть іншу назву файла, щоб зберегти оригінал.")
            atomic_bytes(path, png_bytes(self._image_result))
            self._status, self._error = "Зображення збережено.", ""
            self.changed.emit()
            return True
        except (FormaError, OSError, ValueError) as exc:
            self._error = (
                str(exc) if isinstance(exc, FormaError) else "Не вдалося зберегти зображення."
            )
            self.changed.emit()
            return False

    @Slot()
    def translate(self):
        if self._busy or not self._available():
            return
        try:
            settings = self._settings()
            settings.validate()
            if self._source_language == self._target_language:
                raise FormaError("Оберіть різні мови оригіналу та перекладу.")
            if self._mode == "text" and (
                not self._source_text.strip() or len(self._source_text) > MAX_TEXT_CHARACTERS
            ):
                raise FormaError("Введіть текст довжиною від 1 до 20 000 символів.")
            if self._mode == "image" and not self.hasImage:
                raise FormaError("Додайте зображення для перекладу.")
        except FormaError as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        mode, source, target = self._mode, self._source_language, self._target_language
        text, image = self._source_text, self._image.copy()
        if mode == "text":
            self._translated_text = ""
        else:
            self._image_result, self._translated_url = QImage(), ""
            self._image_source_text = self._image_translated_text = ""
            self._warnings = []
        control = JobControl(settings.max_parallel_requests)
        self._control, self._busy = control, True
        self._status, self._error, self._progress = "Готуємо переклад…", "", 0
        self._serial += 1
        serial = self._serial
        factory = self._provider_factory

        def run(emit):
            provider = factory(settings, control)
            try:

                def progress(percent, message):
                    emit("progress", (percent, message))

                if mode == "text":
                    from forma.quick_translation import translate_text

                    result = translate_text(provider, text, source, target, progress=progress)
                    control.check()
                    emit("text_result", result)
                else:
                    from forma.image_translation import translate_image

                    result = translate_image(provider, image, source, target, progress=progress)
                    control.check()
                    emit("image_result", (result, preview_url(result.image)))
            except Cancelled:
                emit("cancelled", None)
            finally:
                provider.close()

        def receive(kind, payload):
            if serial != self._serial:
                return
            if control.cancelled.is_set() or kind == "cancelled":
                self._status, self._error = "Переклад зупинено.", ""
            elif kind == "progress":
                self._progress, self._status = max(0, min(100, int(payload[0]))), payload[1]
            elif kind == "text_result":
                self._translated_text, self._status, self._progress = payload, "Готово", 100
            elif kind == "image_result":
                result, preview = payload
                self._image_result, self._translated_url = result.image, preview
                self._image_source_text, self._image_translated_text = (
                    result.source_text,
                    result.translated_text,
                )
                self._warnings = result.warnings
                self._status, self._progress = "Готово", 100
            elif kind == "error":
                self._error, self._status = payload, ""
            self.changed.emit()

        def finish():
            if serial == self._serial:
                if control.cancelled.is_set():
                    self._status, self._error = "Переклад зупинено.", ""
                self._busy, self._control = False, None
                self.changed.emit()
                self.activityChanged.emit()

        self.changed.emit()
        self.activityChanged.emit()
        self._launch(run, handler=receive, done=finish)

    @Slot()
    def cancel(self):
        if self._control:
            self._control.cancelled.set()
            self._control.paused.clear()
            self._status = "Зупиняємо після поточного запиту…"
            self.changed.emit()
