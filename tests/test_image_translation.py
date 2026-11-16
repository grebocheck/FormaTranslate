from __future__ import annotations

import base64
from dataclasses import asdict

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

from forma.core.models import FormaError
from forma.providers.client import Cancelled, JobControl
from forma.settings.config import Settings
from forma.translation.image_geometry import TextLine, TextWord
from forma.translation.images import (
    MAX_BLOCKS,
    MAX_IMAGE_PIXELS,
    MAX_UPLOAD_EDGE,
    MAX_UPLOAD_PIXELS,
    ImageBlock,
    _image_payload,
    _parse_blocks,
    _render,
    _text_layout,
    translate_image,
    validate_image,
)


@pytest.fixture(autouse=True)
def no_host_ocr(monkeypatch):
    # Pixel/protocol tests must not depend on host-installed language packs.
    monkeypatch.setattr("forma.translation.images.detect_text", lambda *_: [])


class FakeProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.settings = Settings()
        self.control = JobControl()
        self.calls = []

    def complete(self, instruction, payload, schema, **kwargs):
        self.control.check()
        self.calls.append((instruction, payload, schema, kwargs))
        return next(self.responses)


def picture(width=400, height=200, *, color="white"):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(color))
    return image


def block(source="Welcome", translation="Ласкаво просимо", **coords):
    values = {"x": 100, "y": 200, "width": 700, "height": 400} | coords
    return asdict(ImageBlock(source, translation, **values))


@pytest.mark.parametrize("translation", ["Ласкаво просимо", "Welcome", "Witamy serdecznie", "Herzlich willkommen"])
def test_vision_translation_preserves_pixels_outside_regions_and_source(qapp, translation):
    image = picture(color="#d8e5d8")
    image.setPixelColor(0, 0, QColor("#845120"))
    original = image.copy()
    region = block("Вітаємо", translation)
    provider = FakeProvider({"blocks": [region]})
    progress = []
    result = translate_image(provider, image, "uk", "de", progress=lambda *args: progress.append(args))

    assert image == original
    assert result.image.size() == original.size()
    assert result.source_text == "Вітаємо"
    assert result.translated_text == translation
    assert result.warnings == []
    rectangle = result.blocks[0].rectangle(image.width(), image.height())
    changed = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if rectangle.contains(x, y):
                changed += result.image.pixelColor(x, y) != image.pixelColor(x, y)
            else:
                assert result.image.pixelColor(x, y) == image.pixelColor(x, y)
    assert changed > 0
    assert [item[0] for item in progress] == [10, 75, 90, 100]
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == {"source_language": "uk", "target_language": "de"}
    assert provider.calls[0][3]["images"][0].startswith("data:image/png;base64,")


def test_prepared_image_is_bounded_opaque_and_metadata_free(qapp):
    original = picture(4000, 2000, color="#00000000")
    original.setText("private-file", "/private/photo.png")
    original.setDevicePixelRatio(2)
    encoded = _image_payload(original)
    prepared = QImage.fromData(base64.b64decode(encoded.split(",", 1)[1]))
    assert not prepared.isNull()
    assert max(prepared.width(), prepared.height()) <= MAX_UPLOAD_EDGE
    assert prepared.width() * prepared.height() <= MAX_UPLOAD_PIXELS
    assert prepared.width() / prepared.height() == pytest.approx(2, rel=0.002)
    assert prepared.textKeys() == []
    assert prepared.pixelColor(0, 0) == QColor("white")
    assert original.text("private-file") == "/private/photo.png"
    assert original.devicePixelRatio() == 2


def test_high_dpi_image_rectangles_use_original_pixels(qapp):
    image = picture()
    image.setDevicePixelRatio(2)
    provider = FakeProvider({"blocks": [block()]})
    result = translate_image(provider, image, "en", "uk")
    assert result.image.size() == image.size()
    assert result.image.devicePixelRatio() == 1
    assert result.image.pixelColor(399, 199) == image.pixelColor(399, 199)
    assert result.image != image


