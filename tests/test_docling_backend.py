from __future__ import annotations

import importlib
import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "tools/ocr/src"))
    return importlib.import_module("forma_ocr.docling_backend")


def test_column_proportions_keep_narrow_number_and_wide_description(adapter):
    cells = [
        SimpleNamespace(start_col_offset_idx=index, end_col_offset_idx=index + 1,
                        bbox=SimpleNamespace(l=left, r=right))
        for index, left, right in ((0, 5, 15), (1, 35, 75), (2, 100, 290))
    ]
    # A merged total row must not distort individual column proportions.
    cells.append(SimpleNamespace(start_col_offset_idx=0, end_col_offset_idx=3,
                                 bbox=SimpleNamespace(l=2, r=295)))
    table = SimpleNamespace(data=SimpleNamespace(num_cols=3, table_cells=cells))
    widths = adapter.column_widths(table, 0, 300)
    assert widths == pytest.approx([25, 62.5, 212.5])
    assert sum(widths) == pytest.approx(300)
    table.data.table_cells = cells[:1]
    assert adapter.column_widths(table, 0, 300) is None


class Box:
    def __init__(self, left, top, right, bottom):
        self.l, self.t, self.r, self.b = left, top, right, bottom

    def to_top_left_origin(self, height):
        return self

    def to_bounding_box(self):
        return self


def test_adapter_keeps_list_markers_headers_pictures_and_raw_ocr(adapter, monkeypatch):
    class Layer(Enum):
        BODY = "body"
        FURNITURE = "furniture"
        INVISIBLE = "invisible"

    monkeypatch.setitem(sys.modules, "docling_core.types.doc.document", SimpleNamespace(ContentLayer=Layer))
    provenance = SimpleNamespace(page_no=7, bbox=Box(10, 20, 80, 40))

    def item(label, text="", **extra):
        return SimpleNamespace(label=SimpleNamespace(value=label), text=text, prov=[provenance], **extra)

    items = [item("list_item", "Keep the negative amount -100.", marker="3.1"),
             item("page_header", "Contract 42"), item("picture")]
    requested = {}

    def iterate(**kwargs):
        requested.update(kwargs)
        return [(value, 0) for value in items]

    document = SimpleNamespace(iterate_items=iterate)
    parsed = SimpleNamespace(cells=[SimpleNamespace(text="Keep -100", confidence=float("nan"), rect=Box(12, 22, 70, 35))])
    result = adapter.adapt_page(document, parsed, SimpleNamespace(width=200, height=400),
                                index=6, page_no=7, width=100, height=200)
    assert result.index == 6
    assert result.blocks[0]["text"] == "3.1 Keep the negative amount -100."
    assert result.blocks[1]["label"] == "header"
    assert result.blocks[2]["label"] == "image"
    assert result.blocks[0]["bbox"] == [20, 40, 160, 80]
    assert Layer.FURNITURE in requested["included_content_layers"]
    assert Layer.INVISIBLE not in requested["included_content_layers"]
    assert result.recognized_texts == ["Keep -100"]
    assert result.polygons == [[[24, 44], [140, 44], [140, 70], [24, 70]]]
    assert result.scores == [0.0]
    assert result.stats["low_confidence_lines"] == 1


def test_unsupported_language_fails_before_optional_runtime_import(adapter):
    with pytest.raises(ValueError, match="supported source language"):
        next(adapter.iter_pages(Path("unused.pdf"), "auto"))
