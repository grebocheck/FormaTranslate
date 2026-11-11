from __future__ import annotations

import copy

import pytest

from forma.providers.protocol import (
    ANTHROPIC_VERSION,
    adapt_chat_request,
    api_base,
    native_retry_delay,
    normalize_response,
    request_headers,
    terminal_quota_error,
)
from forma.settings.config import Settings


def config(mode):
    return Settings(
        api_mode=mode,
        base_url="https://provider.invalid/v1",
        model="test-model",
        api_key="fixture-secret",
    )


def response(mode, *, text='{"ok":true}', finish=None):
    if mode == "gemini":
        return {
            "candidates": [
                {
                    "content": {"parts": [{"thought": True, "text": "not output"}, {"text": text}]},
                    "finishReason": finish or "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "cachedContentTokenCount": 3,
                "candidatesTokenCount": 7,
                "thoughtsTokenCount": 2,
            },
            "modelVersion": "actual-model",
            "responseId": "test-response",
        }
    return {
        "content": [{"type": "thinking", "thinking": "not output"}, {"type": "text", "text": text}],
        "stop_reason": finish or "end_turn",
        "model": "actual-model",
        "id": "test-response",
        "usage": {
            "input_tokens": 15,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
            "output_tokens": 9,
            "output_tokens_details": {"thinking_tokens": 2},
        },
    }


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_request_maps_messages_images_schema_and_token_limit_without_mutation(mode):
    schema = {
        "type": "object",
        "properties": {"value": {"type": "number", "minimum": 0}},
        "required": ["value"],
        "additionalProperties": False,
    }
    body = {
        "model": "override-model",
        "messages": [
            {"role": "system", "content": "Translate carefully"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Input"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
                ],
            },
        ],
        "response_format": {"type": "json_schema", "json_schema": {"schema": schema}},
        "max_completion_tokens": 1200,
        "temperature": 0,
        "top_p": 1,
        "top_k": 1,
        "reasoning_effort": "high",
        "store": False,
        "stream": False,
    }
    original = copy.deepcopy(body)
    url, result = adapt_chat_request(config(mode), body)
    assert body == original
    assert "fixture-secret" not in url and "?" not in url
    assert (
        not {"temperature", "top_p", "top_k", "reasoning_effort", "store", "stream"} & result.keys()
    )
    if mode == "gemini":
        assert url == "https://provider.invalid/v1/models/override-model:generateContent"
        assert result["systemInstruction"] == {"parts": [{"text": "Translate carefully"}]}
        assert result["contents"][0]["parts"][1] == {
            "inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}
        }
        assert result["generationConfig"] == {
            "maxOutputTokens": 1200,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        }
    else:
        assert url == "https://provider.invalid/v1/messages"
        assert result["system"] == "Translate carefully" and result["max_tokens"] == 1200
        assert result["messages"][0]["content"][1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="},
        }
        value = result["output_config"]["format"]["schema"]["properties"]["value"]
        assert "minimum" not in value and "0" in value["description"]


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_authentication_and_required_output_default(mode):
    settings = config(mode)
    headers = request_headers(settings)
    if mode == "gemini":
        assert headers == {"x-goog-api-key": "fixture-secret"}
    else:
        assert headers == {
            "Authorization": "Bearer fixture-secret",
            "anthropic-version": ANTHROPIC_VERSION,
        }
    _, body = adapt_chat_request(settings, {"messages": [{"role": "user", "content": "hello"}]})
    assert not body.get("thinking")
    if mode == "anthropic":
        assert body["max_tokens"] == 16384
    assert "fixture-secret" not in repr(body)


@pytest.mark.parametrize(
    "mode,base,expected",
    [
        ("gemini", "https://provider.invalid", "https://provider.invalid/v1beta"),
        (
            "gemini",
            "https://provider.invalid/v1beta/models/model:generateContent",
            "https://provider.invalid/v1beta",
        ),
        ("anthropic", "https://provider.invalid/v1/messages/", "https://provider.invalid/v1"),
        ("anthropic", "https://provider.invalid", "https://provider.invalid/v1"),
        ("chat", "https://provider.invalid/v1/chat/completions", "https://provider.invalid/v1"),
        ("chat", "https://provider.invalid/v1/messages", "https://provider.invalid/v1/messages"),
    ],
)
def test_api_base_understands_native_and_existing_endpoint_forms(mode, base, expected):
    assert api_base(config(mode).with_overrides(base_url=base)) == expected