def test_transparent_pixels_outside_text_are_preserved(qapp):
    image = picture(color="#40a0e030")
    provider = FakeProvider({"blocks": [block()]})
    result = translate_image(provider, image, "en", "uk")
    rectangle = result.blocks[0].rectangle(image.width(), image.height())
    for point in ((0, 0), (399, 199), (50, 170)):
        assert not rectangle.contains(*point)
        assert result.image.pixelColor(*point) == image.pixelColor(*point)
    assert result.image.pixelColor(rectangle.left(), rectangle.top()).alpha() == 255


def test_translated_text_remains_available_when_region_cannot_fit(qapp):
    image = picture()
    translated = "Довгий переклад для дуже маленького підпису. " * 8
    provider = FakeProvider({"blocks": [block(translation=translated, width=20, height=20)]})
    result = translate_image(provider, image, "en", "uk")
    assert result.image == image
    assert result.translated_text == translated
    assert len(result.warnings) == 1
    assert "не помістилися" in result.warnings[0]


def test_overlap_is_not_overpainted_and_keeps_both_translations(qapp):
    image = picture()
    provider = FakeProvider({"blocks": [block(), block("Entry", "Вхід", x=200)]})
    result = translate_image(provider, image, "en", "uk")
    assert result.image == image
    assert result.translated_text == "Ласкаво просимо\n\nВхід"
    assert len(result.warnings) == 1
    assert "перекривні" in result.warnings[0]


def test_exactly_unchanged_codes_preserve_original_pixels(qapp):
    image = picture()
    provider = FakeProvider({"blocks": [block("AB-120", "AB-120")]})
    result = translate_image(provider, image, "en", "uk")
    assert result.image == image
    assert result.warnings == []


def test_changed_numbers_and_identifiers_get_one_bounded_repair(qapp):
    provider = FakeProvider(
        {"blocks": [block("Pump AB-120: 25 kW", "Насос AB-121: 26 кВт"), block("Power 5kW", "Потужність 5MW", y=650, height=250)]},
        {"translations": [{"id": 0, "translation": "Насос AB-120: 25 кВт"}, {"id": 1, "translation": "Потужність 5кВт"}]},
    )
    result = translate_image(provider, picture(), "en", "uk")
    assert result.translated_text == "Насос AB-120: 25 кВт\n\nПотужність 5кВт"
    assert len(provider.calls) == 2
    assert provider.calls[1][3] == {}
    assert provider.calls[1][1]["blocks"][0]["source"] == "Pump AB-120: 25 kW"
    assert provider.calls[1][1]["blocks"][1]["source"] == "Power 5kW"
    provider = FakeProvider(
        {"blocks": [block("Power 5kW", "Потужність 5MW")]},
        {"translations": [{"id": 0, "translation": "Потужність 5MWh"}]},
    )
    with pytest.raises(FormaError, match="одиниці вимірювання"):
        translate_image(provider, picture(), "en", "uk")
    assert len(provider.calls) == 2


@pytest.mark.parametrize("correction", [
    {}, {"translations": []},
    {"translations": [{"id": 0, "translation": "Насос AB-120: 26 кВт"}]},
    {"translations": [{"id": False, "translation": "Насос AB-120: 25 кВт"}]},
    {"translations": [{"id": 1, "translation": "Насос AB-120: 25 кВт"}]},
    {"translations": [{"id": 0, "translation": ""}]},
])
def test_failed_repair_never_returns_corrupted_image(qapp, correction):
    provider = FakeProvider(
        {"blocks": [block("Pump AB-120: 25 kW", "Насос AB-120: 26 кВт")]}, correction
    )
    with pytest.raises(FormaError, match="числа та коди"):
        translate_image(provider, picture(), "en", "uk")
    assert len(provider.calls) == 2


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 1001, True, "100", None, 10 ** 400])
def test_untrusted_coordinates_must_be_finite_numbers_in_bounds(value):
    with pytest.raises(FormaError, match="межі тексту"):
        _parse_blocks({"blocks": [block(x=value)]})


@pytest.mark.parametrize("coords", [{"width": 0}, {"height": -1}, {"x": 900}, {"y": 900}])
def test_positive_rectangles_cannot_extend_beyond_image(coords):
    with pytest.raises(FormaError, match="межі тексту"):
        _parse_blocks({"blocks": [block(**coords)]})


