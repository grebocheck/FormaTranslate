from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from forma.core.models import Cancelled, FormaError
from forma.providers.client import (
    MAX_IMAGE_INPUTS,
    MAX_IMAGE_PAYLOAD_BYTES,
    TRANSLATION_SCHEMA,
    JobControl,
    Provider,
    retry_delay,
)
from forma.settings.config import Settings


def settings(**kwargs):
    return Settings(base_url="http://localhost:11434/v1", model="test-model", **kwargs)


def chat_response(data, **kwargs):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(data)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 14, "completion_tokens": 8},
            **kwargs,
        },
    )


def test_chat_payload_and_usage():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "temperature" not in body
        return chat_response({"segments": []})

    provider = Provider(settings(), transport=httpx.MockTransport(handler))
    try:
        assert provider.complete("Translate", {}, TRANSLATION_SCHEMA) == {"segments": []}
        assert (provider.input_tokens, provider.output_tokens) == (14, 8)
    finally:
        provider.close()


PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()


@pytest.mark.parametrize("mode", ["chat", "responses"])
def test_embedded_image_payloads_use_correct_api_schema(mode):
    payload = {"source_language": "uk", "target_language": "en", "hint": "Текст"}

    def handler(request):
        body = json.loads(request.content)
        messages = body["input" if mode == "responses" else "messages"]
        assert body["model"] == "test-model"
        assert body.get("max_output_tokens", body.get("max_completion_tokens")) == 1234
        assert messages[0]["role"] == "system"
        if mode == "responses":
            assert body["store"] is False
            assert messages[1]["content"] == [
                {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
                {"type": "input_image", "image_url": PNG_DATA_URL, "detail": "high"},
            ]
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [{"content": [{"type": "output_text", "text": '{"segments":[]}'}]}],
                },
            )
        assert messages[1]["content"] == [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
            {"type": "image_url", "image_url": {"url": PNG_DATA_URL, "detail": "high"}},
        ]
        return chat_response({"segments": []})

    provider = Provider(
        settings(api_mode=mode, max_output_tokens=1234), transport=httpx.MockTransport(handler)
    )
    try:
        assert provider.complete(
            "Translate", payload, TRANSLATION_SCHEMA, images=[PNG_DATA_URL]
        ) == {"segments": []}
    finally:
        provider.close()


@pytest.mark.parametrize("mode", ["chat", "responses"])
def test_no_images_retains_text_only_request_structure(mode):
    provider = Provider(settings(api_mode=mode))
    try:
        original = provider._body("Translate", {"text": "Hello"}, TRANSLATION_SCHEMA, "test-model")
        explicit_empty = provider._body(
            "Translate", {"text": "Hello"}, TRANSLATION_SCHEMA, "test-model", images=()
        )
        assert original == explicit_empty
        assert isinstance(
            original["input" if mode == "responses" else "messages"][1]["content"], str
        )
    finally:
        provider.close()


@pytest.mark.parametrize(
    "images",
    [
        ["https://example.org/private.png"],
        ["file:///private.png"],
        ["data:image/svg+xml;base64,PHN2Zz4="],
        ["data:image/png;base64,not_base64"],
        ["data:image/png;base64,cGxhaW4gdGV4dA=="],
        ["data:image/png;base64,"],
        [PNG_DATA_URL] * (MAX_IMAGE_INPUTS + 1),
        [None],
        PNG_DATA_URL,
    ],
)
def test_invalid_image_inputs_are_rejected_before_network(images):
    provider = Provider(
        settings(), transport=httpx.MockTransport(lambda _: pytest.fail("Network called"))
    )
    try:
        with pytest.raises(FormaError):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=images)
    finally:
        provider.close()


def test_image_payload_size_is_bounded_before_base64_decode():
    provider = Provider(
        settings(), transport=httpx.MockTransport(lambda _: pytest.fail("Network called"))
    )
    try:
        with pytest.raises(FormaError, match="завелике"):
            provider.complete(
                "Translate", {}, TRANSLATION_SCHEMA, images=["a" * (MAX_IMAGE_PAYLOAD_BYTES + 1)]
            )
    finally:
        provider.close()


