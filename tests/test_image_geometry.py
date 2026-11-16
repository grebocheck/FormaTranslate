from __future__ import annotations

import os
import sys
import time

import pytest
from PySide6.QtCore import QRect

from forma.core.models import Cancelled
from forma.translation import image_geometry
from forma.translation.image_geometry import TextLine, TextWord, _parse_tsv, _run, anchor_regions

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def line(text, left, top, width=80, height=15):
    return TextLine((TextWord(text, QRect(left, top, width, height)),))


def test_tsv_anchors_scale_words_to_original_and_reject_untrusted_bounds():
    rows = "5\t1\t1\t1\t1\t1\t10\t20\t25\t10\t96.2\tCode\n"
    rows += "5\t1\t1\t1\t1\t2\t40\t20\t50\t10\t95.6\tconduct\n"
    rows += "5\t1\t2\t1\t1\t1\t10\t60\t20\t10\t12\tnoise\n"
    result = _parse_tsv((HEADER + rows).encode(), 200, 100, 0.5)
    assert len(result) == 1
    assert result[0].text == "Code conduct"
    assert result[0].rectangle == QRect(20, 40, 160, 20)
    assert not _parse_tsv((HEADER + rows.replace("\t50\t10\t95.6", "\t500\t10\t95.6")).encode(), 200, 100, 0.5)
    assert not _parse_tsv(b"\xff", 200, 100, 1)


def test_anchor_matching_handles_multiline_and_repeated_labels_without_reusing_an_occurrence(qapp):
    lines = [line("Code", 20, 30), line("of conduct", 20, 50),
             line("Add", 220, 70, 30), line("Add", 220, 170, 30)]
    result = anchor_regions([
        ("Code of conduct", QRect(15, 15, 100, 20)),
        ("Add", QRect(215, 160, 40, 20)),
        ("Add", QRect(215, 65, 40, 20)),
        ("Unrelated source", QRect(15, 20, 100, 20)),
    ], lines, (400, 300))
    assert result[0].rectangle == QRect(20, 30, 80, 35)
    assert result[1].rectangle == lines[3].rectangle
    assert result[2].rectangle == lines[2].rectangle
    assert 3 not in result


def test_anchor_matching_rejects_ambiguous_or_distant_short_labels(qapp):
    assert not anchor_regions([("Add", QRect(20, 95, 30, 15))],
                              [line("Add", 20, 60, 30), line("Add", 20, 130, 30)], (400, 300))
    assert not anchor_regions([("Add", QRect(20, 10, 30, 15))], [line("Add", 350, 250, 30)], (400, 300))


def test_local_ocr_process_handles_input_and_bounds_time_without_network(tmp_path):
    environment = dict(os.environ)
    data = b"synthetic image bytes"
    result = _run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().upper())"],
                  environment, lambda: None, data=data)
    assert result == data.upper()
    start = time.monotonic()
    assert _run([sys.executable, "-c", "import time; time.sleep(20)"], environment,
                lambda: None, timeout=0.15) is None
    assert time.monotonic() - start < 3


def test_local_ocr_cancellation_terminates_process():
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise Cancelled("cancelled")

    start = time.monotonic()
    with pytest.raises(Cancelled):
        _run([sys.executable, "-c", "import time; time.sleep(20)"], dict(os.environ), check)
    assert time.monotonic() - start < 3


def test_ocr_discovery_prefers_bundled_runtime_and_does_not_forward_api_secrets(tmp_path, monkeypatch):
    root = tmp_path / "runtimes"
    binary = root / "tesseract/tesseract.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr(image_geometry, "runtime_root", lambda: root)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    executable, environment = image_geometry._configuration()
    assert executable == str(binary)
    assert environment["TESSDATA_PREFIX"] == str(root / "tesseract/tessdata")
    assert "OPENAI_API_KEY" not in environment


@pytest.mark.skipif(os.name == "nt", reason="Linux system worker library isolation")
def test_frozen_system_ocr_restores_original_library_search_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(image_geometry, "runtime_root", lambda: None)
    monkeypatch.setattr(image_geometry.shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/desktop/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/extra-libraries")
    assert image_geometry._configuration()[1]["LD_LIBRARY_PATH"] == "/system/extra-libraries"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in image_geometry._configuration()[1]


def test_missing_ocr_or_language_preserves_vision_fallback(qapp, monkeypatch):
    from PySide6.QtGui import QImage

    image = QImage(20, 20, QImage.Format_RGB888)
    monkeypatch.setattr(image_geometry, "_configuration", lambda: ("", {}))
    assert image_geometry.detect_text(image, "en", lambda: None) == []
    monkeypatch.setattr(image_geometry, "_configuration", lambda: ("fake", {}))
    monkeypatch.setattr(image_geometry, "_run", lambda *_args, **_kwargs: b"eng\nosd\n")
    assert image_geometry.detect_text(image, "uk", lambda: None) == []