@pytest.mark.parametrize("response", [
    {}, {"blocks": None}, {"blocks": {}}, {"blocks": [None]}, {"blocks": [True]},
    {"blocks": [block()] * (MAX_BLOCKS + 1)},
    {"blocks": [block()], "extra": "unexpected"},
    {"blocks": [{**block(), "action": "https://example.invalid"}]},
])
def test_untrusted_structure_is_validated(response):
    with pytest.raises(FormaError, match="опис зображення"):
        _parse_blocks(response)


@pytest.mark.parametrize("value", ["", "\t\n", None, 12, True, "A" * 4001, "a\x00b", "a\x1bb"])
def test_untrusted_text_has_bounds_and_no_control_characters(value):
    with pytest.raises(FormaError, match="текст зображення"):
        _parse_blocks({"blocks": [block(translation=value)]})


def test_total_image_text_is_bounded():
    with pytest.raises(FormaError, match="забагато тексту"):
        _parse_blocks({"blocks": [block(source="A" * 4000)] * 6})


def test_no_text_is_clear_error_without_fabricated_result(qapp):
    provider = FakeProvider({"blocks": []})
    with pytest.raises(FormaError, match="не знайдено читабельного тексту"):
        translate_image(provider, picture(), "auto", "uk")


@pytest.mark.parametrize("source,target", [("", "uk"), (None, "uk"), ("en", "auto"), ("en", "en")])
def test_invalid_languages_never_send_images(qapp, source, target):
    provider = FakeProvider()
    with pytest.raises(FormaError):
        translate_image(provider, picture(), source, target)
    assert provider.calls == []


def test_invalid_or_excessive_images_are_rejected_before_network(qapp):
    provider = FakeProvider()
    with pytest.raises(FormaError, match="прочитати"):
        translate_image(provider, QImage(), "en", "uk")
    # Thin images exercise the independent maximum-edge limit without large allocations.
    with pytest.raises(FormaError, match="завелике"):
        validate_image(picture(20001, 1))
    image = picture(5000, 5000)
    assert image.width() * image.height() > MAX_IMAGE_PIXELS
    with pytest.raises(FormaError, match="завелике"):
        translate_image(provider, image, "en", "uk")
    assert provider.calls == []


def test_layout_preserves_line_breaks_and_plain_text_markup(qapp):
    layout = _text_layout("<b>First</b>\nДругий рядок", 14, 180, 100)
    assert layout is not None
    assert layout.text() == "<b>First</b>\u2028Другий рядок"
    assert layout.lineCount() == 2
    assert _text_layout("A" * 300, 14, 20, 15) is None


def test_normalized_rectangle_mapping_uses_original_resolution():
    region = ImageBlock("A", "B", 100, 200, 700, 400)
    assert region.rectangle(4000, 2000) == QRect(400, 400, 2800, 800)
    assert ImageBlock("A", "B", 999.5, 999.5, 0.5, 0.5).rectangle(400, 200) == QRect(399, 199, 1, 1)


def test_cancelled_job_does_not_start_a_request(qapp):
    provider = FakeProvider()
    provider.control.cancelled.set()
    with pytest.raises(Cancelled):
        translate_image(provider, picture(), "en", "uk")
    assert provider.calls == []


def test_cancellation_after_vision_prevents_rendering(qapp):
    provider = FakeProvider({"blocks": [block()]})

    def progress(percent, message):
        if percent == 75:
            provider.control.cancelled.set()

    with pytest.raises(Cancelled):
        translate_image(provider, picture(), "en", "uk", progress=progress)
    assert len(provider.calls) == 1


def test_custom_instructions_reach_image_recognition_and_repair(qapp):
    provider = FakeProvider(
        {"blocks": [block("Power 25 kW", "Потужність 35 кВт")]},
        {"translations": [{"id": 0, "translation": "Потужність 25 кВт"}]},
    )
    provider.settings = Settings(translation_instructions="Use formal technical language.")
    result = translate_image(provider, picture(), "en", "uk")
    assert result.translated_text == "Потужність 25 кВт"
    assert len(provider.calls) == 2
    assert all('"translation_preferences": "Use formal technical language."' in call[0] for call in provider.calls)


