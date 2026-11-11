from __future__ import annotations

import hashlib
import json
from zipfile import ZipFile

import pytest
from docx import Document

from forma.core.models import FormaError, Options, TranslationResult, plain_text
from forma.documents.editor import edit_translation_markup
from forma.documents.ooxml import OfficeDocument
from forma.memory.importing import import_reference, prefer_review_segment
from forma.memory.store import MemoryConflict, TranslationMemory
from forma.translation.reports import write_translation_report


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rewrite_part(path, part, change):
    with ZipFile(path) as archive:
        infos, comment = archive.infolist(), archive.comment
        data = {info.filename: archive.read(info) for info in infos}
    data[part] = change(data[part])
    with ZipFile(path, "w") as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(info, data[info.filename])


def metadata(pair, change):
    data = json.loads(pair[2].read_text())
    change(data)
    pair[2].write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


@pytest.fixture
def pair(tmp_path):
    source = tmp_path / "source.docx"
    document = Document()
    document.add_heading("Equipment maintenance instructions", 1)
    paragraph = document.add_paragraph()
    paragraph.add_run("The ")
    paragraph.add_run("pump").bold = True
    paragraph.add_run(" operates at 25 kW, HM-200.")
    paragraph.add_run().add_tab()
    paragraph.add_run("Inspect before starting.")
    document.add_paragraph("Switch off the power before opening the housing.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Model"
    table.cell(0, 1).text = "Rating"
    table.cell(1, 0).text = "HM-200"
    table.cell(1, 1).text = "10,5"
    document.save(source)
    office = OfficeDocument(source)
    replacements = {
        "Equipment maintenance instructions": "Інструкції з технічного обслуговування",
        "The ": "Цей ", "pump": "насос",
        " operates at 25 kW, HM-200.": " працює за потужності 25 kW, HM-200.",
        "Inspect before starting.": "Перевірте перед запуском.",
        "Switch off the power before opening the housing.": "Перед відкриттям корпусу вимкніть живлення.",
        "Model": "Модель", "Rating": "Параметр",
    }
    translations = {}
    for segment in office.segments:
        target = segment.source
        for old, new in replacements.items():
            target = target.replace(old, new)
        translations[segment.id] = target
    output = tmp_path / "source.uk.docx"
    office.write(translations, output)
    result = TranslationResult(source, output, office.segments, translations, [])
    write_translation_report(
        result, Options(source_language="en", target_language="uk", auto_glossary=False), "offline-fixture"
    )
    data = json.loads(result.report.read_text())
    data.update(source_sha256=digest(source), output_sha256=digest(output), effective_glossary={})
    # Report IDs deliberately have no relationship to document node indexes.
    for index, segment in enumerate(reversed(data["segments"])):
        segment["id"] = f"review-id-{1000 - index}"
        segment["context"] = "Untrusted report context must not become the memory context."
    result.report.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return source, output, result.report


@pytest.fixture
def memory(tmp_path):
    return TranslationMemory(tmp_path / "private-memory.sqlite3")


@pytest.fixture
def project(memory):
    return memory.create_project("Equipment", "en", "uk")


def first_segment(pair):
    data = json.loads(pair[2].read_text())
    return next(item for item in data["segments"] if "pump" in item["source"])


def legacy(pair):
    def remove(data):
        for key in ("source_sha256", "output_sha256"):
            data.pop(key, None)
    metadata(pair, remove)


def alternate(item):
    return edit_translation_markup(item["translation"], plain_text(item["translation"]).replace("насос", "надійний насос"))


def test_regular_import_is_reference_even_if_report_claims_review_or_manual_edits(pair, memory, project):
    metadata(pair, lambda data: data.update(reviewed=True, acknowledged=True, manual_edits=[item["id"] for item in data["segments"]]))
    before = [path.read_bytes() for path in pair]
    summary = import_reference(memory, project.id, *pair)
    entries = memory.list_entries(project.id)
    assert summary == {"inserted": len(entries), "approved": 0, "references": len(entries)}
    assert {entry.status for entry in entries} == {"reference"}
    snapshot = memory.snapshot(project.id)
    assert snapshot.approved_revision == 0
    assert all(memory.lookup(snapshot, entry.entry_key, "0" * 64) is None for entry in entries)
    assert [path.read_bytes() for path in pair] == before


def test_explicit_preferred_import_reconstructs_real_context_and_role(pair, memory, project):
    from forma.memory.context import segment_context, segment_role
    from forma.memory.store import exact_fingerprint, policy_fingerprint

    summary = import_reference(memory, project.id, *pair, preferred=True)
    entries = memory.list_entries(project.id)
    assert summary["approved"] == len(entries) and summary["references"] == 0
    assert {entry.status for entry in entries} == {"human_approved"}
    office = OfficeDocument(pair[0])
    policy = policy_fingerprint(Options(source_language="en", target_language="uk", auto_glossary=False), {})
    snapshot = memory.snapshot(project.id)
    for segment in office.segments:
        key = exact_fingerprint(policy, segment.source, segment_context(office, segment), segment_role(office, segment))
        hit = memory.lookup(snapshot, key, "0" * 64, preferred_only=True)
        assert hit is not None
        assert "Untrusted report context" not in hit.context
        assert hit.source_markup == segment.source
        assert hit.provenance["source_sha256"] == digest(pair[0])


def test_shuffled_legacy_report_ids_and_arrays_are_matched_by_full_source_identity(pair, memory, project):
    legacy(pair)
    metadata(pair, lambda data: data["segments"].reverse())
    import_reference(memory, project.id, *pair, preferred=True)
    body = next(entry for entry in memory.list_entries(project.id) if "pump" in entry.source_markup)
    assert "насос" in body.target and "25" in body.target and "<k0/>" in body.target


@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong_location", "wrong_source"])
def test_incomplete_or_ambiguous_report_never_imports_a_partial_batch(pair, memory, project, damage):
    def change(data):
        if damage == "missing":
            data["segments"].pop()
        elif damage == "duplicate":
            data["segments"][1].update(location=data["segments"][0]["location"], source=data["segments"][0]["source"])
        elif damage == "wrong_location":
            data["segments"][0]["location"] = "document · абзац 999"
        else:
            data["segments"][0]["source"] += "<r99>Unreported text</r99>"
    metadata(pair, change)
    with pytest.raises(FormaError):
        import_reference(memory, project.id, *pair, preferred=True)
    assert memory.list_entries(project.id) == []


@pytest.mark.parametrize("field", ["source_sha256", "output_sha256"])
def test_report_hashes_bind_the_selected_files(pair, memory, project, field):
    metadata(pair, lambda data: data.update({field: "0" * 64}))
    with pytest.raises(FormaError):
        import_reference(memory, project.id, *pair)
    assert memory.list_entries(project.id) == []


def test_legacy_binding_also_checks_omitted_numeric_cells(pair, memory, project):
    legacy(pair)
    rewrite_part(pair[0], "word/document.xml", lambda xml: xml.replace(b"10,5", b"11,5"))
    with pytest.raises(FormaError):
        import_reference(memory, project.id, *pair)
    assert memory.list_entries(project.id) == []


def test_externally_edited_other_target_cannot_be_silently_imported_or_promoted(pair, memory, project):
    legacy(pair)
    rewrite_part(pair[1], "word/document.xml", lambda xml: xml.replace("Модель".encode(), "Інша модель".encode()))
    item = first_segment(pair)
    with pytest.raises(FormaError):
        prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=item["translation"])
    assert memory.list_entries(project.id) == []


