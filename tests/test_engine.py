from __future__ import annotations

import json
import threading
import time

import pytest
from support import FakeProvider, local_settings

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.ooxml import OfficeDocument
from forma.translation.engine import TranslationEngine, read_response


def test_complete_pipeline_review_glossary_report_and_cache(docx_file, tmp_path):
    provider = FakeProvider()
    options = Options(glossary={"heat meter": "лічильник теплової енергії"})
    result = TranslationEngine(local_settings(), provider=provider).translate(
        docx_file, tmp_path / "output", options
    )
    assert result.output.exists()
    assert result.report.exists()
    assert result.report.with_suffix(".html").exists()
    assert any("bilingual editor" in call[0] for call in provider.calls)
    translation_call = next(payload for _, payload in provider.calls if "segments" in payload)
    assert translation_call["glossary"]["heat meter"] == "лічильник теплової енергії"
    assert translation_call["document_context"]
    assert len(result.translations) == len(OfficeDocument(docx_file).segments)
    next_provider = FakeProvider()
    again = TranslationEngine(local_settings(), provider=next_provider).translate(
        docx_file, tmp_path / "output", options
    )
    assert again.output != result.output
    assert again.cached_segments == len(result.segments)
    assert not next_provider.calls
    data = json.loads(result.report.read_text())
    assert data["model"] == "test-model"
    assert "api_key" not in data


@pytest.mark.parametrize("damage", ["glossary", "deep-json"])
def test_damaged_checkpoint_recovers_and_keeps_valid_paid_fragments(docx_file, tmp_path, damage):
    from forma.settings.config import data_directory

    settings, options = local_settings(), Options(review=False)
    first = TranslationEngine(settings, provider=FakeProvider()).translate(
        docx_file, tmp_path / "output", options,
    )
    checkpoint = next((data_directory() / "checkpoints").glob("*.json"))
    if damage == "glossary":
        state = json.loads(checkpoint.read_text())
        state["glossary"] = []
        state["translations"].pop(next(iter(state["translations"])))
        checkpoint.write_text(json.dumps(state))
        expected_cached = len(first.segments) - 1
    else:
        checkpoint.write_text('[' * 20000 + '0' + ']' * 20000)
        expected_cached = 0
    provider = FakeProvider()
    result = TranslationEngine(settings, provider=provider).translate(
        docx_file, tmp_path / "output", options,
    )
    assert result.output.exists()
    assert result.cached_segments == expected_cached
    assert len(result.translations) == len(first.segments)
    assert isinstance(json.loads(checkpoint.read_text())["glossary"], dict)


def test_invalid_translation_blocks_export(docx_file, tmp_path):
    provider = FakeProvider(invalid=True)
    with pytest.raises(FormaError, match="Експорт зупинено"):
        TranslationEngine(local_settings(), provider=provider).translate(
            docx_file, tmp_path / "output", Options(review=False, auto_glossary=False)
        )
    assert len(provider.calls) == 3
    assert not list((tmp_path / "output").glob("*.docx"))


def test_resume_reuses_completed_batches(docx_file, tmp_path):
    options = Options(review=False, auto_glossary=False)
    provider = FakeProvider(interrupt_after=1)
    with pytest.raises(Cancelled):
        TranslationEngine(local_settings(), provider=provider).translate(
            docx_file, tmp_path / "output", options
        )
    next_provider = FakeProvider()
    result = TranslationEngine(local_settings(), provider=next_provider).translate(
        docx_file, tmp_path / "output", options
    )
    assert result.cached_segments == 8
    assert len(next_provider.calls) < len(provider.calls) + 2


def test_duplicate_or_missing_ids_are_rejected(docx_file):
    batch = OfficeDocument(docx_file).segments[:2]
    with pytest.raises(FormaError):
        read_response({"segments": [{"id": batch[0].id, "text": "x"}] * 2}, batch)


def test_changed_options_do_not_reuse_another_language(docx_file, tmp_path):
    TranslationEngine(local_settings(), provider=FakeProvider()).translate(
        docx_file, tmp_path / "output", Options(review=False, auto_glossary=False)
    )
    result = TranslationEngine(local_settings(), provider=FakeProvider()).translate(
        docx_file,
        tmp_path / "output",
        Options(target_language="pl", review=False, auto_glossary=False),
    )
    assert result.cached_segments == 0


