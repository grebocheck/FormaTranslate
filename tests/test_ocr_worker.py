"""Offline checks for the optional worker's editable Word reconstruction."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from lxml import etree
from PIL import Image


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools/ocr/src"))
    return importlib.import_module("forma_ocr.writer")


def test_real_merged_word_cells_and_unicode_are_retained(worker, tmp_path):
    document = Document()
    markup = (
        '<table><tr><th colspan="2">Об’єкт № 12</th><th rowspan="2">kWh</th></tr>'
        '<tr><td>A</td><td>B</td></tr><tr><td colspan="3">1.25<br/>Total</td></tr></table>'
    )
    assert worker.add_table(document, markup) == len("Об’єкт № 12kWhAB1.25\nTotal")
    path = tmp_path / "merged.docx"
    document.save(path)
    reopened = Document(path)
    table = reopened.tables[0]
    assert table.cell(0, 0)._tc is table.cell(0, 1)._tc
    assert table.cell(0, 2)._tc is table.cell(1, 2)._tc
    assert table.cell(2, 0)._tc is table.cell(2, 2)._tc
    assert table.cell(0, 0).text == "Об’єкт № 12"
    assert table.cell(2, 0).text == "1.25\nTotal"
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        assert root.xpath("//w:gridSpan/@w:val", namespaces=ns) == ["2", "3"]
        assert root.xpath("count(//w:vMerge)", namespaces=ns) == 2
        assert len(root.xpath("//w:t[text()='kWh']", namespaces=ns)) == 1


@pytest.mark.parametrize(
    "markup",
    [
        '<table><tr><td rowspan="2">A</td></tr></table>',
        '<table><tr><td colspan="101">A</td></tr></table>',
        '<table><tr><td rowspan="0">A</td></tr></table>',
        '<table><tr><td colspan="two">A</td></tr></table>',
        '<table><tr><td rowspan="2">A</td><td>B</td><td rowspan="2">C</td></tr>'
        '<tr><td colspan="2">overlap</td></tr></table>',
        '<table><tr><td><table><tr><td>nested</td></tr></table></td></tr></table>',
    ],
)
def test_invalid_table_geometry_is_not_silently_flattened(worker, markup):
    with pytest.raises(ValueError):
        worker.table_cells(markup)


def test_multipage_word_has_source_boundaries_page_sizes_and_graphics(worker, tmp_path):
    writer = worker.DocumentWriter()
    image = Image.new("RGB", (400, 600), "white")
    image.paste("black", (30, 30, 80, 70))
    first = writer.page(
        [
            {"label": "header", "text": "Header 001", "bbox": [0, 0, 400, 20]},
            {"label": "text", "text": "Original paragraph α 12.5", "bbox": [0, 30, 400, 100]},
            {"label": "seal", "bbox": [20, 20, 90, 80]},
        ],
        width=595, height=842, image=image,
    )
    second = writer.page(
        [{"label": "table", "text": '<table><tr><td>A 17</td><td>B</td></tr></table>', "bbox": [0, 0, 200, 200]}],
        width=842, height=595, image=image,
    )
    path = tmp_path / "combined.docx"
    writer.save(path)
    assert first["images"] == 1 and first["text_chars"] > 0
    assert "graphic_text_not_editable" in first["warnings"]
    assert second["tables"] == 1
    reopened = Document(path)
    assert len(reopened.sections) == 2 and len(reopened.inline_shapes) == 1
    assert abs(reopened.sections[0].page_width.pt - 595) < 1
    assert abs(reopened.sections[1].page_width.pt - 842) < 1
    assert reopened.tables[0].cell(0, 0).text == "A 17"
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        assert root.xpath("//w:bookmarkStart/@w:name", namespaces=ns) == [
            "forma_ocr_page_1", "forma_ocr_page_2"
        ]


def test_unrecognized_table_retains_image_and_text_with_explicit_warning(worker):
    writer = worker.DocumentWriter()
    metadata = writer.page(
        [{"label": "table", "text": '<table><tr><td colspan="0">A 109</td></tr></table>', "bbox": [0, 0, 100, 100]}],
        width=595, height=842, image=Image.new("RGB", (200, 300), "white"),
    )
    assert metadata["tables"] == 0 and metadata["images"] == 1
    assert "table_structure_unresolved" in metadata["warnings"]
    assert writer.document.paragraphs[-1].text == "A 109"


def test_empty_ocr_cannot_be_presented_as_editable_document(worker, tmp_path):
    writer = worker.DocumentWriter()
    with pytest.raises(ValueError, match="nonblank page"):
        writer.page([], width=595, height=842, image=Image.new("RGB", (200, 300), "white"))
    blank_writer = worker.DocumentWriter()
    metadata = blank_writer.page(
        [], width=595, height=842, image=Image.new("RGB", (200, 300), "white"), blank=True
    )
    assert metadata["status"] == "blank" and metadata["text_chars"] == 0
    with pytest.raises(ValueError, match="No editable text"):
        blank_writer.save(tmp_path / "blank.docx")


def test_ocr_words_inside_figures_or_missing_from_table_html_are_recovered(worker, tmp_path):
    main = importlib.import_module("forma_ocr.__main__")
    blocks = [
        {"label": "table", "text": "<table><tr><td>Amount 100</td></tr></table>", "bbox": [0, 0, 100, 100]},
        {"label": "image", "text": "", "bbox": [0, 120, 100, 220]},
    ]
    texts = ["Amount 100", "200 EUR", "Signature 17"]
    polygons = [
        [[0, 0], [90, 0], [90, 10], [0, 10]],
        [[0, 20], [90, 20], [90, 30], [0, 30]],
        [[0, 130], [90, 130], [90, 140], [0, 140]],
    ]
    assert main.recover_ocr_lines(blocks, texts, polygons) == 2
    writer = worker.DocumentWriter()
    metadata = writer.page(
        blocks, width=595, height=842, image=Image.new("RGB", (200, 300), "white"),
        recognized_texts=texts,
    )
    assert metadata["recognized_tokens"] == metadata["retained_tokens"] == 6
    path = tmp_path / "recovered.docx"
    writer.save(path)
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        assert root.xpath("count(//w:highlight)", namespaces=ns) == 2


def test_per_page_export_cannot_borrow_recognized_tokens_from_another_page(worker):
    writer = worker.DocumentWriter()
    image = Image.new("RGB", (200, 300), "white")
    writer.page([{"label": "text", "text": "Amount 100", "bbox": [0, 0, 100, 10]}], width=595, height=842, image=image)
    with pytest.raises(ValueError, match="lost recognized words on page 2"):
        writer.page(
            [{"label": "text", "text": "Only 10", "bbox": [0, 0, 100, 10]}],
            width=595, height=842, image=image, recognized_texts=["Amount 100"],
        )


def test_malformed_table_fallback_preserves_numeric_cell_boundaries(worker):
    assert worker.flatten_table(
        '<table><tr><td colspan="0">100</td><td>200</td></tr><tr><td>300</td></tr></table>'
    ) == "100\t200\n300"


@pytest.mark.parametrize("recognized,layout", [("Amount -100", "Amount 100"), ("Amount 1.00%", "Amount 1,00")])
def test_ocr_recovery_retains_numeric_signs_and_decimal_punctuation(worker, recognized, layout):
    main = importlib.import_module("forma_ocr.__main__")
    blocks = [{"label": "table", "text": f"<table><tr><td>{layout}</td></tr></table>", "bbox": [0, 0, 100, 100]}]
    assert main.recover_ocr_lines(blocks, [recognized], [[[0, 0], [90, 0], [90, 10], [0, 10]]]) == 1
    assert any(block["label"] == "recovered_text" and block["text"] == recognized for block in blocks)


def test_uncovered_ocr_line_does_not_consume_matching_words_from_another_region(worker):
    main = importlib.import_module("forma_ocr.__main__")
    blocks = [{"label": "text", "text": "Contact example.com", "bbox": [0, 100, 200, 200]}]
    texts = ["example.com", "Contact example.com"]
    polygons = [
        [[0, 0], [100, 0], [100, 20], [0, 20]],
        [[0, 110], [200, 110], [200, 150], [0, 150]],
    ]
    assert main.recover_ocr_lines(blocks, texts, polygons) == 1
    recovered = [block["text"] for block in blocks if block["label"] == "recovered_text"]
    assert recovered == ["example.com"]


def test_cli_validation_precedes_heavy_imports_and_never_overwrites(worker, tmp_path):
    main = importlib.import_module("forma_ocr.__main__")
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"not a PDF")
    output = tmp_path / "existing.docx"
    output.write_bytes(b"private existing document")
    before = hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="already exists"):
        main.convert(source, output, "en")
    with pytest.raises(ValueError, match="explicit supported"):
        main.convert(source, tmp_path / "new.docx", "auto")
    assert hashlib.sha256(output.read_bytes()).hexdigest() == before


def test_worker_publication_is_exclusive_and_complete(worker, tmp_path):
    main = importlib.import_module("forma_ocr.__main__")
    source = tmp_path / "prepared.docx"
    source.write_bytes(b"complete prepared bytes")
    destination = tmp_path / "new.docx"
    main.publish_exclusive(source, destination)
    assert destination.read_bytes() == source.read_bytes()
    source.write_bytes(b"other bytes")
    with pytest.raises(FileExistsError):
        main.publish_exclusive(source, destination)
    assert destination.read_bytes() == b"complete prepared bytes"
    assert not list(tmp_path.glob(".forma-ocr-*"))


def test_native_table_grid_uses_source_column_proportions(worker, tmp_path):
    document = Document()
    worker.add_table(
        document,
        "<table><tr><td>#</td><td>Description</td><td>Amount</td></tr></table>",
        column_widths=[1, 6, 2],
    )
    path = tmp_path / "widths.docx"
    document.save(path)
    reopened = Document(path)
    table = reopened.tables[0]
    widths = [column.width for column in table.columns]
    assert not table.autofit
    assert abs(widths[1] / widths[0] - 6) < 0.01
    assert abs(widths[2] / widths[0] - 2) < 0.01
