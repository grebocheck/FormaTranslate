"""One-shot PDF rasterization worker; never imported by the desktop renderer."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

MAX_PAGE_PIXELS = 4_000_000


def _open_pdf(source: Path):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtPdf import QPdfDocument

    app = QGuiApplication.instance() or QGuiApplication([])
    document = QPdfDocument()
    error = document.load(str(source))
    if error == QPdfDocument.Error.IncorrectPassword:
        return app, document, {"error": "password"}
    if error != QPdfDocument.Error.None_ or document.status() != QPdfDocument.Status.Ready:
        return app, document, {"error": "invalid_pdf"}
    if not 1 <= document.pageCount() <= 10000:
        return app, document, {"error": "page_limit"}
    return app, document, None


def _page_size(document, page):
    size = document.pagePointSize(page)
    values = size.width(), size.height()
    return values if all(math.isfinite(value) and 0 < value <= 10_000_000 for value in values) else None


def describe_pdf(source: Path) -> dict:
    """Read page geometry without allocating or rendering any page image."""
    app, document, error = _open_pdf(source)
    try:
        if error:
            return error
        sizes = [_page_size(document, page) for page in range(document.pageCount())]
        if any(size is None for size in sizes):
            return {"error": "invalid_pdf"}
        return {"page_count": document.pageCount(), "page_sizes": sizes}
    finally:
        document.close()
        del document
        del app


def render_pdf(source: Path, output: Path, page: int, width: int) -> dict:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QImage, QPainter

    app, document, error = _open_pdf(source)
    try:
        if error:
            return error
        count = document.pageCount()
        if page >= count:
            return {"page_count": count, "page_index": page, "width": 0, "height": 0}
        size = _page_size(document, page)
        if size is None:
            return {"error": "invalid_pdf"}
        width = max(128, min(2400, width))
        height = max(1, round(width * size[1] / size[0]))
        scale = min(1, 4000 / height, math.sqrt(MAX_PAGE_PIXELS / (width * height)))
        width, height = max(1, int(width * scale)), max(1, int(height * scale))
        image = document.render(page, QSize(width, height))
        if image.isNull():
            return {"error": "render_failed"}
        # PDF pages may have no painted background; show paper in either UI theme.
        opaque = QImage(image.size(), QImage.Format.Format_RGB32)
        opaque.fill(QColor("white"))
        painter = QPainter(opaque)
        painter.drawImage(0, 0, image)
        painter.end()
        if not opaque.save(str(output), "PNG"):
            return {"error": "render_failed"}
        return {"page_count": count, "page_index": page, "width": width, "height": height}
    finally:
        document.close()
        del document
        del app


def main(arguments: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--width", type=int, default=1000)
    args = parser.parse_args(arguments)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    if args.page < 0 or not 128 <= args.width <= 2400 or not args.describe and args.output is None:
        return 2
    try:
        # Bound CPU work. PDFium reserves a large virtual address range; an AS
        # limit would reject healthy files. The parent monitors resident memory.
        if sys.platform.startswith("linux"):
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        result = describe_pdf(args.source) if args.describe else render_pdf(args.source, args.output, args.page, args.width)
    except Exception:
        result = {"error": "render_failed"}
    args.metadata.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