def test_context_samples_end_of_document_without_duplicate_boilerplate():
    from forma.core.models import Segment
    from forma.translation.engine import document_context

    segments = [
        Segment(str(i), f"paragraph {i}", f"<r0>Heading {i} {'text ' * 120}</r0>")
        for i in range(80)
    ]
    segments += [Segment("end", "Appendix", "<r0>Warranty exclusions and indemnification</r0>")]
    context = document_context(segments)
    assert "Heading 0" in context
    assert "Warranty exclusions" in context
    assert len(context) <= 10000


def test_identifiers_locked_in_both_translation_and_review(tmp_path):
    from docx import Document

    source = tmp_path / "codes.docx"
    doc = Document()
    doc.add_paragraph("Насос ТН-42: https://example.com/{asset}; документ звіт.docx")
    doc.save(source)

    class IdentifierProvider(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            assert "ТН-42" not in payload["segments"][0]["text"]
            assert "__FORMA_LOCK_0__" in payload["segments"][0]["text"]
            if "drafts" in payload:
                assert "ТН-42" not in next(iter(payload["drafts"].values()))
            self.calls.append((instruction, payload))
            return {
                "segments": [
                    {
                        "id": s["id"],
                        "text": s["text"].replace("Насос", "Pump").replace("документ", "document"),
                    }
                    for s in payload["segments"]
                ]
            }

    provider = IdentifierProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source,
        tmp_path / "out",
        Options(source_language="uk", target_language="en", auto_glossary=False),
    )
    assert len(provider.calls) == 2
    text = next(iter(result.translations.values()))
    assert "ТН-42" in text and "звіт.docx" in text
    assert "__FORMA_LOCK" not in text


def test_review_repairs_only_failed_segment_and_never_retranslates(tmp_path):
    from docx import Document

    source = tmp_path / "values.docx"
    doc = Document()
    doc.add_paragraph("Витрата 16 м³/год")
    doc.add_paragraph("Тиск 20 бар")
    doc.save(source)

    class RepairProvider(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            segments = [
                {
                    "id": s["id"],
                    "text": s["text"]
                    .replace("Витрата", "Flow")
                    .replace("Тиск", "Pressure")
                    .replace("м³/год", "m³/h")
                    .replace("бар", "bar"),
                }
                for s in payload["segments"]
            ]
            if len(self.calls) == 2:
                segments[1]["text"] = segments[1]["text"].replace("20", "200")
            return {"segments": segments}

    provider = RepairProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source,
        tmp_path / "out",
        Options(source_language="uk", target_language="en", auto_glossary=False),
    )
    assert [len(payload["segments"]) for _, payload in provider.calls] == [2, 2, 1]
    assert "drafts" in provider.calls[2][1]
    assert provider.calls[2][1]["validation_errors"]
    assert not [i for i in result.issues if i.severity == "error"]


def test_interrupted_review_resumes_saved_draft_without_translation(docx_file, tmp_path):
    options = Options(review=True, auto_glossary=False)
    with pytest.raises(Cancelled):
        TranslationEngine(local_settings(), provider=FakeProvider(interrupt_after=1)).translate(
            docx_file, tmp_path / "out", options
        )
    provider = FakeProvider()
    TranslationEngine(local_settings(), provider=provider).translate(
        docx_file, tmp_path / "out", options
    )
    assert "drafts" in provider.calls[0][1]
    assert len(provider.calls[0][1]["drafts"]) == 8


def test_unchanged_foreign_prose_is_flagged_in_report_with_location(tmp_path):
    from docx import Document

    source = tmp_path / "untranslated.docx"
    doc = Document()
    doc.add_paragraph(
        "Постачальник зобов’язаний повідомити замовника про зміну строків виконання робіт до завершення поточного місяця."
    )
    doc.save(source)
    provider = FakeProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source,
        tmp_path / "out",
        Options(source_language="uk", target_language="en", review=False, auto_glossary=False),
    )
    assert len(provider.calls) == 1
    assert result.issues[0].code == "untranslated_prose"
    assert result.issues[0].severity == "warning"
    assert result.issues[0].segment_id == result.segments[0].id
    assert (
        f'href="#segment-{result.segments[0].id}"' in result.report.with_suffix(".html").read_text()
    )


