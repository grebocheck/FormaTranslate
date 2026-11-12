from __future__ import annotations

import json
import re
from xml.sax.saxutils import escape

import pytest
from docx import Document
from support import FakeProvider, local_settings

from forma.core.models import (
    Cancelled,
    CapacityError,
    Issue,
    Options,
    Segment,
    TranslationFailure,
    plain_text,
)
from forma.documents.ooxml import OfficeDocument
from forma.translation.budget import validate_segment_sizes
from forma.translation.engine import TranslationEngine
from forma.translation.fragments import (
    MAX_FRAGMENT_DEPTH,
    MAX_REJECTED_SNAPSHOTS,
    FragmentCache,
    fragment_plan,
)
from forma.translation.quality import NUMBERS, immutable_spans, parse_translation
from forma.translation.quantities import unit_spans


def test_fragment_plans_roundtrip_markup_whitespace_and_protected_boundaries():
    sources = [
        "<r0>" + "Equipment at 1 234,56 кВт · год uses AB-42 and https://example.org/a?b=2. " * 200 + "</r0>",
        "".join(f"<r{i}>{escape(char)}</r{i}>" for i, char in enumerate("A & B. Next sentence. " * 120)),
        "<r0>" + "Before marker. " * 500 + "</r0><k0/><r1>" + "After marker. " * 500 + "</r1>",
        "<r0>A cross-run code РУ-</r0><r1>" + "0,4 then prose. " * 600 + "</r1>",
    ]
    for source in sources:
        segment = Segment("parent", "paragraph", source)
        plan = fragment_plan(segment)
        assert plan is not None and len(plan.parts) > 1
        assert plan.assemble({part.segment.id: part.segment.source for part in plan.parts}) == source
        protected = immutable_spans(segment.plain)
        intervals = [(a, b) for a, b, _ in protected]
        intervals += [match.span() for match in NUMBERS.finditer(segment.plain)]
        intervals += [(unit.number.start() if unit.number is not None else unit.start, unit.end)
                      for unit in unit_spans(segment.plain, NUMBERS, protected)]
        for part in plan.parts:
            assert not any(start < part.start < end or start < part.end < end for start, end in intervals)
    long_url = Segment("url", "paragraph", "<r0>Reference https://example.org/" + "a" * 30000 + " then text.</r0>")
    assert fragment_plan(long_url) is None and validate_segment_sizes([long_url]) == {}
    fragmented_url = Segment("url-runs", "paragraph", "<r0>See </r0>" + "".join(
        f"<r{index}>{char}</r{index}>" for index, char in enumerate("https://example.org/" + "a" * 800, 1)
    ) + "<r1000> now.</r1000>")
    plan = fragment_plan(fragmented_url)
    assert plan is not None and any(part.fixed for part in plan.parts)
    assert plan.assemble({part.segment.id: part.segment.source for part in plan.parts}) == fragmented_url.source
    cjk = Segment("cjk", "paragraph", "<r0>" + "設備運行正常。" * 1500 + "</r0>")
    plan = fragment_plan(cjk)
    outputs = {part.segment.id: part.segment.source for part in plan.parts}
    assert plan.assemble(outputs) == cjk.source
    assert "。 " in plan.assemble(outputs, target_language="en")
    assert plan.assemble(outputs, target_language="ja") == cjk.source