@pytest.mark.parametrize(
    "mime,data", [("jpeg", b"\xff\xd8\xfffixture"), ("webp", b"RIFF\0\0\0\0WEBPfixture")]
)
def test_other_supported_raster_mime_types(mime, data):
    image = f"data:image/{mime};base64," + base64.b64encode(data).decode()
    provider = Provider(
        settings(), transport=httpx.MockTransport(lambda _: chat_response({"segments": []}))
    )
    try:
        assert provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=[image]) == {
            "segments": []
        }
    finally:
        provider.close()


@pytest.mark.parametrize("response_format", ["auto", "strict", "json", "plain"])
def test_unsupported_vision_reports_actionable_hint_without_server_data(response_format):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "This model does not support image inputs secret-sensitive-key "
                    + PNG_DATA_URL
                }
            },
        )

    provider = Provider(
        settings(response_format=response_format), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(FormaError, match="підтримкою зображень") as error:
            provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=[PNG_DATA_URL])
        assert len(requests) == 1
        assert "secret-sensitive-key" not in str(error.value)
        assert "data:image" not in str(error.value)
    finally:
        provider.close()


def test_images_survive_json_format_negotiation():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["messages"][1]["content"][1]["image_url"]["url"] == PNG_DATA_URL
        if len(requests) == 1:
            return httpx.Response(
                400, json={"error": "response_format json_schema unsupported with images"}
            )
        return chat_response({"segments": []})

    provider = Provider(settings(), transport=httpx.MockTransport(handler))
    try:
        provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=[PNG_DATA_URL])
        assert len(requests) == 2
        assert requests[-1]["response_format"]["type"] == "json_object"
    finally:
        provider.close()


def test_image_request_cancellation_discards_result_but_accounts_usage():
    control = JobControl()

    def handler(_request):
        control.cancelled.set()
        return chat_response({"segments": []})

    provider = Provider(settings(), control, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Cancelled):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=[PNG_DATA_URL])
        assert provider.input_tokens == 14
    finally:
        provider.close()


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": []},
        {"choices": [{"message": {"content": []}}]},
        {"usage": [2]},
        {"choices": [{"message": {"content": 12}}]},
        pytest.param(b"[" * 20000 + b"0" + b"]" * 20000, id="nested-response"),
        pytest.param(
            {"choices": [{"message": {"content": "[" * 20000 + "0" + "]" * 20000}}]},
            id="nested-model-content",
        ),
    ],
)
def test_malformed_image_response_is_a_safe_error(body):
    def response(_):
        return httpx.Response(200, **({"content": body} if isinstance(body, bytes) else {"json": body}))

    provider = Provider(settings(), transport=httpx.MockTransport(response))
    try:
        with pytest.raises(FormaError, match="некоректний JSON"):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=[PNG_DATA_URL])
    finally:
        provider.close()


def test_explicit_unsupported_format_falls_back():
    modes = []

    def handler(request):
        body = json.loads(request.content)
        mode = body.get("response_format", {}).get("type", "plain")
        modes.append(mode)
        if mode != "plain":
            return httpx.Response(400, json={"error": "response_format unsupported"})
        assert "JSON schema:" in body["messages"][0]["content"]
        return chat_response({"segments": []})

    provider = Provider(settings(), transport=httpx.MockTransport(handler))
    provider.complete("Translate", {}, TRANSLATION_SCHEMA)
    assert modes == ["json_schema", "json_object", "plain"]
    provider.close()