def _draw_label(image, text, x, baseline, size=20, color="white"):
    font = QFont("Sans Serif")
    font.setPixelSize(size)
    painter = QPainter(image)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(x, baseline, text)
    painter.end()
    rect = QFontMetrics(font).tightBoundingRect(text).translated(x, baseline)
    return TextLine((TextWord(text, rect),))


def test_local_word_anchors_correct_displaced_tiny_vision_boxes_and_fit_readable_text(qapp, monkeypatch):
    image = picture(620, 260, color="#161b22")
    heading = _draw_label(image, "Community standards", 20, 50, 28)
    label = _draw_label(image, "Code of conduct", 28, 145, 19, "#58a6ff")
    neighbour = _draw_label(image, "Security policy", 28, 215, 19, "#58a6ff")
    lines = [heading, label, neighbour]
    monkeypatch.setattr("forma.translation.images.detect_text", lambda *_: lines)
    # Wrong positions and nine-pixel heights reproduce the reported symptom.
    provider = FakeProvider({"blocks": [
        block("Community standards", "Стандарти спільноти", x=35, y=100, width=350, height=35),
        block("Code of conduct", "Кодекс поведінки спільноти", x=50, y=350, width=160, height=35),
        block("Security policy", "Security policy", x=50, y=740, width=260, height=35),
    ]})
    original = image.copy()
    result = translate_image(provider, image, "en", "uk")
    assert image == original
    assert result.warnings == []
    assert len(provider.calls) == 1
    assert provider.calls[0][1]["local_text_regions"][1]["text"] == "Code of conduct"
    for index, line in enumerate(lines[:2]):
        painted = result.blocks[index].rectangle(image.width(), image.height())
        assert painted.contains(line.rectangle)
        assert painted.height() >= line.rectangle.height()
    # Translation uses adjacent blank space instead of an eight-pixel font.
    label_bounds = result.blocks[1].rectangle(image.width(), image.height())
    assert label_bounds.width() > label.rectangle.width()
    assert not label_bounds.intersects(neighbour.rectangle)
    ink_rows = {y for y in range(label_bounds.top(), label_bounds.bottom() + 1)
                for x in range(label_bounds.left(), label_bounds.right() + 1)
                if result.image.pixelColor(x, y).blue() > 130}
    assert max(ink_rows) - min(ink_rows) >= label.rectangle.height() * 0.65
    bounds = [item.rectangle(image.width(), image.height()) for item in result.blocks[:2]]
    for y in range(image.height()):
        for x in range(image.width()):
            if not any(rect.contains(x, y) for rect in bounds):
                assert result.image.pixelColor(x, y) == image.pixelColor(x, y)


def test_local_anchors_resolve_model_overlap_and_preserve_neighbouring_artwork(qapp):
    image = picture(420, 180, color="#161b22")
    one = _draw_label(image, "Description", 20, 45, 18)
    two = _draw_label(image, "Contributing", 20, 125, 18)
    painter = QPainter(image)
    painter.fillRect(QRect(230, 0, 2, 180), QColor("#58a6ff"))
    painter.end()
    blocks = [ImageBlock(**block("Description", "Дуже докладний опис спільноти", x=40, y=100, width=700, height=500)),
              ImageBlock(**block("Contributing", "Участь у розробці", x=40, y=300, width=700, height=500))]
    result, warnings = _render(image, blocks, lambda: None, lines=[one, two])
    assert not warnings
    for item in blocks:
        assert item.rectangle(420, 180).right() < 230
    assert not blocks[0].rectangle(420, 180).intersects(blocks[1].rectangle(420, 180))
    assert all(result.pixelColor(230, y) == image.pixelColor(230, y) for y in range(180))


def test_unrelated_local_ocr_does_not_move_a_vision_label(qapp):
    image = picture(400, 200)
    line = _draw_label(image, "Another label", 20, 50, 18, "black")
    item = ImageBlock(**block("Power output", "Вихідна потужність", x=500, y=500, width=450, height=350))
    blocks = [item]
    result, warnings = _render(image, blocks, lambda: None, lines=[line])
    assert not warnings
    assert result.copy(line.rectangle) == image.copy(line.rectangle)
    assert blocks[0].rectangle(400, 200) == item.rectangle(400, 200)
