"""One-click PDF routing with synthetic documents and offline providers only."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from pypdf import PdfWriter
from support import FakeProvider, local_settings

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
    worker = docx_file.parent / "offline-worker" / "forma-ocr"
    worker.parent.mkdir(exist_ok=True)
    worker.write_bytes(b"Offline frozen worker fixture")
    internal = worker.parent / "_internal"
    internal.mkdir(exist_ok=True)
    (internal / "worker.py").write_text("# Synthetic, never executed\n")
    models = docx_file.parent / "offline-models"
    models.mkdir(exist_ok=True)
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(models))
    monkeypatch.setenv("TESSDATA_PREFIX", str(models))
    monkeypatch.setattr(preparation, "find_scan_converter", lambda _: str(worker))

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


def test_one_page_scan_with_native_footer_uses_ocr_instead_of_translating_only_footer(
    monkeypatch, docx_file, tmp_path,
):
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    source = tmp_path / "scan-with-footer.pdf"
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).text((40, 80), "Heat meter 12345", fill="black")
    canvas = Canvas(str(source), pagesize=(600, 800))
    canvas.drawImage(ImageReader(image), 0, 20, 600, 780)
    canvas.drawString(260, 5, "Page 1")
    canvas.save()
    info = pdf_module.inspect_pdf(source)
    assert not info["scanned"] and info["pages"][0]["image_without_text"]
    calls = fake_converter(monkeypatch, docx_file)
    monkeypatch.setattr(pdf_module, "translate_pdf", lambda *_: pytest.fail("Raster text needs OCR"))
    result = TranslationEngine(
        local_settings().with_overrides(scan_provider="forma"), provider=FakeProvider(),
    ).translate(source, tmp_path / "out", Options(source_language="en", auto_glossary=False))
    assert calls == [(source, "en", "")]
    assert result.output.suffix == ".docx" and result.preparation["kind"] == "ocr"


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


def test_interrupted_scan_reuses_durable_ocr_and_paid_office_checkpoint(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    calls = fake_converter(monkeypatch, docx_file)
    settings = local_settings().with_overrides(scan_provider="forma")
    options = Options(source_language="en", review=False, auto_glossary=False)
    with pytest.raises(Cancelled):
        TranslationEngine(settings, provider=FakeProvider(interrupt_after=1)).translate(
            scanned_pdf, tmp_path / "out", options,
        )
    provider = FakeProvider()
    result = TranslationEngine(settings, provider=provider).translate(scanned_pdf, tmp_path / "out", options)
    assert len(calls) == 1
    assert result.cached_segments == 8
    assert result.preparation["reused"]
    loaded, _ = load_report(result.report)
    assert loaded.preparation["reused"]


@pytest.mark.parametrize("changed", ["docx", "manifest"])
def test_prepared_stage_rechecks_both_artifacts_before_reuse(
    monkeypatch, scanned_pdf, docx_file, tmp_path, changed,
):
    calls = fake_converter(monkeypatch, docx_file)
    settings = local_settings().with_overrides(scan_provider="forma")
    engine = TranslationEngine(settings, provider=FakeProvider())
    options = Options(source_language="en", review=False, auto_glossary=False)
    result = engine.translate(scanned_pdf, tmp_path / "out", options)
    altered = result.source if changed == "docx" else result.source.with_suffix(".ocr.json")
    altered.write_bytes(altered.read_bytes() + b"\nmodified artifact\n")
    retry = engine.translate(scanned_pdf, tmp_path / "out", options)
    assert len(calls) == 2 and not retry.preparation["reused"]


def test_prepared_stage_identity_tracks_source_language_and_worker_not_translation_model(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    from forma.providers.client import JobControl

    calls = fake_converter(monkeypatch, docx_file)
    worker = tmp_path / "offline-ocr"
    worker.write_bytes(b"worker version 1")
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal/model.onnx").write_bytes(b"synthetic model")
    monkeypatch.setattr(preparation, "find_scan_converter", lambda _: str(worker))
    settings = local_settings().with_overrides(scan_provider="forma")

    def prepare(language="en", configured=settings):
        return preparation.prepare_pdf_scan(
            scanned_pdf, tmp_path / "out", language, configured, JobControl(), lambda *_: None,
            source_sha256=digest(scanned_pdf), page_count=1,
        )

    assert not prepare().provenance["reused"]
    assert prepare(configured=settings.with_overrides(model="another-model")).provenance["reused"]
    assert len(calls) == 1
    worker.write_bytes(b"worker version 2")
    assert not prepare().provenance["reused"] and len(calls) == 2
    assert not prepare("de").provenance["reused"] and len(calls) == 3
    scanned_pdf.write_bytes(scanned_pdf.read_bytes() + b"\n% updated source\n")
    assert not prepare().provenance["reused"] and len(calls) == 4


def test_external_worker_identity_tracks_frozen_and_default_model_resources(
    monkeypatch, tmp_path,
):
    import forma.persistence.prepared as stages
    from forma.providers.client import JobControl

    worker = tmp_path / "worker/forma-ocr"
    worker.parent.mkdir()
    worker.write_bytes(b"frozen worker")
    internal = worker.parent / "_internal"
    internal.mkdir()
    frozen_model = internal / "layout.onnx"
    frozen_model.write_bytes(b"frozen model version one")
    hub = tmp_path / "hub"
    default_model = hub / "models--docling-project--layout/snapshots/revision/weights.bin"
    default_model.parent.mkdir(parents=True)
    default_model.write_bytes(b"default model version one")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setattr(stages.shutil, "which", lambda _: None)
    settings, control = local_settings(), JobControl()

    def identity():
        return stages.worker_identity(str(worker), "forma", settings, control)

    first = identity()
    assert identity() == first  # Warm digest cache must not change identity.
    frozen_model.write_bytes(b"frozen model version two")
    second = identity()
    assert second != first
    default_model.write_bytes(b"default model version two")
    assert identity() != second
    # A distribution record without discoverable worker code is not a known
    # closure (for example, an editable import hook outside the environment).
    external = tmp_path / "unknown/bin/forma-ocr"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"editable launcher")
    metadata = tmp_path / "unknown/lib/python3.12/site-packages/forma_ocr-1.0.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Name: forma-ocr\nVersion: 1.0\n")
    assert stages.worker_identity(str(external), "forma", settings, control) != stages.worker_identity(
        str(external), "forma", settings, control,
    )
    monkeypatch.setattr(stages, "_MAX_IDENTITY_ENTRIES", 1)
    assert identity() != identity()  # Incomplete closure never authorizes reuse.


def test_bundled_worker_identity_survives_portable_moves_but_tracks_bundle_changes(monkeypatch, tmp_path):
    import sys

    import forma.persistence.prepared as stages
    from forma.providers.client import JobControl

    identities = []
    for name in ("first portable", "moved/portable"):
        portable = tmp_path / name
        root = portable / "_internal/runtimes"
        worker = root / "ocr/forma-ocr.exe"
        worker.parent.mkdir(parents=True)
        worker.write_bytes(b"same worker")
        inventory = root.parent / "runtimes-meta/BUNDLE.json"
        inventory.parent.mkdir()
        inventory.write_text('{"snapshot":"same verified worker and models"}')
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(portable / "Forma.exe"))
        monkeypatch.setattr(sys, "_MEIPASS", str(root.parent), raising=False)
        identities.append(stages.worker_identity(str(worker), "forma", local_settings(), JobControl()))
    assert identities[0] == identities[1]
    inventory.write_text('{"snapshot":"updated worker or models"}')
    assert stages.worker_identity(str(worker), "forma", local_settings(), JobControl()) != identities[1]


def test_stop_after_completed_ocr_keeps_stage_without_starting_translation(
    monkeypatch, scanned_pdf, docx_file, tmp_path,
):
    calls = fake_converter(monkeypatch, docx_file, during=lambda source, output, control: control.cancelled.set())
    settings = local_settings().with_overrides(scan_provider="forma")
    provider = FakeProvider()
    options = Options(source_language="en", review=False, auto_glossary=False)
    with pytest.raises(Cancelled):
        TranslationEngine(settings, provider=provider).translate(scanned_pdf, tmp_path / "out", options)
    assert not provider.calls
    retry = TranslationEngine(settings, provider=FakeProvider()).translate(scanned_pdf, tmp_path / "out", options)
    assert len(calls) == 1 and retry.preparation["reused"]


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
    worker = tmp_path / "FineReaderOCR.exe"
    worker.write_bytes(b"offline ABBYY fixture")
    monkeypatch.setattr(preparation, "find_abbyy", lambda _: str(worker))
    handoffs = []

    def handoff(source, language, control, progress):
        handoffs.append(source)
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
    provider = FakeProvider()
    resumed = TranslationEngine(engine.settings, provider=provider).translate(
        scanned_pdf, tmp_path / "out", Options(source_language="en", auto_glossary=False),
    )
    assert resumed.preparation["reused"]
    assert len(handoffs) == 1 and not provider.calls
    assert any(issue.code == "ocr_coverage_not_verified" for issue in resumed.issues)


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