@pytest.mark.parametrize("damage", ["number", "control", "empty"])
def test_manual_preference_rejects_numeric_code_and_format_damage(pair, memory, project, damage):
    item = first_segment(pair)
    changed = {"number": item["translation"].replace("25", "250"),
               "control": item["translation"].replace("<k0/>", ""), "empty": ""}[damage]
    with pytest.raises(FormaError):
        prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=changed)
    assert memory.list_entries(project.id) == []


def test_a_valid_unsaved_manual_draft_can_be_preferred_without_modifying_documents(pair, memory, project):
    item = first_segment(pair)
    draft = alternate(item)
    before = [path.read_bytes() for path in pair]
    result = prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=draft,
                                  expected_report_fingerprint=digest(pair[2]))
    entries = memory.list_entries(project.id)
    assert result == {"inserted": 1, "approved": 1, "references": 0}
    assert len(entries) == 1 and entries[0].target == draft
    assert entries[0].status == "human_approved"
    assert [path.read_bytes() for path in pair] == before


def test_legacy_unknown_auto_glossary_is_reference_only(pair, memory, project):
    metadata(pair, lambda data: (data.pop("effective_glossary", None), data["options"].update(auto_glossary=True)))
    import_reference(memory, project.id, *pair)
    with pytest.raises(FormaError, match="глосарію"):
        import_reference(memory, project.id, *pair, preferred=True)
    assert {entry.status for entry in memory.list_entries(project.id)} == {"reference"}