def test_gemini_model_name_cannot_change_origin_or_inject_query():
    url, _ = adapt_chat_request(
        config("gemini"),
        {
            "model": "models/odd/model?key=secret",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert url == "https://provider.invalid/v1/models/odd%2Fmodel%3Fkey%3Dsecret:generateContent"


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": [{"role": "tool", "content": "unsafe"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://private.invalid/image.png"},
                        }
                    ],
                }
            ]
        },
        {"messages": [{"role": "user", "content": "hello"}], "stream": True},
        {"messages": [{"role": "user", "content": "hello"}], "max_tokens": False},
    ],
)
def test_adapter_rejects_unsupported_input_without_sending_a_request(body):
    # Native request validation is shared before provider-specific serialization.
    # The valid request mapping test above exercises both serialization branches.
    with pytest.raises(ValueError):
        adapt_chat_request(config("gemini"), body)


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_response_hides_thoughts_and_preserves_billed_usage(mode):
    normalized = normalize_response(config(mode), response(mode))
    assert normalized["choices"] == [
        {
            "message": {"role": "assistant", "content": '{"ok":true}', "refusal": False},
            "finish_reason": "stop",
        }
    ]
    assert normalized["usage"]["prompt_tokens"] == 20
    assert normalized["usage"]["completion_tokens"] == 9
    assert normalized["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2
    assert normalized["usage"]["prompt_tokens_details"]["cached_tokens"] == 3
    assert normalized["model"] == "actual-model" and normalized["id"] == "test-response"


@pytest.mark.parametrize(
    "mode,finish,expected",
    [
        ("gemini", "MAX_TOKENS", "length"),
        ("gemini", "SAFETY", "content_filter"),
        ("gemini", "RECITATION", "content_filter"),
        ("anthropic", "max_tokens", "length"),
        ("anthropic", "refusal", "content_filter"),
        ("anthropic", "pause_turn", "pause_turn"),
        ("anthropic", "model_context_window_exceeded", "length"),
    ],
)
def test_native_terminal_statuses_preserve_usage_for_callers(mode, finish, expected):
    normalized = normalize_response(config(mode), response(mode, finish=finish))
    assert normalized["choices"][0]["finish_reason"] == expected
    assert normalized["choices"][0]["message"]["refusal"] == (expected == "content_filter")
    assert normalized["choices"][0].get("capacity_limit") == (
        "context" if finish == "model_context_window_exceeded" else "output" if expected == "length" else None
    )
    assert normalized["usage"]["completion_tokens"] == 9


def test_gemini_prompt_block_is_a_refusal_even_without_candidates():
    normalized = normalize_response(config("gemini"), {"promptFeedback": {"blockReason": "SAFETY"}})
    assert normalized["choices"][0]["message"]["refusal"]


def test_claude_explicit_refusal_details_are_not_accepted_as_translation():
    data = response("anthropic")
    data["stop_details"] = {"type": "refusal", "explanation": "fixture-sensitive-server-text"}
    normalized = normalize_response(config("anthropic"), data)
    assert normalized["choices"][0]["message"]["refusal"]
    assert "fixture-sensitive-server-text" not in repr(normalized)


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
@pytest.mark.parametrize(
    "corruption", ["empty", "missing_stop", "wrong_usage", "unknown_block", "negative_usage"]
)
def test_malformed_native_responses_fail_closed(mode, corruption):
    data = response(mode)
    if corruption == "empty":
        data = response(mode, text=" ")
    elif corruption == "missing_stop":
        if mode == "gemini":
            data["candidates"][0].pop("finishReason")
        else:
            data.pop("stop_reason")
    elif corruption == "wrong_usage":
        data["usageMetadata" if mode == "gemini" else "usage"] = [123]
    elif corruption == "unknown_block":
        if mode == "gemini":
            data["candidates"][0]["content"]["parts"].append({"functionCall": {"name": "tool"}})
        else:
            data["content"].append({"type": "tool_use", "name": "tool"})
    else:
        data["usageMetadata" if mode == "gemini" else "usage"][
            "promptTokenCount" if mode == "gemini" else "input_tokens"
        ] = -1
    with pytest.raises(ValueError):
        normalize_response(config(mode), data)


@pytest.mark.parametrize(
    "duration,expected", [("1.5s", 1.5), ("0s", 0), ("-1s", None), ("NaNs", None), (42, None)]
)
def test_retryinfo_parser_accepts_only_nonnegative_finite_duration(duration, expected):
    data = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": duration}
            ]
        }
    }
    assert native_retry_delay(data) == expected


@pytest.mark.parametrize(
    "violation,expected",
    [
        ({"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}, True),
        ({"quotaMetric": "generate_content_free_tier_requests", "quotaValue": "0"}, True),
        ({"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}, False),
    ],
)
def test_daily_or_unavailable_quota_can_stop_without_pointless_retries(violation, expected):
    data = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [violation]}
            ]
        }
    }
    assert terminal_quota_error(config("gemini"), data) is expected
    assert not terminal_quota_error(config("anthropic"), data)
