"""Document-level reference checks complement per-paragraph structural guards."""

from pathlib import Path

import pytest
from docx import Document

from forma.core.models import Options, Segment, TranslationResult
from forma.documents.editor import edit_translation_markup, export_reviewed_copy
from forma.documents.review import build_review
from forma.memory.store import TranslationMemory
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine
from forma.translation.references import REFERENCE_CODES, check_document_references


def segment(number, text, part="word/document.xml"):
    return Segment(f"{part}#{number}", f"Paragraph {number}", f"<r0>{text}</r0>")


def test_detects_inconsistent_labels_and_collisions_without_choosing_an_alphabet():
    segments = [segment(1, "Додаток Г. Насоси"), segment(2, "Додаток Д. Таблиці"),
                segment(3, "Додаток Д. Розрахунок")]
    translations = {segments[0].id: "<r0>Appendix D. Pumps</r0>",
                    segments[1].id: "<r0>Appendix E. Tables</r0>",
                    segments[2].id: "<r0>Appendix D. Calculations</r0>"}
    before = dict(translations)
    findings = check_document_references(segments, translations)
    assert {issue.code for issue in findings} == REFERENCE_CODES
    assert all(issue.severity == "warning" for issue in findings)
    assert translations == before
    translations[segments[2].id] = "<r0>Appendix E. Calculations</r0>"
    assert not check_document_references(segments, translations)


def test_ignores_lookalikes_prose_ranges_subappendices_and_unrelated_parts():
    pairs = [("Додаток Б. Дані", "Appendix B. Data"),
             ("Додаток Б. Інші дані", "Appendix В. More data"),
             ("Див. Додаток Б.", "See Appendix C."),
             ("Додаток Б.1", "Appendix D.1"),
             ("Додаток Б–В", "Appendix E–F"),
             ("Додаток Б-1", "Appendix H-1"),
             ("Додаток Б‑1", "Appendix H‑1"),
             ("Додаток Б‑В", "Appendix E‑F"),
             ("Додаток Б.i", "Appendix J.i"),
             ("Додаток Б/В", "Appendix K/L"),
             ("Додаток А або додаток Б", "Appendix B or Appendix A"),
             ("Додаток Б містить інші дані", "Appendix M contains more data"),
             ("Додаток Б\u00a0містить дані", "Appendix N\u00a0contains data"),
             ("Додаток Б\tмістить дані", "Appendix P\tcontains data")]
    segments = [segment(index, source) for index, (source, _) in enumerate(pairs)]
    translations = {item.id: f"<r0>{target}</r0>" for item, (_, target) in zip(segments, pairs, strict=True)}
    header = segment(1, "Додаток Б. Дані", "word/header1.xml")
    sheet = segment(1, "Додаток Б. Дані", "xl/sharedStrings.xml")
    segments.extend((header, sheet))
    translations.update({header.id: "<r0>Appendix F. Data</r0>", sheet.id: "<r0>Appendix H. Data</r0>"})
    assert not check_document_references(segments, translations)


def test_reference_acknowledgement_tracks_other_headings_and_edits_clear_old_findings():
    segments = [segment(1, "Додаток А. Дані"), segment(2, "Додаток А. Розрахунок")]
    translations = {segments[0].id: "<r0>Appendix A. Data</r0>",
                    segments[1].id: "<r0>Appendix B. Calculation</r0>"}
    result = TranslationResult(Path("source.docx"), None, segments, translations, [])
    options = Options("uk", "en", review=False, auto_glossary=False)
    payload = build_review(result, options)
    first = next(item for item in payload["entries"] if item["id"] == segments[0].id)
    accepted = {first["id"]: first["fingerprint"]}
    assert build_review(result, options, accepted=accepted)["entries"][0]["accepted"]
    changed_peer = {segments[1].id: "<r0>Appendix B. Detailed calculation</r0>"}
    changed = build_review(result, options, edits=changed_peer, accepted=accepted)
    assert not changed["entries"][0]["accepted"]
    fixed = build_review(result, options, edits={segments[1].id: "<r0>Appendix A. Calculation</r0>"})
    assert not any("referenceContextFingerprint" in item for item in fixed["entries"])
    assert result.translations == translations and not result.issues


@pytest.mark.parametrize("use_memory", [False, True])
def test_engine_reports_reference_conflict_and_manual_copy_rechecks_it(tmp_path, use_memory):
    source = tmp_path / "references.docx"
    document = Document()
    document.add_paragraph("Додаток Д. Енергетичні показники")
    document.add_paragraph("Додаток Д. Розрахунок показників")
    document.save(source)
    project = TranslationMemory().create_project("Reference audit", "uk", "en") if use_memory else None
    options = Options("uk", "en", review=False, auto_glossary=False,
                      memory_project_id=project.id if project else "")
    settings = Settings(api_key="offline-reference-test", base_url="http://127.0.0.1:1/v1", model="fake")

    class Provider:
        input_tokens = output_tokens = calls = 0

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls += 1
            return {"segments": [
                {"id": item["id"], "text": item["text"]
                 .replace("Додаток Д. Енергетичні показники", "Appendix E. Energy indicators")
                 .replace("Додаток Д. Розрахунок показників", "Appendix D. Indicator calculations")}
                for item in payload["segments"]
            ]}

    provider = Provider()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert provider.calls == 1
    assert [issue.code for issue in result.issues if issue.code in REFERENCE_CODES] == ["reference_consistency"]
    source_bytes, output_bytes = source.read_bytes(), result.output.read_bytes()
    second = result.segments[1]
    edit = edit_translation_markup(result.translations[second.id], "Appendix E. Indicator calculations")
    revised = export_reviewed_copy(source, result.output, result.report, {second.id: edit})
    assert not [issue for issue in revised.issues if issue.code in REFERENCE_CODES]
    assert Document(revised.output).paragraphs[1].text == "Appendix E. Indicator calculations"
    assert provider.calls == 1 and source.read_bytes() == source_bytes and result.output.read_bytes() == output_bytes
