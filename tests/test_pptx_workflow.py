"""Presentation workflows exercise real extraction with offline providers."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree
from pypdf import PdfReader
from support import TextProvider, local_settings, make_pptx, pump, settle

from forma.core.formats import get_format
from forma.core.models import Options, TranslationFailure
from forma.documents.ooxml import A, OfficeDocument
from forma.documents.preview import PreviewSession
from forma.documents.preview_office import find_libreoffice, prepare_office
from forma.persistence.library import TranslationLibrary
from forma.presentation.backend import Backend
from forma.translation.engine import TranslationEngine
from forma.translation.recovery import load_failure_report, save_recovery_edits


class WorkflowProvider(TextProvider):
    input_tokens = output_tokens = 0


@pytest.mark.parametrize("name", [".draft.pptx", Path(".draft.pptx"), "PPTX", ".PPTX"])
def test_presentation_capabilities_resolve_hidden_names_and_format_codes(name):
    capabilities = get_format(name)
    assert capabilities.code == "PPTX" and capabilities.manual_export and capabilities.page_preview


def test_desktop_pptx_queue_translation_review_and_restart(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *a, **k: "")

    class OfflineEngine(TranslationEngine):
        def __init__(self, settings, control):
            super().__init__(settings, control, provider=WorkflowProvider())

    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", OfflineEngine)
    source = make_pptx(tmp_path / "deck.pptx")
    before = source.read_bytes()
    backend = Backend()
    restored = None
    try:
        settle(qapp, backend)
        backend.setOutputFolder(str(tmp_path / "results"))
        backend.addFiles([source.as_uri()])
        pump(qapp, lambda: backend.documents and backend.documents[0]["status"] == "ready")
        assert backend.documentCapabilities("PPTX")["page_preview"]
        assert backend.documentCapabilities("PPTX")["manual_export"]
        assert not backend.documentCapabilities("PPTX")["memory_import"]
        backend.start("en", "uk", "Загальна тематика", False, False)
        pump(qapp, lambda: not backend.busy and backend.documents[0]["status"] == "done")
        settle(qapp, backend)
        document = backend.documents[0]
        assert document["format"] == "PPTX" and Path(document["report"]).is_file()
        payload = json.loads(Path(document["report"]).read_text())
        assert payload["extraction_profile"] == 4 and payload["output"] == document["output"]
        backend.activateReview()
        pump(qapp, lambda: bool(backend.reviewEntries))
        entry = next(item for item in backend.reviewEntries if item["kind"] == "segment")
        backend.selectReviewEntry(entry["id"])
        assert backend.selectedReviewEntry["canEdit"]
        assert TranslationLibrary().load_queue()[0]["format"] == "PPTX"
        restored = Backend()
        settle(qapp, restored)
        assert restored.documents[0]["id"] == document["id"]
        assert restored.documents[0]["status"] == "done"
        assert restored.selectedDocument["format"] == "PPTX"
        assert source.read_bytes() == before
    finally:
        for instance in (backend, restored):
            if instance is not None:
                instance.cancel()
                settle(qapp, instance)


def test_pptx_validation_failure_can_be_corrected_and_resumed_offline(tmp_path):
    source = make_pptx(tmp_path / "deck.pptx")
    settings, options = local_settings(), Options("en", "uk", review=False, auto_glossary=False)

    def corrupt(segments, _):
        return {"segments": [{**item, "text": item["text"].replace("25", "250")} for item in segments]}

    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=WorkflowProvider(corrupt)).translate(source, tmp_path / "out", options)
    report = caught.value.report.with_suffix(".json")
    failed, _, metadata = load_failure_report(report)
    assert metadata["can_resume"] and failed.extraction_profile == 4
    edits = {item.id: failed.translations[item.id].replace("250", "25") for item in failed.segments}
    save_recovery_edits(report, edits, expected_report_fingerprint=metadata["report_fingerprint"])
    provider = WorkflowProvider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = metadata["checkpoint_id"]
    result = engine.translate(source, tmp_path / "out", options)
    assert result.output.is_file() and result.extraction_profile == 4
    assert not provider.calls
    assert any("25" in segment.plain for segment in OfficeDocument(result.output).segments)


def test_private_pptx_preview_removes_actions_external_media_and_traversal(tmp_path):
    relations = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="external" Type="image" Target="https://example.invalid/private.png" TargetMode="External"/>
<Relationship Id="traversal" Type="image" Target="../../../../private.png"/>
<Relationship Id="video" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/video" Target="../media/video.mp4"/>
</Relationships>'''
    source = make_pptx(tmp_path / "deck.pptx", extra_parts={
        "ppt/slides/_rels/slide9.xml.rels": relations,
        "ppt/media/video.mp4": b"synthetic inert media",
    })
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(parts["ppt/slides/slide9.xml"])
    etree.SubElement(root.find(f".//{{{A}}}rPr"), f"{{{A}}}hlinkClick", action="ppaction://hlinkprogram")
    parts["ppt/slides/slide9.xml"] = etree.tostring(root)
    parts["ppt/_rels/presentation.xml.rels"] = parts["ppt/_rels/presentation.xml.rels"].replace(
        b'Target="slides/', b'Target="/ppt/slides/',
    )
    with ZipFile(source, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    before = source.read_bytes()
    destination = tmp_path / "private.pptx"
    prepare_office(source, destination, lambda: None)
    with ZipFile(destination) as archive:
        content = archive.read("ppt/slides/_rels/slide9.xml.rels")
        assert b"example.invalid" not in content and b"Relationship Id=" not in content
        assert b"Hello world" in archive.read("ppt/slides/slide9.xml")
        assert b"hlinkprogram" not in archive.read("ppt/slides/slide9.xml")
    assert len(OfficeDocument(destination).presentation_slides) == 2
    assert source.read_bytes() == before


@pytest.mark.skipif(not find_libreoffice(), reason="Optional installed LibreOffice Impress")
def test_installed_impress_preview_keeps_slide_order_geometry_and_source(tmp_path):
    source = make_pptx(tmp_path / "deck.pptx")
    before = source.read_bytes()
    session = PreviewSession(cache_parent=tmp_path)
    try:
        description = session.describe(source, threading.Event())
        assert description.page_count == 2
        assert description.page_sizes[0][0] / description.page_sizes[0][1] == pytest.approx(4 / 3, abs=.01)
        first = session.render(source, 0, 600, threading.Event())
        assert first.page_count == 2 and first.image_path.is_file()
        _, converted = next(iter(session._documents.values()))
        pages = PdfReader(converted).pages
        assert "Hello" in pages[0].extract_text() and "Other slide" in pages[1].extract_text()
        assert not list(session._root.rglob("*.pptx"))
        assert source.read_bytes() == before
    finally:
        session.close()
