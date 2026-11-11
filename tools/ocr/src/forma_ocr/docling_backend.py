"""Local Docling layout/TableFormer and language-specific RapidOCR adapter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LANGUAGES = frozenset({"en", "uk", "pl", "de"})
SCALE = 300 / 72
MAX_PAGE_PIXELS = 25_000_000
MAX_RENDER_EDGE = 6000


@dataclass
class RecognitionPage:
    index: int
    blocks: list[dict]
    image: Any
    stats: dict
    recognized_texts: list[str]
    scores: list[float]
    polygons: list[list[list[float]]]
    width: float
    height: float


def _pixel_bbox(box, width: float, height: float, image) -> list[float]:
    box = box.to_top_left_origin(height)
    sx, sy = image.width / width, image.height / height
    return [
        max(0.0, min(float(image.width), float(box.l) * sx)),
        max(0.0, min(float(image.height), float(box.t) * sy)),
        max(0.0, min(float(image.width), float(box.r) * sx)),
        max(0.0, min(float(image.height), float(box.b) * sy)),
    ]


def column_widths(table, left: float, right: float) -> list[float] | None:
    """Estimate column proportions from text extents and table outer edges.

    Cell boxes are OCR text bounds rather than the grid lines. Split the gaps
    between adjacent unmerged columns; never pretend these are exact rulings.
    """
    count = table.data.num_cols
    if not 1 <= count <= 100 or not right > left:
        return None
    starts: list[list[float]] = [[] for _ in range(count)]
    ends: list[list[float]] = [[] for _ in range(count)]
    for cell in table.data.table_cells:
        start, end = cell.start_col_offset_idx, cell.end_col_offset_idx
        if cell.bbox is not None and end == start + 1 and 0 <= start < count:
            starts[start].append(float(cell.bbox.l))
            ends[start].append(float(cell.bbox.r))
    edges = [left]
    for index in range(1, count):
        if not ends[index - 1] or not starts[index]:
            return None
        edge = (max(ends[index - 1]) + min(starts[index])) / 2
        if not math.isfinite(edge) or edge <= edges[-1] or edge >= right:
            return None
        edges.append(edge)
    edges.append(right)
    return [b - a for a, b in zip(edges, edges[1:], strict=False)]


def adapt_page(document, parsed_page, image, *, index: int, page_no: int, width: float, height: float) -> RecognitionPage:
    """Translate the public Docling document model into the worker's contract."""
    from docling_core.types.doc.document import ContentLayer

    blocks = []
    layers = set(ContentLayer) - {ContentLayer.INVISIBLE}
    for item, _level in document.iterate_items(page_no=page_no, included_content_layers=layers):
        provenance = next((value for value in item.prov if value.page_no == page_no), None)
        if provenance is None:
            continue
        label = item.label.value
        bbox = _pixel_bbox(provenance.bbox, width, height, image)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        block = {"bbox": bbox, "label": "text", "text": getattr(item, "text", "")}
        if label == "table":
            block.update(label="table", text=item.export_to_html(doc=document, add_caption=False))
            proportions = column_widths(item, float(provenance.bbox.l), float(provenance.bbox.r))
            if proportions:
                block["column_widths"] = proportions
        elif label in {"picture", "chart", "formula"}:
            block.update(label="image", text="")
        elif label == "list_item":
            marker = getattr(item, "marker", "")
            if marker:
                block["text"] = f"{marker} {block['text']}"
        elif label in {"title", "section_header"}:
            block["label"] = "doc_title" if label == "title" else "paragraph_title"
        elif label in {"page_header", "page_footer", "footnote"}:
            block["label"] = {"page_header": "header", "page_footer": "footer"}.get(label, label)
        blocks.append(block)

    texts, scores, polygons = [], [], []
    for cell in parsed_page.cells:
        if not cell.text.strip():
            continue
        bbox = _pixel_bbox(cell.rect.to_bounding_box(), width, height, image)
        texts.append(cell.text)
        score = float(cell.confidence)
        scores.append(score if math.isfinite(score) else 0.0)
        left, top, right, bottom = bbox
        polygons.append([[left, top], [right, top], [right, bottom], [left, bottom]])
    return RecognitionPage(
        index=index, blocks=blocks, image=image,
        stats={"recognized_lines": len(texts), "low_confidence_lines": sum(value < 0.8 for value in scores)},
        recognized_texts=texts, scores=scores, polygons=polygons, width=width, height=height,
    )


def iter_pages(source: Path, language: str):
    """Reuse one local model pipeline while releasing each processed source page."""
    if language not in LANGUAGES:
        raise ValueError("Select an explicit supported source language: en, uk, pl, de")
    import pypdfium2 as pdfium
    import torch
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import (
        OcrMode,
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    torch.set_num_threads(4)
    options = PdfPipelineOptions(
        do_ocr=True,
        ocr_options=RapidOcrOptions(lang=[f"iso:{language}"], mode=OcrMode.FULL_PAGE, scale=SCALE, backend="onnxruntime", text_score=0.0),
        do_table_structure=True,
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True),
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4),
        enable_remote_services=False,
        do_picture_description=False,
        do_picture_classification=False,
        do_code_enrichment=False,
        do_formula_enrichment=False,
        generate_page_images=True,
        generate_picture_images=True,
        generate_parsed_pages=True,
        images_scale=SCALE,
        document_timeout=600,
    )
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    pdf = pdfium.PdfDocument(str(source))
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                width, height = page.get_size()
            finally:
                page.close()
            if width * height * SCALE ** 2 > MAX_PAGE_PIXELS or max(width, height) * SCALE > MAX_RENDER_EDGE:
                raise ValueError(f"Page {index + 1} exceeds the render limit")
            # One page per conversion prevents a 200-page document retaining
            # hundreds of full-resolution image/parsed-page objects at once.
            result = converter.convert(source, page_range=(index + 1, index + 1))
            if result.status != ConversionStatus.SUCCESS or len(result.pages) != 1:
                raise ValueError(f"Incomplete OCR conversion on page {index + 1}")
            parsed = result.pages[0]
            if parsed.parsed_page is None:
                raise ValueError(f"OCR page data is missing on page {index + 1}")
            page_no = parsed.page_no
            page_image = result.document.pages[page_no].image
            if page_image is None:
                raise ValueError(f"OCR image is missing on page {index + 1}")
            recognized = adapt_page(result.document, parsed, page_image.pil_image.convert("RGB"), index=index, page_no=page_no, width=width, height=height)
            yield recognized
            del result, parsed, recognized
    finally:
        pdf.close()