def test_reviewer_can_correct_usable_but_inaccurate_primary_draft(tmp_path):
    from docx import Document

    source = tmp_path / "review-repair.docx"
    doc = Document()
    doc.add_paragraph(
        "Гарантійний строк становить 24 місяці та обчислюється з дати підписання акта приймання обладнання замовником."
    )
    doc.save(source)

    class EditorProvider(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            if "drafts" not in payload:
                text = payload["segments"][0]["text"].replace("24", "240")
            else:
                assert payload["draft_issues"]
                text = "<r0>The warranty period is 24 months and starts on the date the customer signs the equipment acceptance certificate.</r0>"
            return {"segments": [{"id": payload["segments"][0]["id"], "text": text}]}

    provider = EditorProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source,
        tmp_path / "out",
        Options(source_language="uk", target_language="en", auto_glossary=False),
    )
    assert len(provider.calls) == 2
    assert not [i for i in result.issues if i.severity == "error"]
    assert "24 months" in next(iter(result.translations.values()))


def test_decimal_locale_is_consistent_between_prose_and_numeric_table_cells(tmp_path):
    from docx import Document

    source = tmp_path / "decimal.docx"
    doc = Document()
    doc.add_paragraph("Напруга РУ-0,4: 0,40 кВ; див. specification_0,4.docx")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Напруга"
    table.cell(0, 1).text = "0,40"
    doc.save(source)
    provider = FakeProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source,
        tmp_path / "out",
        Options(source_language="uk", target_language="en", auto_glossary=False),
    )
    output = Document(result.output)
    assert "РУ-0,4: 0.40" in output.paragraphs[0].text
    assert output.tables[0].cell(0, 1).text == "0.40"
    assert "specification_0,4.docx" in output.paragraphs[0].text


def test_parallel_batches_keep_review_order_repair_and_checkpoint_complete(tmp_path):
    from docx import Document

    source = tmp_path / "parallel.docx"
    doc = Document()
    for i in range(32):
        doc.add_paragraph(f"Item {i}")
    doc.save(source)
    settings = local_settings().with_overrides(max_parallel_requests=3)
    options = Options(source_language="en", target_language="uk")

    class ConcurrentProvider:
        def __init__(self):
            self.input_tokens = self.output_tokens = 0
            self.lock = threading.Lock()
            self.barrier = threading.Barrier(3)
            self.active = self.peak = self.translations_started = 0
            self.glossary_done = False
            self.translated_ids = set()
            self.calls = []
            self.repaired = False

        def complete(self, instruction, payload, schema, **kwargs):
            with self.lock:
                self.input_tokens += 10
                self.output_tokens += 5
                if "terms" in schema["properties"]:
                    self.glossary_done = True
                    return {"terms": []}
                assert self.glossary_done
                ids = {s["id"] for s in payload["segments"]}
                review = "drafts" in payload
                if review:
                    assert ids <= self.translated_ids
                    assert ids == set(payload["drafts"])
                self.calls.append((review, ids))
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.translations_started += int(not review)
                wait_at_barrier = not review and self.translations_started <= 3
            try:
                if wait_at_barrier:
                    self.barrier.wait(timeout=3)
                time.sleep(0.005)
                values = [
                    {"id": s["id"], "text": s["text"].replace("Item", "Елемент")}
                    for s in payload["segments"]
                ]
                with self.lock:
                    if not review:
                        self.translated_ids.update(ids)
                    elif not self.repaired and any(
                        s["text"] == "<r0>Елемент 0</r0>" for s in values
                    ):
                        self.repaired = True
                        next(s for s in values if s["text"] == "<r0>Елемент 0</r0>")["text"] = (
                            "<r0>Елемент 1000</r0>"
                        )
                return {"segments": values}
            finally:
                with self.lock:
                    self.active -= 1

    provider = ConcurrentProvider()
    progress = []
    result = TranslationEngine(settings, provider=provider).translate(
        source, tmp_path / "out", options, progress=lambda value, _: progress.append(value)
    )
    assert provider.peak == 3 and provider.active == 0
    assert len(result.translations) == 32
    assert len([call for call in provider.calls if not call[0]]) == 4
    assert [len(ids) for review, ids in provider.calls if review].count(1) == 1
    assert result.input_tokens == 100 and result.output_tokens == 50
    assert progress == sorted(progress)
    checkpoints = list((tmp_path / "appdata/checkpoints").glob("*.json"))
    state = json.loads(checkpoints[0].read_text())
    assert state["translations"] == result.translations
    assert len(state["drafts"]) == 32
    cached_provider = FakeProvider()
    cached = TranslationEngine(settings, provider=cached_provider).translate(
        source, tmp_path / "out", options
    )
    assert cached.cached_segments == 32 and not cached_provider.calls


