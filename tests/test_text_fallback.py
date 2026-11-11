"""Offline validation of optional local prose OCR and raw-reference replacement."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.fixture
def fallback(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools/ocr/src"))
    return importlib.import_module("forma_ocr.text_fallback")


def tsv(text, confidence=96):
    rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    rows.extend(f"5\t1\t1\t1\t1\t{index}\t0\t0\t10\t10\t{confidence}\t{word}" for index, word in enumerate(text.split(), 1))
    return ("\n".join(rows) + "\n").encode()


def page():
    damaged = "Damaged primary OCR paragraph containing unrelated gibberish words."
    table = "<table><tr><td>Budget -100.25</td></tr></table>"
    return SimpleNamespace(
        blocks=[
            {"label": "text", "text": damaged, "bbox": [10, 10, 350, 100], "uncertain": True},
            {"label": "table", "text": table, "bbox": [10, 200, 350, 350], "uncertain": True},
            {"label": "text", "text": "Reliable source paragraph with all numbers retained.", "bbox": [10, 400, 350, 450]},
        ],
        image=Image.new("RGB", (400, 600), "white"),
        recognized_texts=[damaged, "Budget -100.25", "Reliable source paragraph"],
        scores=[0.4, 0.5, 0.96],
        polygons=[[[10, 10], [350, 10], [350, 100], [10, 100]], [[10, 200], [350, 200], [350, 350], [10, 350]], [[10, 400], [350, 400], [350, 450], [10, 450]]],
    )


def test_strong_local_prose_replaces_primary_reference_without_changing_table(fallback, monkeypatch):
    source = page()
    table = source.blocks[1]["text"]
    corrected = "Payments are due within 45 calendar days after receipt of a valid invoice."
    calls = []

    def local(arguments, payload=None, **_kwargs):
        calls.append(arguments)
        if arguments[-1] == "--list-langs":
            return b"List of available languages:\neng\nosd\n"
        assert payload.startswith(b"\x89PNG")
        assert arguments[1:3] == ["stdin", "stdout"]
        return tsv(corrected)

    monkeypatch.setattr(fallback, "run_local", local)
    result = fallback.repair_uncertain_prose(source, "en", executable="test-tesseract")
    assert result.corrected == result.attempted == 1
    assert source.blocks[0]["text"] == corrected and source.blocks[0]["uncertain"]
    assert source.blocks[1]["text"] == table
    assert len(calls) == 2
    assert corrected in source.recognized_texts
    assert not any("gibberish" in text for text in source.recognized_texts)
    assert "Budget -100.25" in source.recognized_texts
    assert len(source.recognized_texts) == len(source.scores) == len(source.polygons) == 3
    assert result.alternatives[0]["primary_text"].startswith("Damaged")


@pytest.mark.parametrize("candidate,confidence", [("Payments are due after receipt of a valid invoice.", 65), ("Short", 99)])
def test_weak_or_short_retry_does_not_replace_source(fallback, monkeypatch, candidate, confidence):
    source = page()
    before = list(source.recognized_texts)
    monkeypatch.setattr(fallback, "run_local", lambda args, *_a, **_k: b"eng\n" if args[-1] == "--list-langs" else tsv(candidate, confidence))
    result = fallback.repair_uncertain_prose(source, "en", executable="test-tesseract")
    assert not result.corrected and result.rejected == 1
    assert source.recognized_texts == before
    assert "gibberish" in source.blocks[0]["text"]


def test_missing_optional_executable_keeps_reviewable_primary_ocr(fallback, monkeypatch):
    source = page()
    monkeypatch.setattr(fallback.shutil, "which", lambda _name: None)
    result = fallback.repair_uncertain_prose(source, "en")
    assert result.unavailable and not result.corrected
    assert "gibberish" in source.blocks[0]["text"]


def test_missing_language_never_silently_uses_english(fallback, monkeypatch):
    monkeypatch.setattr(fallback, "run_local", lambda *_a, **_k: b"eng\nosd\n")
    result = fallback.repair_uncertain_prose(page(), "uk", executable="test-tesseract")
    assert result.unavailable and not result.attempted


def test_timeout_is_optional_retry_failure(fallback, monkeypatch):
    def local(arguments, *_args, **_kwargs):
        if arguments[-1] == "--list-langs":
            return b"eng\n"
        raise subprocess.TimeoutExpired(arguments, 20)

    monkeypatch.setattr(fallback, "run_local", local)
    source = page()
    result = fallback.repair_uncertain_prose(source, "en", executable="test-tesseract")
    assert result.rejected == 1 and not result.corrected
    assert source.blocks[0]["uncertain"]


def test_cached_corrupted_recovery_text_is_removed_only_inside_repaired_box(fallback, monkeypatch):
    source = page()
    source.blocks.extend([
        {"label": "recovered_text", "text": "gibberish copy", "bbox": [10, 40, 350, 60]},
        {"label": "recovered_text", "text": "outside source note", "bbox": [10, 500, 350, 520]},
    ])
    corrected = "Payments are due within 45 calendar days after receipt of a valid invoice."
    monkeypatch.setattr(fallback, "run_local", lambda args, *_a, **_k: b"eng\n" if args[-1] == "--list-langs" else tsv(corrected))
    assert fallback.repair_uncertain_prose(source, "en", executable="test-tesseract").corrected == 1
    assert not any(block["text"] == "gibberish copy" for block in source.blocks)
    assert any(block["text"] == "outside source note" for block in source.blocks)


def test_tsv_includes_numeric_signs_punctuation_and_weighted_confidence(fallback):
    text, confidence = fallback.parse_tsv(tsv("Amount -100.25 EUR", 93))
    assert text == "Amount -100.25 EUR" and confidence == 93
    with pytest.raises(ValueError, match="exceeds"):
        fallback.parse_tsv(b"x" * (fallback.MAX_OUTPUT_BYTES + 1))
    with pytest.raises(ValueError, match="Invalid"):
        fallback.parse_tsv(b"invalid TSV")


def test_second_ocr_cannot_erase_reliable_primary_numeric_sign(fallback, monkeypatch):
    source = page()
    source.recognized_texts.append("Deduction -12.50%")
    source.scores.append(0.97)
    source.polygons.append([[10, 40], [350, 40], [350, 60], [10, 60]])
    candidate = "Payments include a deduction of 12.50% after receipt of a valid invoice."
    monkeypatch.setattr(fallback, "run_local", lambda args, *_a, **_k: b"eng\n" if args[-1] == "--list-langs" else tsv(candidate))
    result = fallback.repair_uncertain_prose(source, "en", executable="test-tesseract")
    assert result.rejected == 1 and not result.corrected
    assert "Deduction -12.50%" in source.recognized_texts


def test_local_process_timeout_kills_and_waits_for_child(fallback, monkeypatch):
    events = []

    class Process:
        def communicate(self, **_kwargs):
            raise subprocess.TimeoutExpired("fake", 20)

        def kill(self):
            events.append("kill")

        def wait(self):
            events.append("wait")

    monkeypatch.setattr(fallback.subprocess, "Popen", lambda *_a, **_k: Process())
    with pytest.raises(subprocess.TimeoutExpired):
        fallback.run_local(["fake"], b"PNG")
    assert events == ["kill", "wait"]