def test_fragmented_ocr_paragraph_resumes_drafts_and_review_without_leaking_ids(tmp_path):
    source = tmp_path / "ocr-runs.docx"
    doc = Document()
    paragraph = doc.add_paragraph()
    for index, char in enumerate("This is a formatted paragraph. " * 80):
        paragraph.add_run(char).bold = bool(index % 2)
    doc.save(source)
    original = OfficeDocument(source).segments[0]
    count = len(fragment_plan(original).parts)
    assert len(original.source) > 26000
    # Identity output isolates formatting/restart behavior from language repair.
    settings, options = local_settings(), Options(source_language="en", target_language="en", auto_glossary=False)
    with pytest.raises(Cancelled):
        TranslationEngine(settings, provider=FakeProvider(interrupt_after=2)).translate(source, tmp_path / "out", options)
    checkpoint = next((tmp_path / "appdata/checkpoints").glob("*.json"))
    first = json.loads(checkpoint.read_text())
    assert original.id not in first.get("drafts", {}) and original.id not in first.get("translations", {})
    assert sum(len(entry.get("drafts", {})) for parent in first["fragment_stages"].values()
               for entry in parent["plans"].values()) == 2

    class InterruptReview(FakeProvider):
        review_calls = 0

        def complete(self, instruction, payload, schema, **kwargs):
            if "drafts" in payload:
                self.review_calls += 1
                if self.review_calls > 2:
                    raise Cancelled("Interrupted review")
            return super().complete(instruction, payload, schema, **kwargs)

    second = InterruptReview()
    with pytest.raises(Cancelled):
        TranslationEngine(settings, provider=second).translate(source, tmp_path / "out", options)
    assert sum("drafts" not in payload for _, payload in second.calls) == count - 2
    interim = json.loads(checkpoint.read_text())
    assert interim["drafts"][original.id] == original.source
    assert original.id not in interim.get("translations", {})
    resumed = FakeProvider()
    result = TranslationEngine(settings, provider=resumed).translate(source, tmp_path / "out", options)
    assert len(resumed.calls) == count - 2 and all("drafts" in payload for _, payload in resumed.calls)
    assert result.translations == {original.id: original.source}
    assert set(parse_translation(original.source, result.translations[original.id])) == set(parse_translation(original.source, original.source))
    assert "::fragment:" not in result.report.read_text()
    assert [run.bold for run in Document(result.output).paragraphs[0].runs] == [run.bold for run in paragraph.runs]
    assert set(json.loads(checkpoint.read_text())["translations"]) == {original.id}


def test_legacy_whole_draft_is_retained_while_private_piece_drafts_are_regenerated(tmp_path):
    source = tmp_path / "legacy-draft.docx"
    doc = Document()
    doc.add_paragraph("A sentence about equipment. " * 500)
    doc.save(source)
    segment = OfficeDocument(source).segments[0]
    count = len(fragment_plan(segment).parts)
    settings, options = local_settings(), Options(source_language="en", target_language="en", auto_glossary=False)
    with pytest.raises(Cancelled):
        TranslationEngine(settings, provider=FakeProvider(interrupt_after=count)).translate(source, tmp_path / "out", options)
    checkpoint = next((tmp_path / "appdata/checkpoints").glob("*.json"))
    state = json.loads(checkpoint.read_text())
    state.pop("fragment_stages")  # A pre-fragmentation checkpoint had only a whole draft.
    legacy = "<r0>Legacy draft with unrelated length and wording.</r0>"
    state["drafts"][segment.id] = legacy
    checkpoint.write_text(json.dumps(state))
    provider = FakeProvider()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert len(provider.calls) == count * 2
    assert all("Legacy draft" not in json.dumps(payload) for _, payload in provider.calls)
    assert json.loads(checkpoint.read_text())["drafts"][segment.id] == legacy
    assert set(result.translations) == {segment.id}


