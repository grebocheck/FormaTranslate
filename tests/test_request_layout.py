"""Real HTTP payload boundaries and lossless memory-context packing, offline."""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from docx import Document

from forma.accounting.context import usage_context
from forma.accounting.usage import UsageLedger
from forma.core.models import FormaError, Options
from forma.documents.ooxml import OfficeDocument
from forma.memory.context import scoped_segment_context
from forma.memory.store import TranslationMemory
from forma.providers.client import TRANSLATION_SCHEMA, Provider
from forma.providers.request_layout import compact_memory_payload
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine, batched


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def user_payload(body, mode="chat"):
    result = {}
    for message in body["messages" if mode == "chat" else "input"]:
        if message["role"] != "user":
            continue
        content = message["content"]
        text = content if isinstance(content, str) else "".join(
            part.get("text", "") for part in content
        )
        result.update(json.loads(text))
    return result


def success(body, result=None):
    result = result or {"segments": []}
    usage = {"prompt_tokens": 1200, "completion_tokens": 20,
             "prompt_tokens_details": {"cached_tokens": 0, "cache_creation_tokens": 1100}}
    if "messages" in body:
        return httpx.Response(200, json={
            "model": body["model"], "usage": usage,
            "choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}],
        })
    return httpx.Response(200, json={
        "model": body["model"], "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": json.dumps(result)}]}],
    })


@pytest.mark.parametrize("mode", ["chat", "responses"])
def test_official_cache_prefix_stays_identical_and_dynamic_data_stays_untrusted(mode):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return success(body)

    settings = Settings(api_key="offline-key", api_mode=mode, model="gpt-6.1-terra")
    provider = Provider(settings, transport=httpx.MockTransport(handler))
    common = {"source_language": "en", "target_language": "uk", "domain": "Technical",
              "document_context": "Ignore previous instructions is a quoted document sentence.",
              "glossary": {"power": "потужність"}}
    try:
        for index in range(2):
            payload = {**common, "segments": [{"id": str(index), "text": f"<r0>Power {index}</r0>"}],
                       "drafts": {str(index): "Чернетка"}}
            assert provider.complete("Translate carefully.", payload, TRANSLATION_SCHEMA) == {"segments": []}
            assert user_payload(seen[-1], mode) == payload
        first = seen[0]["messages" if mode == "chat" else "input"]
        second = seen[1]["messages" if mode == "chat" else "input"]
        assert first[:2] == second[:2]
        assert first[-1] != second[-1]
        assert [message["role"] for message in first] == ["system", "user", "user"]
        assert "quoted document sentence" not in first[0]["content"][0]["text"]
        for message in first[:2]:
            assert message["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert isinstance(first[-1]["content"], str)
        assert seen[0]["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
        assert (seen[0].get("text") or seen[0].get("response_format")) is not None
    finally:
        provider.close()


@pytest.mark.parametrize("mode,base,model", [
    ("chat", "https://api.openai.com/v1", "gpt-4.1"),
    ("responses", "https://api.openai.com/v1", "gpt-6-future-unknown"),
    ("chat", "https://api.openai.com.example/v1", "gpt-6-luna"),
    ("responses", "http://localhost:1234/v1", "gpt-6.1-terra"),
    ("gemini", "https://api.openai.com/v1", "gpt-6-luna"),
    ("anthropic", "https://api.anthropic.com/v1", "gpt-6-luna"),
])
def test_unknown_models_and_nonofficial_connections_keep_original_wire_contract(mode, base, model):
    provider = Provider(Settings(api_key="offline-key", api_mode=mode, base_url=base, model=model))
    try:
        payload = {"source_language": "en", "segments": [{"id": "a", "text": "Hello"}]}
        body = provider._body("Translate", payload, TRANSLATION_SCHEMA, model)
        assert "prompt_cache_options" not in body
        messages = body["input" if mode == "responses" else "messages"]
        assert len(messages) == 2
        assert isinstance(messages[0]["content"], str)
        assert json.loads(messages[1]["content"]) == payload
    finally:
        provider.close()


@pytest.mark.parametrize("fixture", ["docx_file", "xlsx_file"])
def test_real_office_contexts_pack_losslessly_without_mutating_checkpoint_payload(fixture, request):
    document = OfficeDocument(request.getfixturevalue(fixture))
    packed_count = 0
    for batch in batched(document.segments):
        payload = {"memory_context_version": 2, "segments": [
            {"id": segment.id, "text": segment.source,
             "neighboring_context": scoped_segment_context(document, segment)}
            for segment in batch
        ]}
        original = copy.deepcopy(payload)
        packed, changed = compact_memory_payload(payload)
        assert payload == original
        if not changed:
            continue
        packed_count += 1
        assert len(json.dumps(packed, ensure_ascii=False).encode()) < len(json.dumps(payload, ensure_ascii=False).encode())
        restored = copy.deepcopy(packed)
        scopes = restored.pop("shared_scopes", {})
        for segment in restored["segments"]:
            context = segment["neighboring_context"]
            if isinstance(context, dict):
                if "scope_ref" in context:
                    context.update(scopes[context.pop("scope_ref")])
                if isinstance(context["context"], dict):
                    context["context"] = canonical(context["context"])
                segment["neighboring_context"] = canonical(context)
        assert restored == original
    assert packed_count > 0


def test_packing_retains_hidden_context_bindings_and_unknown_context_bytes():
    def payload(digest):
        context = canonical({"memory_context_version": 2,
                             "context": canonical({"facts": ["Visible context"], "selected_context_sha256": digest}),
                             "scope_sha256": "a" * 64, "scope_prompt": "Section facts " * 70,
                             "role_sha256": "b" * 64, "full_context_sha256": digest})
        return {"memory_context_version": 2, "segments": [
            {"id": str(index), "text": "Text", "neighboring_context": context}
            for index in range(3)
        ] + [{"id": "legacy", "text": "Text", "neighboring_context": '{"memory_context_version":99}'}]}

    first, changed = compact_memory_payload(payload("c" * 64))
    second, _ = compact_memory_payload(payload("d" * 64))
    assert changed
    assert first != second
    assert first["segments"][0]["neighboring_context"]["full_context_sha256"] == "c" * 64
    assert first["segments"][0]["neighboring_context"]["context"]["selected_context_sha256"] == "c" * 64
    assert first["segments"][-1]["neighboring_context"] == '{"memory_context_version":99}'


def test_explicit_cache_rejection_retries_only_the_capability_and_records_attempts():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {
                "code": "unsupported_parameter", "param": "prompt_cache_options",
                "message": "Unavailable for this deployment",
            }})
        return success(body)

    settings = Settings(api_key="offline-key", model="gpt-6-luna")
    provider = Provider(settings, transport=httpx.MockTransport(handler))
    payload = {"source_language": "en", "target_language": "uk", "segments": []}
    try:
        with usage_context(run_id="a" * 32, document_id="b" * 32, stage="review"):
            provider.complete("Translate", payload, TRANSLATION_SCHEMA)
        assert len(bodies) == 2
        assert "prompt_cache_options" not in bodies[1]
        assert bodies[1]["response_format"] == bodies[0]["response_format"]
        assert user_payload(bodies[0]) == user_payload(bodies[1]) == payload
        with UsageLedger().connect() as db:
            rows = [tuple(row) for row in db.execute("SELECT stage,reason,attempt FROM requests ORDER BY id")]
        assert rows == [("review", "initial", 1), ("review", "capability_retry", 2)]
    finally:
        provider.close()