def test_failed_parallel_batch_drains_checkpoint_without_cancelling_other_documents(tmp_path):
    from docx import Document

    from forma.providers.client import JobControl

    source = tmp_path / "failure.docx"
    doc = Document()
    for i in range(40):
        doc.add_paragraph(f"Item {i}")
    doc.save(source)
    settings = local_settings().with_overrides(max_parallel_requests=2)
    control = JobControl(2)
    options = Options(review=True, auto_glossary=False)

    class FailingProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(2)
            self.failure = threading.Event()
            self.completed_review = False

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            first_id = payload["segments"][0]["id"]
            if "drafts" not in payload:
                self.barrier.wait(timeout=3)
                if first_id.endswith("#0"):
                    self.failure.set()
                    raise FormaError("Deliberate batch failure")
                assert self.failure.wait(2)
                # Let the coordinator observe the failed task while its sibling drains.
                time.sleep(0.03)
            else:
                self.completed_review = True
            return {"segments": [{"id": s["id"], "text": s["text"]} for s in payload["segments"]]}

    provider = FailingProvider()
    with pytest.raises(FormaError, match="Deliberate"):
        TranslationEngine(settings, control, provider=provider).translate(
            source, tmp_path / "out", options
        )
    assert provider.completed_review
    assert len(provider.calls) == 3  # Two initial translations and the successful sibling's review.
    assert not control.cancelled.is_set()
    assert not list((tmp_path / "out").glob("*.docx"))
    state = json.loads(next((tmp_path / "appdata/checkpoints").glob("*.json")).read_text())
    assert len(state["translations"]) == 8
    resumed = TranslationEngine(settings, control, provider=FakeProvider()).translate(
        source, tmp_path / "out", options
    )
    assert resumed.cached_segments == 8 and len(resumed.translations) == 40
    with control.request_slots(2):
        pass  # No permits or cancellation leaked from the failed document.


@pytest.mark.parametrize("fault", ["missing", "duplicate", "invalid_text"])
def test_partial_review_keeps_valid_ids_and_repairs_only_ambiguous_segment(tmp_path, fault):
    from docx import Document

    source = tmp_path / "partial-response.docx"
    doc = Document()
    for index in range(8):
        doc.add_paragraph(f"Item {index}")
    doc.save(source)

    class PartialProvider(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            values = [
                {"id": s["id"], "text": s["text"].replace("Item", "Елемент")}
                for s in payload["segments"]
            ]
            if len(self.calls) == 2:
                if fault == "missing":
                    values.pop()
                elif fault == "duplicate":
                    values.append({**values[-1], "text": "<r0>Conflicting content 999</r0>"})
                else:
                    values[-1]["text"] = None
                # Unrequested IDs must never become document content or invalidate good IDs.
                values.append({"id": "unrequested-id", "text": "<r0>Injected text 999</r0>"})
            return {"segments": list(reversed(values))}

    provider = PartialProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source, tmp_path / "out", Options(auto_glossary=False)
    )
    assert [len(payload["segments"]) for _, payload in provider.calls] == [8, 8, 1]
    retry = provider.calls[-1][1]
    assert retry["segments"][0]["id"].endswith("#7")
    assert set(retry["drafts"]) == {retry["segments"][0]["id"]}
    assert len(result.translations) == 8
    assert "999" not in " ".join(result.translations.values())