def test_adaptive_fragment_limits_decrease_are_durable_and_stop_at_a_bound():
    segment = Segment("original", "paragraph", "<r0>" + "A sentence about equipment. " * 500 + "</r0>")
    cache, saved = FragmentCache(), {}

    class Limited(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            if any(len(item["text"]) > 1800 for item in payload["segments"]):
                self.calls.append((instruction, payload))
                raise CapacityError("output")
            return super().complete(instruction, payload, schema, **kwargs)

    provider = Limited()
    engine = TranslationEngine(local_settings())
    engine._stage([segment], {}, Options(review=False), provider, saved.update, fragment_cache=cache)
    assert saved == {segment.id: segment.source}
    assert len(cache.state["fragment_stages"]) > 1
    reused = FakeProvider(interrupt_after=0)
    engine._stage([segment], {}, Options(review=False), reused, saved.update,
                  fragment_cache=FragmentCache(json.loads(json.dumps(cache.state))))
    assert not reused.calls

    class AlwaysLimited(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            raise CapacityError("context")

    blocked = AlwaysLimited()
    with pytest.raises(TranslationFailure) as caught:
        engine._stage([segment], {}, Options(review=False), blocked, lambda _: pytest.fail("Accepted parent"),
                      fragment_cache=FragmentCache())
    assert len(blocked.calls) <= MAX_FRAGMENT_DEPTH + 1
    assert [item.id for item in caught.value.segments] == [segment.id]
    assert all(issue.segment_id == segment.id for issue in caught.value.issues)


def test_whole_parent_omission_is_repaired_before_accepting_individually_valid_pieces():
    source = "".join(f"<r{i}>{char}</r{i}>" for i, char in enumerate("abc 1 " * 15))
    segment = Segment("paragraph", "paragraph", source)
    plan = fragment_plan(segment, limit=128)
    cache, accepted = FragmentCache(), []
    cache.save(plan, "finals", {})  # A previously learned provider capacity.

    class DropsProse(FakeProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            values = []
            for item in payload["segments"]:
                candidate = item["text"]
                if not payload.get("paragraph_validation_errors"):
                    candidate = re.sub(r">[^<]*<", lambda match: re.sub(r"[a-z]", "", match.group()), candidate)
                values.append({"id": item["id"], "text": candidate})
            return {"segments": values}

    provider = DropsProse()
    result = TranslationEngine(local_settings())._stage(
        [segment], {}, Options(review=False), provider, lambda values: accepted.append(dict(values)),
        fragment_cache=cache,
    )
    assert result == {segment.id: source} and accepted == [result]
    assert any(payload.get("paragraph_validation_errors") for _, payload in provider.calls)
    entry = cache.state["fragment_stages"][segment.id]["plans"][plan.fingerprint]
    assert len(entry["rejected"]) == 1 and entry["rejected"][0]["stage"] == "finals"
    assert len(entry["finals"]) == len(plan.parts)
    assert not entry.get("paragraph_feedback")
    for _ in range(10):
        cache.save(plan, "finals", {plan.parts[0].segment.id: plan.parts[0].segment.source})
        cache.reject(plan, "finals", [Issue("missing_text", "Rejected fixture", segment.id, "error")])
    assert len(entry["rejected"]) == MAX_REJECTED_SNAPSHOTS and entry["rejection_count"] == 11

    # Each sparse piece loses fewer than 30 letters and remains hard-valid,
    # while the assembled paragraph loses all prose. Earlier completed soft
    # revisions must not bypass the new whole-paragraph corrective feedback.
    sparse = Segment("sparse", "paragraph", "<r0>" + ("abcdefghijklmnopqrst" + " " * 90 + "1 ") * 3 + "</r0>")
    sparse_plan = fragment_plan(sparse, limit=128)
    class DropsTwice(DropsProse):
        def __init__(self):
            super().__init__()
            self.feedback_calls = {}

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, payload))
            if payload.get("paragraph_validation_errors") and not payload.get("editorial_feedback"):
                assert any(entry.get("completed") is True and plain_text(entry.get("candidate", "")).strip() == "1"
                           for entry in sparse_cache.state["editorial_revisions"].values())
            values = []
            for item in payload["segments"]:
                if payload.get("paragraph_validation_errors"):
                    self.feedback_calls[item["id"]] = self.feedback_calls.get(item["id"], 0) + 1
                text = item["text"].replace("abcdefghijklmnopqrst", "перекладенийфрагмент") if self.feedback_calls.get(item["id"], 0) >= 3 else re.sub(
                    r">[^<]*<", lambda match: re.sub(r"[a-z ]", "", match.group()), item["text"],
                )
                values.append({"id": item["id"], "text": text})
            return {"segments": values}

    sparse_cache, sparse_saved, sparse_provider = FragmentCache(), [], DropsTwice()
    sparse_cache.save(sparse_plan, "finals", {})
    repaired = TranslationEngine(local_settings())._stage(
        [sparse], {}, Options(source_language="en", target_language="uk"), sparse_provider,
        lambda values: sparse_saved.append(dict(values)), drafts={sparse.id: sparse.source},
        fragment_cache=sparse_cache,
    )
    assert repaired == {sparse.id: sparse.source.replace("abcdefghijklmnopqrst", "перекладенийфрагмент")}
    assert sparse_saved == [repaired]
    assert any(payload.get("editorial_feedback") for _, payload in sparse_provider.calls)
    assert {payload["paragraph_repair_revisions"][-1] for _, payload in sparse_provider.calls
            if payload.get("paragraph_validation_errors")} == {1, 2}
    assert set(sparse_provider.feedback_calls.values()) == {3}
    assert len(sparse_cache.state["editorial_revisions"]) <= len(sparse_plan.parts) + 1
    after_private_completion = FakeProvider(invalid=True)
    assert TranslationEngine(local_settings())._stage(
        [sparse], {}, Options(source_language="en", target_language="uk"), after_private_completion,
        lambda _: None, drafts={sparse.id: sparse.source}, fragment_cache=sparse_cache,
    ) == repaired
    assert not after_private_completion.calls