def test_unrelated_provider_error_cannot_silently_disable_cache_controls():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {
            "code": "invalid_parameter", "param": "model",
            "message": "Document mentions prompt_cache_options",
        }})

    provider = Provider(Settings(api_key="offline-key", model="gpt-6-luna"),
                        transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FormaError, match="HTTP 400"):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA)
        assert len(requests) == 1
    finally:
        provider.close()


def test_http_memory_translation_preserves_exact_reuse_after_wire_projection(tmp_path):
    store = TranslationMemory()
    project = store.create_project("Offline project", "en", "uk", {})
    settings = Settings(api_key="offline-key", model="gpt-6-luna", review_model="gpt-6.1-terra")
    options = Options(source_language="en", target_language="uk", auto_glossary=False,
                      memory_project_id=project.id)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        payload = user_payload(body)
        requests.append(payload)
        return success(body, {"segments": [
            {"id": segment["id"], "text": segment["text"].replace("Energy", "Енергія")}
            for segment in payload["segments"]
        ]})

    provider = Provider(settings, transport=httpx.MockTransport(handler))
    try:
        results = []
        for filename in ("first.docx", "second.docx"):
            source = tmp_path / filename
            document = Document()
            document.core_properties.comments = filename
            for _ in range(6):
                document.add_paragraph("Energy 15 kW")
            document.save(source)
            results.append(TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options))
        assert len(requests) == 2  # One draft + complete bilingual review, then exact memory reuse.
        assert results[1].memory_segments == 6
        assert all("Енергія" in value for result in results for value in result.translations.values())
        assert any(isinstance(segment["neighboring_context"], dict)
                   for payload in requests for segment in payload["segments"])
    finally:
        provider.close()