def test_omitted_review_segment_never_allows_incomplete_export(tmp_path):
    from docx import Document

    source = tmp_path / "omitted.docx"
    doc = Document()
    for index in range(8):
        doc.add_paragraph(f"Item {index}")
    doc.save(source)

    class OmittingProvider(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            values = [{"id": s["id"], "text": s["text"]} for s in payload["segments"]]
            if "drafts" in payload:
                values = [s for s in values if not s["id"].endswith("#7")]
            return {"segments": values}

    provider = OmittingProvider()
    with pytest.raises(FormaError, match="Експорт зупинено"):
        TranslationEngine(local_settings(), provider=provider).translate(
            source, tmp_path / "out", Options(auto_glossary=False)
        )
    assert [len(payload["segments"]) for _, payload in provider.calls] == [8, 8, 1, 1]
    assert not list((tmp_path / "out").glob("*.docx"))
    state = json.loads(next((tmp_path / "appdata/checkpoints").glob("*.json")).read_text())
    assert len(state["translations"]) == 7
    resumed = TranslationEngine(local_settings(), provider=FakeProvider()).translate(
        source, tmp_path / "out", Options(auto_glossary=False)
    )
    assert resumed.cached_segments == 7 and len(resumed.translations) == 8


@pytest.mark.parametrize(
    ("cancel_stage", "fault", "final_count", "draft_count"),
    [
        ("translation", "valid", 0, 8),
        ("translation", "markup", 0, 7),
        ("translation", "malformed_json", 0, 0),
        ("review", "valid", 8, 8),
        ("review", "numbers", 7, 8),
        ("review", "markup", 7, 8),
    ],
)
def test_cancel_retains_only_valid_completed_response_without_starting_more_requests(
    tmp_path, cancel_stage, fault, final_count, draft_count
):
    import httpx
    from docx import Document

    from forma.providers.client import JobControl, Provider

    source = tmp_path / "cancel-completed.docx"
    doc = Document()
    for index in range(8):
        doc.add_paragraph(f"Value {index}")
    doc.save(source)
    control = JobControl(1)
    calls = []

    def handler(request):
        assert not control.cancelled.is_set(), "Cancellation started another request"
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        stage = "review" if "drafts" in payload else "translation"
        calls.append(stage)
        values = [
            {"id": s["id"], "text": s["text"].replace("Value", "Значення")}
            for s in payload["segments"]
        ]
        cancelling = stage == cancel_stage
        if cancelling:
            if fault == "markup":
                values[0]["text"] = values[0]["text"].replace("</r0>", "")
            elif fault == "numbers":
                values[0]["text"] = values[0]["text"].replace("</r0>", " 999</r0>")
            control.cancelled.set()
        text = (
            "{broken-json"
            if cancelling and fault == "malformed_json"
            else json.dumps({"segments": values})
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    provider = Provider(local_settings(), control, transport=httpx.MockTransport(handler))
    options = Options(auto_glossary=False)
    try:
        with pytest.raises(Cancelled):
            TranslationEngine(local_settings(), control, provider=provider).translate(
                source, tmp_path / "out", options
            )
        assert calls == (
            ["translation"] if cancel_stage == "translation" else ["translation", "review"]
        )
        assert provider.input_tokens == len(calls) * 10
    finally:
        provider.close()
    assert not list((tmp_path / "out").glob("*.docx"))
    checkpoints = list((tmp_path / "appdata/checkpoints").glob("*.json"))
    state = json.loads(checkpoints[0].read_text()) if checkpoints else {}
    assert len(state.get("translations", {})) == final_count
    assert len(state.get("drafts", {})) == draft_count
    resumed_provider = FakeProvider()
    resumed = TranslationEngine(local_settings(), provider=resumed_provider).translate(
        source, tmp_path / "out", options
    )
    assert resumed.cached_segments == final_count
    assert len(resumed.translations) == 8
    if final_count:
        assert all("drafts" in payload for _, payload in resumed_provider.calls)
        assert (
            sum(len(payload["segments"]) for _, payload in resumed_provider.calls)
            == 8 - final_count
        )


def test_cancellation_at_export_boundary_does_not_write_final_document(docx_file, tmp_path):
    from forma.providers.client import JobControl

    control = JobControl(1)

    def progress(percent, _):
        if percent == 96:
            control.cancelled.set()

    with pytest.raises(Cancelled):
        TranslationEngine(local_settings(), control, provider=FakeProvider()).translate(
            docx_file, tmp_path / "out", Options(auto_glossary=False), progress
        )
    assert not list((tmp_path / "out").glob("*.docx"))