def test_auth_error_never_exposes_server_echoed_key():
    def handler(_):
        return httpx.Response(401, json={"error": "invalid secret-sensitive-key"})

    provider = Provider(
        settings(api_key="secret-sensitive-key"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(FormaError) as error:
        provider.complete("Translate", {}, TRANSLATION_SCHEMA)
    assert "secret-sensitive-key" not in str(error.value)
    assert "HTTP 401" in str(error.value)
    provider.close()


def test_responses_api_body_and_refusal():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url).endswith("/responses")
        assert body["text"]["format"]["schema"] == TRANSLATION_SCHEMA
        assert body["store"] is False
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": '{"segments":[]}'}]}],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    provider = Provider(settings(api_mode="responses"), transport=httpx.MockTransport(handler))
    assert provider.complete("Translate", {}, TRANSLATION_SCHEMA) == {"segments": []}
    assert provider.input_tokens == 12
    provider.close()


def test_truncated_output_is_not_accepted():
    response = {"choices": [{"message": {"content": '{"segments":[]}'}, "finish_reason": "length"}]}
    provider = Provider(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    with pytest.raises(FormaError, match="обірвалася"):
        provider.complete("Translate", {}, TRANSLATION_SCHEMA)
    provider.close()


@pytest.mark.parametrize("retain_completed", [False, True])
def test_cancelled_request_never_starts(retain_completed):
    control = JobControl()
    control.cancelled.set()
    provider = Provider(
        settings(), control, transport=httpx.MockTransport(lambda _: pytest.fail("Network called"))
    )
    with pytest.raises(Cancelled):
        provider.complete(
            "Translate", {}, TRANSLATION_SCHEMA, retain_completed_on_cancel=retain_completed
        )
    provider.close()


def test_shared_budget_limits_multiple_providers_and_accounts_every_parallel_response():
    control = JobControl(3)
    lock = threading.Lock()
    first_requests = threading.Barrier(3)
    active = peak = started = 0

    def handler(_):
        nonlocal active, peak, started
        with lock:
            active += 1
            peak = max(peak, active)
            started += 1
            meet = started <= 3
        try:
            if meet:
                first_requests.wait(timeout=3)
            time.sleep(0.003)
            return chat_response({"segments": []})
        finally:
            with lock:
                active -= 1

    providers = [
        Provider(settings(), control, transport=httpx.MockTransport(handler)) for _ in range(2)
    ]
    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [
                executor.submit(providers[i % 2].complete, "Translate", {}, TRANSLATION_SCHEMA)
                for i in range(24)
            ]
            assert all(f.result(timeout=3) == {"segments": []} for f in futures)
        assert peak == 3 and active == 0
        for provider in providers:
            assert provider.input_tokens == 14 * 12
            assert provider.output_tokens == 8 * 12
            assert len(provider.usage_records) == 12
    finally:
        for provider in providers:
            provider.close()


def test_parallel_format_fallback_does_not_skip_supported_json_mode():
    lock = threading.Lock()
    strict_requests = threading.Barrier(2)
    json_seen = threading.Event()
    modes = []

    def handler(request):
        mode = json.loads(request.content).get("response_format", {}).get("type", "plain")
        with lock:
            modes.append(mode)
            ordinal = modes.count("json_schema")
        if mode == "json_schema":
            strict_requests.wait(timeout=3)
            if ordinal == 2:
                assert json_seen.wait(3)
            return httpx.Response(400, json={"error": "response_format unsupported"})
        assert mode == "json_object"
        json_seen.set()
        return chat_response({"segments": []})

    provider = Provider(settings(), transport=httpx.MockTransport(handler))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(provider.complete, "Translate", {}, TRANSLATION_SCHEMA)
                for _ in range(2)
            ]
            assert all(f.result(timeout=3) == {"segments": []} for f in futures)
        assert modes.count("json_schema") == 2 and modes.count("json_object") == 2
        assert provider.format == "json"
    finally:
        provider.close()


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "120"}, 120),  # Do not retry earlier than the server asks.
        ({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, 60),
        ({"retry-after-ms": "250"}, 0.25),
        ({"Retry-After": "invalid"}, 4),
        ({"Retry-After": "nan"}, 4),
    ],
)
def test_retry_after_supports_seconds_dates_and_compatible_milliseconds(
    monkeypatch, headers, expected
):
    monkeypatch.setattr("forma.providers.client.time.time", lambda: 1445412420)
    assert retry_delay(httpx.Headers(headers), 2) == expected


