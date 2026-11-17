"""Exercise OpenDocument through application workflows with an offline provider."""
from __future__ import annotations

import json
import threading
import zipfile

import pytest
from support import TextProvider, local_settings, pump, settle

from forma.core.models import FormaError, Options, TranslationFailure
from forma.documents.adapters import open_document
from forma.documents.comparison import compare_files
from forma.documents.editor import export_reviewed_copy
from forma.documents.preview import PreviewSession
from forma.documents.preview_office import find_libreoffice, prepare_profile
from forma.documents.review import load_report, parse_report_data
from forma.memory.context import scoped_segment_context
from forma.memory.store import TranslationMemory
from forma.persistence.library import TranslationLibrary
from forma.presentation.backend import Backend
from forma.settings.config import data_directory
from forma.translation.engine import TranslationEngine
from forma.translation.recovery import load_failure_report, save_recovery_edits

CASES = [(".odt", 15), (".ods", 9), (".odp", 16)]


def workflow_source(path):
    kind = {".odt": "text", ".ods": "spreadsheet", ".odp": "presentation"}[path.suffix]
    paragraph = '<text:p>Hello <text:span text:style-name="Bold">world</text:span> 25 kW</text:p>'
    body = {
        ".odt": paragraph,
        ".ods": '<table:table table:name="Data"><table:table-row><table:table-cell office:value-type="string">'
                + paragraph + '</table:table-cell><table:table-cell office:value-type="float" office:value="42">'
                '<text:p>42</text:p></table:table-cell><table:table-cell table:formula="of:=B1*2" '
                'office:value-type="float" office:value="84"><text:p>84</text:p></table:table-cell>'
                '</table:table-row></table:table>',
        ".odp": '<draw:page draw:name="Slide 1"><draw:frame draw:name="Title"><draw:text-box>'
                + paragraph + '</draw:text-box></draw:frame></draw:page>',
    }[path.suffix]
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
    <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
    xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" office:version="1.3">
    <office:body><office:{kind}>{body}</office:{kind}></office:body></office:document-content>'''
    mime = "application/vnd.oasis.opendocument." + kind
    manifest = f'''<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
    <manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>
    <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
    </manifest:manifest>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mime, compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("meta.xml", '<meta>PROTECTED_PRIVATE_METADATA</meta>', compress_type=zipfile.ZIP_DEFLATED)
    return path


def replace_part(path, part, transform):
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for info, data in entries:
            archive.writestr(info, transform(data) if info.filename == part else data)


class OfflineProvider(TextProvider):
    input_tokens = output_tokens = 0


@pytest.mark.parametrize("suffix,profile", CASES)
def test_odf_translation_report_manual_edit_and_unchanged_structure(tmp_path, suffix, profile):
    source = workflow_source(tmp_path / ("source" + suffix))
    before = source.read_bytes()
    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "results", Options("en", "uk", review=False, auto_glossary=False),
    )
    assert result.engine == "forma-odf" and result.extraction_profile == profile
    assert source.read_bytes() == before
    saved, _ = load_report(result.report)
    assert saved.engine == "forma-odf" and saved.extraction_profile == profile
    assert compare_files(source, [result.output])["candidates"][0]["structure_ok"]
    fragment = next(item for item in result.segments if "Hello" in item.plain)
    edited = export_reviewed_copy(source, result.output, result.report,
                                 {fragment.id: result.translations[fragment.id].replace("Вітаю", "Привіт")})
    assert edited.engine == "forma-odf" and edited.extraction_profile == profile
    assert "Привіт" in " ".join(item.plain for item in open_document(edited.output).segments)
    assert "Вітаю" in " ".join(item.plain for item in open_document(result.output).segments)
    assert compare_files(source, [edited.output])["candidates"][0]["structure_ok"]
    with zipfile.ZipFile(result.output) as archive:
        assert archive.read("meta.xml") == b"<meta>PROTECTED_PRIVATE_METADATA</meta>"
    for engine in ("forma-text", "forma-ooxml"):
        payload = json.loads(result.report.read_text())
        payload["engine"] = engine
        with pytest.raises(FormaError):
            parse_report_data(payload, result.report)
    replace_part(result.output, "meta.xml", lambda data: data.replace(b"PRIVATE", b"EXTERNAL"))
    with pytest.raises(FormaError, match="змінився"):
        export_reviewed_copy(source, result.output, result.report, {fragment.id: result.translations[fragment.id]})


@pytest.mark.parametrize("suffix,profile", CASES)
def test_odf_memory_reuses_source_and_invalidates_on_protected_metadata_change(tmp_path, suffix, profile):
    source = workflow_source(tmp_path / ("source" + suffix))
    project = TranslationMemory().create_project("OpenDocument", "en", "uk")
    options = Options("en", "uk", review=False, auto_glossary=False, memory_project_id=project.id)
    first = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    assert first.memory_project_id == project.id
    for checkpoint in (data_directory() / "checkpoints").glob("*.json"):
        checkpoint.unlink()
    provider = OfflineProvider()
    second = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)
    assert not provider.calls and second.memory_segments == len(second.segments)
    original = open_document(source)
    context = scoped_segment_context(original, original.segments[0])
    assert "PROTECTED_PRIVATE_METADATA" not in context
    replace_part(source, "meta.xml", lambda data: data.replace(b"PRIVATE", b"CHANGED"))
    changed = open_document(source)
    assert changed.segments[0].plain == original.segments[0].plain
    assert scoped_segment_context(changed, changed.segments[0]) != context


