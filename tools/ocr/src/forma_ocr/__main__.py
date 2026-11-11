from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from forma_ocr import __version__

LANGUAGES = frozenset({"en", "uk", "pl", "de"})
MAX_PAGES = 200
MAX_PAGE_PIXELS = 25_000_000
MAX_RENDER_EDGE = 6000
MAX_INPUT_BYTES = 200 * 1024 * 1024


def emit(value: int, stage: str, **fields) -> None:
    print(json.dumps({"type": "progress", "value": value, "stage": stage, **fields}), flush=True)


def publish_exclusive(source: Path, destination: Path) -> None:
    """Publish complete bytes atomically, refusing even a racing destination."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".forma-ocr-", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recover_ocr_lines(blocks: list[dict], texts, polygons) -> int:
    from forma_ocr.writer import flatten_table, text_tokens

    pool = text_tokens("\n".join(
        flatten_table(block["text"]) if block["label"] == "table" else block["text"]
        for block in blocks if block["label"] not in {
            "image", "seal", "chart", "formula", "header_image", "footer_image"
        }
    ))
    recovered = 0
    for text, polygon in zip(texts, polygons, strict=True):
        if not text.strip():
            continue
        points = list(polygon)
        xs, ys = [point[0] for point in points], [point[1] for point in points]
        bbox = [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]
        tokens = text_tokens(text)
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        local = text_tokens("\n".join(
            flatten_table(block["text"]) if block["label"] == "table" else block["text"]
            for block in blocks
            if block["label"] not in {"image", "seal", "chart", "formula", "header_image", "footer_image"}
            and block["bbox"][0] <= cx <= block["bbox"][2]
            and block["bbox"][1] <= cy <= block["bbox"][3]
        ))
        if not tokens - pool and not tokens - local:
            pool.subtract(tokens)
            continue
        fallback = {"label": "recovered_text", "text": text, "bbox": bbox}
        # Keep the model's reading order; insert an uncovered line before the
        # first block starting below it, avoiding a global multi-column resort.
        index = next((i for i, block in enumerate(blocks) if block["bbox"][1] > bbox[1]), len(blocks))
        blocks.insert(index, fallback)
        recovered += 1
    return recovered


def highlight_uncertain_regions(blocks, scores, polygons):
    """Highlight low confidence editable content without duplicating source text."""
    graphics = {"image", "seal", "chart", "formula", "header_image", "footer_image"}
    for score, polygon in zip(scores, polygons, strict=True):
        if score >= 0.8:
            continue
        points = list(polygon)
        cx = (min(point[0] for point in points) + max(point[0] for point in points)) / 2
        cy = (min(point[1] for point in points) + max(point[1] for point in points)) / 2
        for block in blocks:
            if block["label"] not in graphics:
                x0, y0, x1, y1 = block["bbox"]
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    block["uncertain"] = True


def expand_native_graphics(blocks, image):
    """Extend existing figure crops to retain overlapping colored seal ink.

    Layout models can crop the bottom of a seal. This only expands an existing
    graphic; it never appends images or duplicates low confidence printed text.
    """
    import cv2
    import numpy as np

    graphics = [block for block in blocks if block["label"] in {"image", "seal", "chart", "header_image", "footer_image"}]
    if not graphics:
        return
    pixels = np.asarray(image)
    maximum, minimum = pixels.max(axis=2), pixels.min(axis=2)
    colored = ((maximum.astype(np.int16) - minimum.astype(np.int16) > 45) & (minimum < 220)).astype(np.uint8)
    if int(colored.sum()) < 200:
        return
    grouped = cv2.dilate(colored, np.ones((9, 9), dtype=np.uint8), iterations=1)
    count, _labels, regions, _centers = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    for index in range(1, count):
        x, y, width, height, _area = [int(value) for value in regions[index]]
        if width < 30 or height < 30 or int(colored[y:y+height, x:x+width].sum()) < 200:
            continue
        component = [x, y, x + width, y + height]
        for block in graphics:
            x0, y0, x1, y1 = block["bbox"]
            intersection = max(0, min(x1, component[2]) - max(x0, component[0])) * max(0, min(y1, component[3]) - max(y0, component[1]))
            smaller_area = max(1, min(width * height, (x1-x0) * (y1-y0)))
            if intersection / smaller_area < 0.1:
                continue
            expanded = [max(0, min(x0, x) - 6), max(0, min(y0, y) - 6), min(image.width, max(x1, x+width) + 6), min(image.height, max(y1, y+height) + 6)]
            if expanded != block["bbox"]:
                block["bbox"] = expanded
                block["graphic_crop_expanded"] = True


def convert(source: Path, output: Path, language: str, diagnostics: Path | None = None) -> dict:
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise ValueError("Input must be an existing PDF file")
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("PDF exceeds the 200 MB input limit")
    if output.suffix.lower() != ".docx":
        raise ValueError("Output must have a .docx extension")
    manifest_path = output.with_suffix(".ocr.json")
    if output.exists() or manifest_path.exists():
        raise ValueError("Output already exists; choose a new name")
    if language not in LANGUAGES:
        raise ValueError("Select an explicit supported source language: en, uk, pl, de")
    if diagnostics is not None:
        diagnostics.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    emit(1, "loading")
    with contextlib.redirect_stdout(sys.stderr):
        import pypdfium2 as pdfium

        from forma_ocr.docling_backend import iter_pages
        from forma_ocr.writer import DocumentWriter

        pdf = pdfium.PdfDocument(str(source))
        try:
            page_count = len(pdf)
            if not 1 <= page_count <= MAX_PAGES:
                raise ValueError("PDF must contain between 1 and 200 pages")
            for index in range(page_count):
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    pixels = width * height * (300 / 72) ** 2
                    if pixels > MAX_PAGE_PIXELS or max(width, height) * 300 / 72 > MAX_RENDER_EDGE:
                        raise ValueError(f"Page {index + 1} exceeds the render limit")
                finally:
                    page.close()
        finally:
            pdf.close()
        writer = DocumentWriter()
    pages = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forma-ocr-") as temporary:
        root = Path(temporary)
        emit(5, "recognizing", pages=page_count)
        iterator = iter(iter_pages(source, language))
        while True:
            # Redirect all model diagnostics, including generator execution, to
            # the parent's private log. Stdout remains the progress protocol.
            with contextlib.redirect_stdout(sys.stderr):
                page = next(iterator, None)
            if page is None:
                break
            index = len(pages)
            if page.index != index or index >= page_count:
                raise ValueError("Unexpected OCR source-page sequence")
            emit(5 + int(85 * index / page_count), "recognizing", page=index + 1, pages=page_count)
            blocks, image = page.blocks, page.image
            with contextlib.redirect_stdout(sys.stderr):
                highlight_uncertain_regions(blocks, page.scores, page.polygons)
                from forma_ocr.text_fallback import repair_uncertain_prose

                secondary = repair_uncertain_prose(page, language)
                recovered = recover_ocr_lines(blocks, page.recognized_texts, page.polygons)
                expand_native_graphics(blocks, image)
                if diagnostics is not None:
                    diagnostic = {
                        "schema_version": 1,
                        "index": index + 1,
                        "blocks": blocks,
                        "recognized_texts": page.recognized_texts,
                        "scores": page.scores,
                        "polygons": page.polygons,
                        "secondary_ocr_alternatives": secondary.alternatives,
                    }
                    (diagnostics / f"page-{index+1:03d}.json").write_text(json.dumps(diagnostic, ensure_ascii=False), encoding="utf-8")
                    image.save(diagnostics / f"page-{index+1:03d}.png")
                from PIL import ImageStat

                blank = ImageStat.Stat(image.convert("L")).extrema[0][0] >= 250
                metadata = writer.page(
                    blocks, width=page.width, height=page.height, image=image,
                    recognized_texts=page.recognized_texts, blank=blank,
                )
            metadata.update(page.stats)
            metadata["recovered_lines"] = recovered
            metadata["secondary_ocr_blocks"] = secondary.corrected
            metadata["secondary_ocr_attempted"] = secondary.attempted
            if secondary.corrected:
                metadata["warnings"].append("secondary_ocr_used")
            if secondary.unavailable:
                metadata["warnings"].append("secondary_ocr_unavailable")
            if secondary.rejected:
                metadata["warnings"].append("secondary_ocr_rejected")
            metadata["width_points"], metadata["height_points"] = page.width, page.height
            if page.stats.get("low_confidence_lines", 0):
                metadata["warnings"].append("low_ocr_confidence")
            if recovered:
                metadata["warnings"].append("unassigned_ocr_lines_recovered")
            pages.append(metadata)
        if len(pages) != page_count:
            raise ValueError("OCR did not return every source page")
        emit(93, "exporting")
        temporary_docx = root / "document.docx"
        writer.save(temporary_docx)
        emit(97, "validating")
        from docx import Document
        from docx.oxml.ns import qn

        document = Document(temporary_docx)
        anchors = document._element.findall(".//" + qn("w:bookmarkStart"))
        names = [node.get(qn("w:name")) for node in anchors]
        if names != [f"forma_ocr_page_{i}" for i in range(1, page_count + 1)]:
            raise ValueError("DOCX export lost source-page boundaries")
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_digest:
            raise ValueError("Source PDF changed during conversion")
        manifest = {
            "schema_version": 1,
            "engine": "docling-rapidocr",
            "worker_version": __version__,
            "versions": {name: version(name) for name in ("docling", "docling-core", "rapidocr", "onnxruntime")},
            "source_sha256": source_digest,
            "output_sha256": hashlib.sha256(temporary_docx.read_bytes()).hexdigest(),
            "source_language": language,
            "page_count": page_count,
            "pages": pages,
            "quality": "needs_review",
            "warnings": ["ocr_requires_source_review", "reflowed_layout", "cross_page_tables_not_joined"],
        }
        temporary_manifest = root / "document.ocr.json"
        temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        published = []
        try:
            for src, destination in ((temporary_docx, output), (temporary_manifest, manifest_path)):
                publish_exclusive(src, destination)
                published.append(destination)
        except BaseException:
            for destination in published:
                destination.unlink(missing_ok=True)
            raise
    emit(100, "complete", pages=page_count)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local scanned PDF to editable DOCX; review OCR before translation")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-language", required=True, choices=sorted(LANGUAGES))
    parser.add_argument("--diagnostics", type=Path, help="Explicitly save private per-page OCR JSON and source images for debugging")
    args = parser.parse_args(argv)
    try:
        convert(args.input.resolve(), args.output.resolve(), args.source_language, args.diagnostics)
    except Exception as exc:
        # No source text, credentials, or provider errors enter the stdout protocol.
        # Detailed local diagnostics remain in the parent's private stderr log.
        print(f"OCR failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        if isinstance(exc, ImportError):
            code = "dependencies_missing"
        elif isinstance(exc, ValueError) and "No editable text" in str(exc):
            code = "empty_page"
        elif isinstance(exc, ValueError) and "DOCX export" in str(exc):
            code = "export_failed"
        else:
            code = "recognition_failed"
        print(json.dumps({"type": "error", "code": code}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