def test_invalid_retry_header_still_respects_native_retryinfo():
    assert retry_delay(httpx.Headers({"retry-after": "invalid"}), 0, fallback=30) == 30
    assert retry_delay(httpx.Headers({"retry-after": "40"}), 0, fallback=30) == 40


def test_rate_limit_cooldown_is_shared_with_another_provider():
    class ObservedControl(JobControl):
        def __init__(self):
            super().__init__(2)
            self.limited = threading.Event()
            self.cooldown_started = 0

        def defer_requests(self, key, seconds):
            super().defer_requests(key, seconds)
            self.cooldown_started = time.monotonic()
            self.limited.set()

    control = ObservedControl()
    limited_calls = 0
    following_requests = []

    def limited_handler(_):
        nonlocal limited_calls
        limited_calls += 1
        if limited_calls == 1:
            return httpx.Response(429, headers={"retry-after-ms": "200"})
        following_requests.append(time.monotonic())
        return chat_response({"segments": []})

    def second_handler(_):
        following_requests.append(time.monotonic())
        return chat_response({"segments": []})

    first = Provider(settings(), control, transport=httpx.MockTransport(limited_handler))
    second = Provider(settings(), control, transport=httpx.MockTransport(second_handler))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(first.complete, "Translate", {}, TRANSLATION_SCHEMA)
            assert control.limited.wait(3)
            b = executor.submit(second.complete, "Translate", {}, TRANSLATION_SCHEMA)
            assert a.result(timeout=3) == b.result(timeout=3) == {"segments": []}
        assert len(following_requests) == 2
        assert all(moment - control.cooldown_started >= 0.18 for moment in following_requests)
    finally:
        first.close()
        second.close()


def test_cancellation_interrupts_long_cooldown_without_leaking_capacity():
    class ObservedControl(JobControl):
        def __init__(self):
            super().__init__(1)
            self.limited = threading.Event()

        def defer_requests(self, key, seconds):
            super().defer_requests(key, seconds)
            self.limited.set()

    control = ObservedControl()
    calls = []

    def handler(_):
        calls.append(True)
        return httpx.Response(429, headers={"Retry-After": "120"})

    provider = Provider(settings(), control, transport=httpx.MockTransport(handler))
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(provider.complete, "Translate", {}, TRANSLATION_SCHEMA)
            assert control.limited.wait(3)
            control.cancelled.set()
            with pytest.raises(Cancelled):
                future.result(timeout=1)
        assert len(calls) == 1
        control.cancelled.clear()
        with control.request_slots():
            pass
    finally:
        provider.close()


def test_pause_and_cancel_prevent_waiting_weighted_requests_from_starting():
    control = JobControl(2)
    entered = threading.Event()

    def waiter():
        with control.request_slots(2):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with control.request_slots():
            future = executor.submit(waiter)
            assert not entered.wait(0.05)
            control.paused.set()
        assert not entered.wait(0.05)
        control.cancelled.set()
        with pytest.raises(Cancelled):
            future.result(timeout=1)
    assert not entered.is_set()
    control.paused.clear()
    control.cancelled.clear()
    with control.request_slots(2):
        pass


def test_completed_request_usage_is_recorded_when_cancellation_arrives_in_flight():
    control = JobControl(1)

    def handler(_):
        control.cancelled.set()
        return chat_response({"segments": []})

    provider = Provider(settings(), control, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Cancelled):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA)
        assert (provider.input_tokens, provider.output_tokens) == (14, 8)
        assert len(provider.usage_records) == 1
    finally:
        provider.close()