def test_changed_glossary_or_wrong_project_language_cannot_be_preferred(pair, memory, project):
    memory.update_glossary(project.id, {"pump": "помпа"})
    with pytest.raises(FormaError, match="глосарій"):
        import_reference(memory, project.id, *pair, preferred=True)
    other = memory.create_project("Polish", "en", "pl")
    with pytest.raises(FormaError):
        import_reference(memory, other.id, *pair)
    assert memory.list_entries(project.id) == memory.list_entries(other.id) == []


def test_replacement_requires_conflict_tokens_and_preserves_approved_history(pair, memory, project):
    item = first_segment(pair)
    prefer_review_segment(memory, project.id, *pair, item["id"])
    draft = alternate(item)
    with pytest.raises(MemoryConflict) as caught:
        prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=draft)
    conflict = caught.value
    assert conflict.current_target == item["translation"] and conflict.new_target == draft
    assert type(conflict.expected_revision) is type(conflict.expected_glossary_revision) is int
    with pytest.raises(FormaError):
        prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=draft, replace=True)
    prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=draft,
                          replace=True, expected_revision=conflict.expected_revision,
                          expected_glossary_revision=conflict.expected_glossary_revision)
    history = memory.history(project.id, conflict.entry_key)
    assert [entry.target for entry in history] == [draft, item["translation"]]


def test_stale_conflict_tokens_cannot_replace_a_newer_human_decision(pair, memory, project):
    item = first_segment(pair)
    prefer_review_segment(memory, project.id, *pair, item["id"])
    draft = alternate(item)
    with pytest.raises(MemoryConflict) as caught:
        prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=draft)
    conflict = caught.value
    other = next(value for value in json.loads(pair[2].read_text())["segments"] if value["id"] != item["id"])
    prefer_review_segment(memory, project.id, *pair, other["id"])
    before = memory.list_entries(project.id)
    with pytest.raises(FormaError, match="Пам’ять змінилася"):
        prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=draft,
                              replace=True, expected_revision=conflict.expected_revision,
                              expected_glossary_revision=conflict.expected_glossary_revision)
    assert memory.list_entries(project.id) == before


def test_bulk_approval_conflict_rolls_back_all_preceding_entries(pair, memory, project):
    item = first_segment(pair)
    prefer_review_segment(memory, project.id, *pair, item["id"], translation_override=alternate(item))
    before = memory.list_entries(project.id)
    revision = memory.snapshot(project.id).approved_revision
    with pytest.raises(MemoryConflict):
        import_reference(memory, project.id, *pair, preferred=True)
    assert memory.list_entries(project.id) == before
    assert memory.snapshot(project.id).approved_revision == revision


def test_report_change_after_opening_and_during_verification_is_rejected(pair, memory, project, monkeypatch):
    fingerprint = digest(pair[2])
    pair[2].write_bytes(pair[2].read_bytes() + b"\n")
    item = first_segment(pair)
    with pytest.raises(FormaError, match="змінилися"):
        prefer_review_segment(memory, project.id, *pair, item["id"], expected_report_fingerprint=fingerprint)
    legacy(pair)
    write = OfficeDocument.write

    def race(self, translations, output, **kwargs):
        write(self, translations, output, **kwargs)
        pair[2].write_bytes(pair[2].read_bytes() + b"\n")

    monkeypatch.setattr(OfficeDocument, "write", race)
    with pytest.raises(FormaError, match="змінилися"):
        import_reference(memory, project.id, *pair)
    assert memory.list_entries(project.id) == []


def test_duplicate_json_keys_are_not_interpreted_as_an_approval_or_policy(pair, memory, project):
    raw = pair[2].read_text()
    pair[2].write_text(raw.replace('"options": {', '"options": {}, "options": {', 1))
    with pytest.raises(FormaError):
        import_reference(memory, project.id, *pair, preferred=True)
    assert memory.list_entries(project.id) == []


@pytest.mark.parametrize("preferred", ["false", 1, None])
def test_approval_requires_an_explicit_boolean_action(pair, memory, project, preferred):
    with pytest.raises(FormaError):
        import_reference(memory, project.id, *pair, preferred=preferred)
    assert memory.list_entries(project.id) == []
