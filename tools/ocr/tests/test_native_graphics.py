"""Optional-engine checks for source graphic crops, using actual image pixels."""

from forma_ocr.__main__ import expand_native_graphics, highlight_uncertain_regions
from PIL import Image, ImageDraw


def test_uncertain_printed_text_only_adds_highlight():
    blocks = [{"label": "text", "text": "Amount 100", "bbox": [20, 20, 250, 100]}]
    highlight_uncertain_regions(blocks, [0.4], [[[20, 20], [250, 20], [250, 60], [20, 60]]])
    assert len(blocks) == 1 and blocks[0]["uncertain"]


def test_native_graphic_crop_expands_to_keep_whole_blue_seal():
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((400, 700, 560, 900), outline="blue", width=5)
    blocks = [{"label": "image", "bbox": [390, 690, 570, 820]}]
    expand_native_graphics(blocks, image)
    assert len(blocks) == 1
    assert blocks[0]["bbox"][3] >= 900
    assert blocks[0]["graphic_crop_expanded"]


def test_printed_text_page_never_gains_supplemental_images():
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((400, 700, 560, 820), outline="blue", width=5)
    draw.line((40, 400, 500, 420), fill="black", width=5)
    blocks = [{"label": "table", "text": "<table><tr><td>100</td></tr></table>", "bbox": [30, 30, 600, 300]}]
    expand_native_graphics(blocks, image)
    assert len(blocks) == 1 and blocks[0]["label"] == "table"