def test_completed_response_can_be_retained_only_with_explicit_opt_in():
    control = JobControl(1)

    def handler(_):
        control.cancelled.set()
        return chat_response({"segments": []})

    provider = Provider(settings(), control, transport=httpx.MockTransport(handler))
    try:
        assert provider.complete(
            "Translate", {}, TRANSLATION_SCHEMA, retain_completed_on_cancel=True
        ) == {"segments": []}
        assert control.cancelled.is_set()
        assert (provider.input_tokens, provider.output_tokens) == (14, 8)
        with pytest.raises(Cancelled):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA, retain_completed_on_cancel=True)
    finally:
        provider.close()


def native_response(mode, *, finish=None):
    if mode == "gemini":
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"segments":[]}'}, {"text": "hidden", "thought": True}]
                    },
                    "finishReason": finish or "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 14,
                "candidatesTokenCount": 6,
                "thoughtsTokenCount": 2,
            },
        }
    return {
        "content": [{"type": "text", "text": '{"segments":[]}'}],
        "stop_reason": finish or "end_turn",
        "usage": {"input_tokens": 14, "output_tokens": 8},
    }


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_provider_uses_images_editor_override_and_native_auth(mode):
    def handler(request):
        body = json.loads(request.content)
        assert "fixture-key" not in str(request.url) and not request.url.query
        if mode == "gemini":
            assert request.headers["x-goog-api-key"] == "fixture-key"
            assert "authorization" not in request.headers
            assert request.url.path.endswith("/models/editor:generateContent")
            assert (
                body["contents"][0]["parts"][1]["inlineData"]["data"] == PNG_DATA_URL.split(",")[1]
            )
        else:
            assert request.headers["authorization"] == "Bearer fixture-key"
            assert request.headers["anthropic-version"] == "2026-10-21"
            assert request.url.path.endswith("/messages") and body["model"] == "editor"
            assert body["max_tokens"] == 1234
            assert body["messages"][0]["content"][1]["source"]["data"] == PNG_DATA_URL.split(",")[1]
        return httpx.Response(200, json=native_response(mode))

    provider = Provider(
        settings(api_mode=mode, api_key="fixture-key", max_output_tokens=1234),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert provider.complete(
            "Translate", {}, TRANSLATION_SCHEMA, model="editor", images=[PNG_DATA_URL]
        ) == {"segments": []}
        assert (provider.input_tokens, provider.output_tokens) == (14, 8)
    finally:
        provider.close()


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_json_schema_fallback_retains_image_and_schema_instruction(mode):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            key = "responseJsonSchema" if mode == "gemini" else "output_config.format"
            return httpx.Response(400, json={"error": {"message": key + " unsupported"}})
        if mode == "gemini":
            assert "responseJsonSchema" not in body["generationConfig"]
            assert "JSON schema:" in body["systemInstruction"]["parts"][0]["text"]
            assert len(body["contents"][0]["parts"]) == 2
        else:
            assert "output_config" not in body
            assert "JSON schema:" in body["system"]
            assert len(body["messages"][0]["content"]) == 2
        return httpx.Response(200, json=native_response(mode))

    provider = Provider(settings(api_mode=mode), transport=httpx.MockTransport(handler))
    try:
        assert provider.complete("Translate", {}, TRANSLATION_SCHEMA, images=[PNG_DATA_URL]) == {
            "segments": []
        }
        assert len(requests) == 2
    finally:
        provider.close()


@pytest.mark.parametrize(
    "mode,finish,message",
    [
        ("gemini", "SAFETY", "відмовилась"),
        ("anthropic", "max_tokens", "обірвалася"),
    ],
)
def test_native_incomplete_response_is_not_accepted_even_when_json_is_valid(mode, finish, message):
    provider = Provider(
        settings(api_mode=mode),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=native_response(mode, finish=finish))
        ),
    )
    try:
        with pytest.raises(FormaError, match=message):
            provider.complete("Translate", {}, TRANSLATION_SCHEMA)
        assert provider.output_tokens == 8
    finally:
        provider.close()