@pytest.mark.parametrize("suffix,profile", CASES)
def test_odf_queue_review_and_restart(qapp, monkeypatch, tmp_path, suffix, profile):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *a, **k: "")

    class OfflineEngine(TranslationEngine):
        def __init__(self, settings, control):
            super().__init__(settings, control, provider=OfflineProvider())

    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", OfflineEngine)
    source = workflow_source(tmp_path / ("source" + suffix))
    backend, restored = Backend(), None
    try:
        settle(qapp, backend)
        backend.setOutputFolder(str(tmp_path / "out"))
        backend.addFiles([source.as_uri()])
        pump(qapp, lambda: backend.documents and backend.documents[0]["status"] == "ready")
        caps = backend.documentCapabilities(suffix)
        assert caps["page_preview"] == (suffix != ".ods")
        assert caps["manual_export"] and caps["memory"] and caps["recovery"]
        assert not caps["memory_import"] and not caps["memory_approval"]
        backend.start("en", "uk", "Загальна тематика", False, False)
        pump(qapp, lambda: not backend.busy and backend.documents[0]["status"] == "done")
        settle(qapp, backend)
        backend.activateReview()
        pump(qapp, lambda: bool(backend.reviewEntries))
        entry = next(item for item in backend.reviewEntries if item["kind"] == "segment")
        backend.selectReviewEntry(entry["id"])
        assert backend.selectedReviewEntry["canEdit"]
        row = TranslationLibrary().load_queue()[0]
        assert row["format"] == suffix[1:].upper()
        restored = Backend()
        settle(qapp, restored)
        assert restored.documents[0]["status"] == "done"
        assert restored.documents[0]["report"] == row["report"]
    finally:
        for instance in (backend, restored):
            if instance is not None:
                instance.cancel()
                settle(qapp, instance)


@pytest.mark.parametrize("suffix,profile", CASES)
def test_odf_failed_number_validation_can_be_corrected_and_resumed(tmp_path, suffix, profile):
    source = workflow_source(tmp_path / ("source" + suffix))
    settings, options = local_settings(), Options("en", "uk", review=False, auto_glossary=False)

    def corrupt(items, _):
        return {"segments": [{**item, "text": item["text"].replace("25", "250")} for item in items]}

    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=OfflineProvider(corrupt)).translate(source, tmp_path / "out", options)
    report = caught.value.report.with_suffix(".json")
    result, _, meta = load_failure_report(report)
    assert meta["can_resume"] and result.engine == "forma-odf" and result.extraction_profile == profile
    edits = {item.id: result.translations[item.id].replace("250", "25") for item in result.segments}
    save_recovery_edits(report, edits, expected_report_fingerprint=meta["report_fingerprint"])
    provider = OfflineProvider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "out", options)
    assert resumed.output.is_file() and not provider.calls


def test_ods_comparison_detects_formula_and_numeric_cell_changes(tmp_path):
    source = workflow_source(tmp_path / "source.ods")
    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    replace_part(result.output, "content.xml", lambda data: data.replace(b"of:=B1*2", b"of:=B1*3"))
    assert not compare_files(source, [result.output])["candidates"][0]["structure_ok"]
    replace_part(result.output, "content.xml", lambda data: data.replace(b'office:value="42"', b'office:value="43"').replace(b">42<", b">43<"))
    metrics = compare_files(source, [result.output])["candidates"][0]
    assert not metrics["numbers_preserved"]


@pytest.mark.skipif(not find_libreoffice(), reason="Optional installed LibreOffice")
def test_installed_libreoffice_reads_translated_ods_text_values_and_formula(tmp_path):
    from openpyxl import load_workbook

    source = workflow_source(tmp_path / "source.ods")
    before = source.read_bytes()
    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    instance = PreviewSession(cache_parent=tmp_path)
    try:
        for index, (path, expected) in enumerate(((source, "Hello world 25 kW"), (result.output, "Вітаю світ 25 kW"))):
            directory = instance._root / str(index)
            directory.mkdir()
            profile = directory / "profile"
            prepare_profile(profile)
            instance._run([find_libreoffice(), "-env:UserInstallation=" + profile.as_uri(), "--headless", "--nologo",
                           "--nodefault", "--norestore", "--convert-to", "xlsx", "--outdir", str(directory), str(path)],
                          directory, 30, threading.Event(), external=True)
            workbook = load_workbook(directory / (path.stem + ".xlsx"))
            try:
                sheet = workbook.active
                assert sheet["A1"].value == expected and sheet["B1"].value == 42 and sheet["C1"].value == "=B1*2"
            finally:
                workbook.close()
        assert source.read_bytes() == before
    finally:
        instance.close()
