from __future__ import annotations

import base64
from dataclasses import asdict

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from forma.image_translation import (
    MAX_BLOCKS,
    MAX_IMAGE_PIXELS,
    MAX_UPLOAD_EDGE,
    MAX_UPLOAD_PIXELS,
    ImageBlock,
    _image_payload,
    _parse_blocks,
    _text_layout,
    translate_image,
    validate_image,
)
from forma.models import FormaError
from forma.provider import Cancelled, JobControl


class FakeProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
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
        {"blocks": [block("Pump AB-120: 25 kW", "Насос AB-121: 26 кВт")]},
        {"translations": [{"id": 0, "translation": "Насос AB-120: 25 кВт"}]},
    )
    result = translate_image(provider, picture(), "en", "uk")
    assert result.translated_text == "Насос AB-120: 25 кВт"
    assert len(provider.calls) == 2
    assert provider.calls[1][3] == {}
    assert provider.calls[1][1]["blocks"][0]["source"] == "Pump AB-120: 25 kW"


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
