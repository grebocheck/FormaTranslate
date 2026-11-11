"""One-click PDF routing with synthetic documents and offline providers only."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from pypdf import PdfWriter
from test_engine import FakeProvider, local_settings

import forma.documents.pdf as pdf_module
import forma.translation.preparation as preparation
from forma.core.models import Cancelled, FormaError, Options, TranslationFailure, TranslationResult
from forma.documents.editor import export_reviewed_copy
from forma.documents.review import load_report, parse_report_data
from forma.memory.store import TranslationMemory
from forma.translation.engine import TranslationEngine


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def scanned_pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def fake_converter(monkeypatch, docx_file, *, warnings=(), during=None, corrupt=None):
    calls = []

    def convert(source, folder, language, configured, control, progress):
        calls.append((source, language, configured))
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / "scan.ocr.docx"
        shutil.copyfile(docx_file, output)
        manifest = {
            "schema_version": 1, "source_sha256": digest(source),
            "output_sha256": digest(output), "source_language": language,
            "page_count": 1, "pages": [{
                "index": 1, "text_chars": 100, "status": "complete", "tables": 0,
                "warnings": list(warnings),
            }],
        }
        if corrupt:
            manifest.update(corrupt)
        output.with_suffix(".ocr.json").write_text(json.dumps(manifest), encoding="utf-8")
        progress(80, "Розпізнаємо текст")
        if during:
            during(source, output, control)
        return output

    monkeypatch.setattr(preparation, "convert_scan_pdf", convert)
    return calls


@pytest.mark.parametrize(("scanned", "images", "expected"), [
    (True, [False], True),
    (False, [False], False),
    (False, [True, False], False),
    (False, [True, True, False], True),
    (False, [True, True, False, False], False),
])
def test_pdf_scan_threshold_is_shared_with_dispatch(scanned, images, expected):
    assert pdf_module.needs_ocr({
        "scanned": scanned, "pages": [{"image_without_text": value} for value in images],
    }) is expected


def test_scan_is_prepared_and_translated_in_one_run_with_reviewable_provenance(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    calls = fake_converter(monkeypatch, docx_file, warnings=("low_ocr_confidence", "private text"))
    settings = local_settings().with_overrides(scan_provider="forma")
    engine = TranslationEngine(settings, provider=FakeProvider())
    phases = []
    engine.preparation_progress = lambda percent, text: phases.append((percent, text))
    original = scanned_pdf.read_bytes()
    result = engine.translate(
        scanned_pdf, tmp_path / "out", Options(source_language="en", auto_glossary=False),
    )
    assert calls == [(scanned_pdf, "en", "")]
    assert result.source.name == "scan.ocr.docx" and result.output.exists()
    assert result.preparation["original_source"] == str(scanned_pdf)
    assert result.preparation["original_source_sha256"] == digest(scanned_pdf)
    assert result.source_sha256 == digest(result.source)
    assert result.preparation["prepared_source_sha256"] == result.source_sha256
    assert result.preparation["coverage"] == "complete"
    assert {issue.code for issue in result.issues} >= {"ocr_source_review", "ocr_low_confidence"}
    assert not any("private text" in issue.message for issue in result.issues)
    report = json.loads(result.report.read_text())
    assert report["preparation"] == result.preparation
    assert report["source"] == str(result.source)
    loaded, _ = load_report(result.report)
    assert loaded.source == result.source
    assert loaded.source_sha256 == result.source_sha256
    assert loaded.preparation == result.preparation
    assert scanned_pdf.read_bytes() == original
    assert phases and any(percent == 80 for percent, _ in phases)


def test_project_snapshot_is_captured_before_ocr_and_used_for_the_docx(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    memory = TranslationMemory()
    project = memory.create_project("Series", "en", "uk", {"heat meter": "старий термін"})
    options = Options(source_language="en", memory_project_id=project.id)

    def update(*_):
        memory.update_glossary(project.id, {"heat meter": "новий термін"}, expected_revision=0)

    fake_converter(monkeypatch, docx_file, during=update)
    provider = FakeProvider()
    engine = TranslationEngine(local_settings().with_overrides(scan_provider="forma"), provider=provider)
    result = engine.translate(scanned_pdf, tmp_path / "out", options)
    assert result.memory_project_id == project.id
    assert result.effective_glossary == {"heat meter": "старий термін"}
    assert all(payload["glossary"] == result.effective_glossary for _, payload in provider.calls)
    assert engine.memory_run.snapshot.project_id == project.id
    assert options.memory_project_id == project.id


def test_relative_cli_paths_produce_a_report_that_can_be_reopened(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    fake_converter(monkeypatch, docx_file)
    monkeypatch.chdir(tmp_path)
    result = TranslationEngine(
        local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider(),
    ).translate(Path("scan.pdf"), Path("out"), Options(source_language="en", auto_glossary=False))
    loaded, _ = load_report(result.report)
    assert result.source.is_absolute()
    assert loaded.preparation == result.preparation
    assert loaded.source == result.source


def test_text_pdf_uses_frozen_project_glossary_without_disabling_project(
    monkeypatch, pdf_file, tmp_path,
):
    memory = TranslationMemory()
    project = memory.create_project("Series", "en", "uk", {"meter": "лічильник"})
    options = Options(source_language="en", memory_project_id=project.id, glossary={"stale": "old"})
    seen = []

    def translate(source, folder, effective, *_):
        seen.append(effective)
        folder.mkdir(parents=True)
        output = folder / "translated.pdf"
        shutil.copyfile(source, output)
        return TranslationResult(source, output, [], {}, [], engine="babeldoc")

    monkeypatch.setattr(pdf_module, "translate_pdf", translate)
    result = TranslationEngine(local_settings()).translate(pdf_file, tmp_path / "out", options)
    assert seen[0].memory_project_id == ""
    assert seen[0].glossary == {"meter": "лічильник"}
    assert not seen[0].auto_glossary
    assert options.memory_project_id == project.id and options.glossary == {"stale": "old"}
    assert result.memory_project_id == ""
    assert result.effective_glossary == {"meter": "лічильник"}
    assert any(issue.code == "pdf_memory_skipped" and issue.severity == "info" for issue in result.issues)
    assert json.loads(result.report.read_text())["options"]["memory_project_id"] == ""


def test_automatic_ocr_prefers_unattended_converter_and_explicit_modes_are_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(preparation, "find_scan_converter", lambda path: calls.append(path) or "/forma-ocr")
    monkeypatch.setattr(preparation, "find_abbyy", lambda *_: pytest.fail("No ABBYY probe needed"))
    assert preparation.effective_scan_provider(local_settings()) == "forma"
    assert calls == [""]
    assert preparation.effective_scan_provider(local_settings().with_overrides(scan_provider="abbyy")) == "abbyy"
    with pytest.raises(FormaError, match="Extended CLI"):
        preparation.effective_scan_provider(local_settings().with_overrides(scan_provider="abbyy_extended"))


def test_explicit_converter_path_remains_authoritative_in_auto_mode(monkeypatch):
    def forbidden(*_):
        pytest.fail("An explicit converter path must not silently fall back to another provider")

    monkeypatch.setattr(preparation, "find_scan_converter", forbidden)
    monkeypatch.setattr(preparation, "find_abbyy", forbidden)
    assert preparation.effective_scan_provider(
        local_settings().with_overrides(scan_converter_path="/missing/custom-worker"),
    ) == "forma"


def test_ordinary_abbyy_without_ui_handoff_provides_a_guided_error(scanned_pdf, tmp_path):
    provider = FakeProvider()
    engine = TranslationEngine(local_settings().with_overrides(scan_provider="abbyy"), provider=provider)
    with pytest.raises(FormaError, match="Відкрийте PDF в ABBYY"):
        engine.translate(scanned_pdf, tmp_path / "out", Options(source_language="en"))
    assert not provider.calls


def test_ordinary_abbyy_continues_only_with_a_bound_import(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    def handoff(source, language, control, progress):
        output = tmp_path / "reviewed.docx"
        shutil.copyfile(docx_file, output)
        output.with_suffix(".ocr.json").write_text(json.dumps({
            "schema_version": 1, "provider": "abbyy", "coverage": "not_verified",
            "source_sha256": digest(source), "output_sha256": digest(output),
            "source_language": language, "source_page_count": 1,
        }), encoding="utf-8")
        return output

    engine = TranslationEngine(local_settings().with_overrides(scan_provider="abbyy"), provider=FakeProvider())
    engine.pdf_handoff = handoff
    result = engine.translate(scanned_pdf, tmp_path / "out", Options(source_language="en", auto_glossary=False))
    assert result.preparation["provider"] == "abbyy"
    assert result.preparation["coverage"] == "not_verified"
    assert any(issue.code == "ocr_coverage_not_verified" for issue in result.issues)


@pytest.mark.parametrize("corrupt", [
    {"source_sha256": "0" * 64}, {"output_sha256": "0" * 64}, {"page_count": 2},
])
def test_bad_conversion_binding_never_reaches_provider(
    monkeypatch, scanned_pdf, docx_file, tmp_path, corrupt,
):
    fake_converter(monkeypatch, docx_file, corrupt=corrupt)
    provider = FakeProvider()
    engine = TranslationEngine(local_settings().with_overrides(scan_provider="forma"), provider=provider)
    with pytest.raises(FormaError, match="цілісність"):
        engine.translate(scanned_pdf, tmp_path / "out", Options(source_language="en"))
    assert not provider.calls


@pytest.mark.parametrize("action", ["cancel", "change_source"])
def test_ocr_boundary_cancellation_or_source_change_never_starts_translation(
    monkeypatch, scanned_pdf, docx_file, tmp_path, action,
):
    def during(source, output, control):
        if action == "cancel":
            control.cancelled.set()
        else:
            source.write_bytes(source.read_bytes() + b"\nchanged")

    fake_converter(monkeypatch, docx_file, during=during)
    provider = FakeProvider()
    engine = TranslationEngine(local_settings().with_overrides(scan_provider="forma"), provider=provider)
    with pytest.raises(Cancelled if action == "cancel" else FormaError):
        engine.translate(scanned_pdf, tmp_path / "out", Options(source_language="en"))
    assert not provider.calls


def test_unknown_source_language_stops_before_ocr(monkeypatch, scanned_pdf, tmp_path):
    monkeypatch.setattr(preparation, "convert_scan_pdf", lambda *_: pytest.fail("Must select source language"))
    engine = TranslationEngine(local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider())
    with pytest.raises(FormaError, match="мову оригіналу"):
        engine.translate(scanned_pdf, tmp_path / "out", Options())


def test_validation_failure_report_names_prepared_docx_and_original_pdf(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    fake_converter(monkeypatch, docx_file)
    engine = TranslationEngine(local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider(invalid=True))
    with pytest.raises(TranslationFailure) as caught:
        engine.translate(scanned_pdf, tmp_path / "out", Options(source_language="en", review=False, auto_glossary=False))
    failure = caught.value
    assert failure.report.exists()
    payload = json.loads(failure.report.with_suffix(".json").read_text())
    assert payload["source"].endswith("scan.ocr.docx")
    assert payload["preparation"]["original_source"] == str(scanned_pdf)
    assert not payload["exported"]


def test_ocr_review_export_keeps_original_provenance_and_prepared_source(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    fake_converter(monkeypatch, docx_file)
    result = TranslationEngine(
        local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider(),
    ).translate(scanned_pdf, tmp_path / "out", Options(source_language="en", auto_glossary=False))
    sid = next(sid for sid, text in result.translations.items() if "Лічильник тепла" in text)
    edited = result.translations[sid].replace("Лічильник тепла", "Теплолічильник")
    reviewed = export_reviewed_copy(result.source, result.output, result.report, {sid: edited})
    assert reviewed.preparation == result.preparation
    assert reviewed.translations[sid] == edited
    loaded, _ = load_report(reviewed.report)
    assert loaded.preparation == result.preparation
    assert loaded.source == result.source
    assert loaded.translations[sid] == edited


def test_preparation_parser_is_pure_and_drops_unrelated_private_metadata(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    fake_converter(monkeypatch, docx_file)
    result = TranslationEngine(
        local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider(),
    ).translate(scanned_pdf, tmp_path / "out", Options(source_language="en", auto_glossary=False))
    payload = json.loads(result.report.read_text())
    payload["preparation"]["private_ignored_marker"] = "private ignored value"

    def forbidden(*_args, **_kwargs):
        pytest.fail("Parsing provenance must never open referenced paths")

    monkeypatch.setattr(type(tmp_path), "open", forbidden)
    parsed, _ = parse_report_data(payload, result.report)
    assert parsed.preparation == result.preparation
    assert "private_ignored_marker" not in parsed.serializable()["preparation"]


@pytest.mark.parametrize(("key", "value"), [
    ("original_source", "https://untrusted.invalid/a.pdf"),
    ("prepared_source", "//untrusted.invalid/a.docx"),
    ("prepared_source_sha256", "0" * 64),
    ("source_language", "de"),
    ("provider", "untrusted"),
    ("coverage", "not_verified"),
    ("page_count", True),
])
def test_preparation_parser_rejects_unbound_or_malformed_provenance(
    monkeypatch, scanned_pdf, docx_file, tmp_path, key, value,
):
    fake_converter(monkeypatch, docx_file)
    result = TranslationEngine(
        local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider(),
    ).translate(scanned_pdf, tmp_path / "out", Options(source_language="en", auto_glossary=False))
    payload = json.loads(result.report.read_text())
    payload["preparation"][key] = value
    with pytest.raises(FormaError, match="прочитати звіт"):
        parse_report_data(payload, result.report)
