"""One-shot PDF rasterization worker; never imported by the desktop renderer."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path


def render_pdf(source: Path, output: Path, page: int, width: int) -> dict:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtPdf import QPdfDocument

    app = QGuiApplication.instance() or QGuiApplication([])
    document = QPdfDocument()
    error = document.load(str(source))
    if error == QPdfDocument.Error.IncorrectPassword:
        return {"error": "password"}
    if error != QPdfDocument.Error.None_ or document.status() != QPdfDocument.Status.Ready:
        return {"error": "invalid_pdf"}
    count = document.pageCount()
    if count < 1 or count > 10000:
        return {"error": "page_limit"}
    if page >= count:
        return {"page_count": count, "page_index": page, "width": 0, "height": 0}
    size = document.pagePointSize(page)
    if not all(math.isfinite(value) and value > 0 for value in (size.width(), size.height())):
        return {"error": "invalid_pdf"}
    width = max(128, min(2400, width))
    height = max(1, round(width * size.height() / size.width()))
    if height > 4000:
        width, height = max(1, round(width * 4000 / height)), 4000
    image = document.render(page, QSize(width, height))
    if image.isNull():
        return {"error": "render_failed"}
    # PDF pages may have no painted background; preview them as paper in either theme.
    opaque = QImage(image.size(), QImage.Format.Format_RGB32)
    opaque.fill(QColor("white"))
    painter = QPainter(opaque)
    painter.drawImage(0, 0, image)
    painter.end()
    if not opaque.save(str(output), "PNG"):
        return {"error": "render_failed"}
    document.close()
    del document
    del app
    return {"page_count": count, "page_index": page, "width": width, "height": height}


def main(arguments: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    args = parser.parse_args(arguments)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    if args.page < 0 or not 128 <= args.width <= 2400:
        return 2
    try:
        # Bound CPU work. PDFium reserves a large virtual address range; an AS
        # limit would reject healthy files. The parent monitors resident memory.
        if sys.platform.startswith("linux"):
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        result = render_pdf(args.source, args.output, args.page, args.width)
    except Exception:
        result = {"error": "render_failed"}
    args.metadata.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
