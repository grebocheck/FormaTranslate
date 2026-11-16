"""The shipped Qt PDF notices remain traceable to its separate source archive."""

import hashlib
import json
from pathlib import Path


def test_pdf_notices_match_the_independent_corresponding_source_inventory():
    root = Path(__file__).resolve().parents[1] / "third_party"
    sources = json.loads((root / "SOURCES.json").read_text())["sources"]
    source = next(entry for entry in sources if entry["component"] == "QtPDF")
    assert source["archive"] == f"qtwebengine-everywhere-src-{source['version']}.tar.xz"
    assert source["modified"] is False
    entries = [entry for entry in json.loads((root / "NOTICE_PROVENANCE.json").read_text())
               if entry["file"].startswith("licenses/Qt/qtwebengine/")]
    assert entries
    for entry in entries:
        assert entry["archive"] == source["archive"]
        assert entry["archive_sha256"] == source["sha256"]
        assert entry["url"] == source["url"]
        assert entry["archive_member"].startswith(source["archive"].removesuffix(".tar.xz") + "/")
        assert hashlib.sha256((root / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
    files = {entry["file"].removeprefix("licenses/Qt/qtwebengine/") for entry in entries}
    assert {"LICENSES/LGPL-3.0-only.txt", "LICENSES/GPL-3.0-only.txt",
            "src/3rdparty/chromium/LICENSE",
            "src/3rdparty/chromium/third_party/pdfium/LICENSE",
            "src/3rdparty/chromium/third_party/freetype/src/docs/FTL.TXT",
            "src/3rdparty/chromium/third_party/libjpeg_turbo/README.ijg"} <= files