def test_gemini_daily_quota_is_not_retried_and_does_not_echo_server_details():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            429,
            json={
                "error": {
                    "message": "fixture-secret",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                            ],
                        },
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "60s"},
                    ],
                }
            },
        )

    provider = Provider(settings(api_mode="gemini"), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FormaError, match="денну квоту") as exc:
            provider.complete("Translate", {}, TRANSLATION_SCHEMA)
        assert len(requests) == 1 and "fixture-secret" not in str(exc.value)
    finally:
        provider.close()


def test_gemini_retryinfo_is_shared_and_cancellable():
    class ObservedControl(JobControl):
        def __init__(self):
            super().__init__()
            self.deferred = threading.Event()
            self.seconds = 0

        def defer_requests(self, key, seconds):
            super().defer_requests(key, seconds)
            self.seconds = seconds
            self.deferred.set()

    control = ObservedControl()
    provider = Provider(
        settings(api_mode="gemini"),
        control,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                429,
                json={
                    "error": {
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "60.5s",
                            }
                        ]
                    }
                },
            )
        ),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(provider.complete, "Translate", {}, TRANSLATION_SCHEMA)
            assert control.deferred.wait(1) and control.seconds == 60.5
            control.cancelled.set()
            with pytest.raises(Cancelled):
                future.result(timeout=1)
    finally:
        provider.close()


def test_rpm_gate_does_not_reserve_waiting_calls_and_is_shared_with_pool(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("forma.providers.client.time.monotonic", lambda: now[0])
    control = JobControl(2)
    assert control.request_delay("provider", 5) == 0
    assert control.request_delay("provider", 5) == 12
    assert control.request_delay("other", 5) == 0
    now[0] += 12
    with control.request_slots(cooldown_key="provider", requests_per_minute=5):
        assert control.request_delay("provider", 5) == 12
    now[0] += 12
    control.defer_requests("provider", 30)
    assert control.request_delay("provider", 5) == 30
    now[0] += 30
    assert control.request_delay("provider", 5) == 0


def test_rpm_wait_can_be_cancelled_without_network_or_capacity_leak():
    control = JobControl(1)
    provider = Provider(
        settings(requests_per_minute=1),
        control,
        transport=httpx.MockTransport(lambda _: pytest.fail("Network called")),
    )
    assert control.request_delay(provider.rate_limit_key, 1) == 0
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(provider.complete, "Translate", {}, TRANSLATION_SCHEMA)
            time.sleep(0.02)
            control.cancelled.set()
            with pytest.raises(Cancelled):
                future.result(timeout=1)
        assert control._available == 1 and not control._waiters
    finally:
        provider.close()


def test_capped_account_pacing_and_cooldown_survive_new_jobs_without_sharing_cancel(monkeypatch):
    from forma.providers.protocol import rate_limit_key

    monkeypatch.setattr("forma.providers.client._PACING_DEADLINES", {})
    now = [10.0]
    monkeypatch.setattr("forma.providers.client.time.monotonic", lambda: now[0])
    connection = settings(api_key="scoped-fixture-key", requests_per_minute=5)
    key = rate_limit_key(connection)
    assert "scoped-fixture-key" not in key
    first, second = JobControl(), JobControl()
    assert first.request_delay(key, 5) == 0
    assert second.request_delay(key, 5) == 12
    assert (
        second.request_delay(rate_limit_key(connection.with_overrides(api_key="another-key")), 5)
        == 0
    )
    first.cancelled.set()
    assert not second.cancelled.is_set()
    now[0] += 12
    assert second.request_delay(key, 5) == 0
    second.defer_requests(key, 60)
    assert JobControl().request_delay(key, 5) == 60


def test_shared_pacing_cache_has_a_bounded_account_count(monkeypatch):
    from forma.providers import client as module
    from forma.providers.protocol import RATE_LIMIT_SCOPE_PREFIX

    monkeypatch.setattr(module, "_PACING_DEADLINES", {})
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    control = JobControl()
    for index in range(600):
        control.request_delay(RATE_LIMIT_SCOPE_PREFIX + str(index), 5)
    assert len(module._PACING_DEADLINES) == module._MAX_PACING_ACCOUNTS
