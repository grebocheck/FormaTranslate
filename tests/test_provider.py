from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from forma.config import Settings
from forma.models import Cancelled, FormaError
from forma.provider import TRANSLATION_SCHEMA, JobControl, Provider, retry_delay


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
    monkeypatch.setattr("forma.provider.time.time", lambda: 1445412420)
    assert retry_delay(httpx.Headers(headers), 2) == expected


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
