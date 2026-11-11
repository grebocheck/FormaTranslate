"""Small REST adapters shared by document, text, photo and PDF translation.

Only protocol conversion lives here. Request scheduling, HTTP lifetimes and
application validation remain with the caller; no SDK or hosted proxy is used.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
from urllib.parse import quote

from forma.settings.connection import connection_identity

NATIVE_MODES = frozenset({"gemini", "anthropic"})
ANTHROPIC_VERSION = "2026-10-21"
RATE_LIMIT_SCOPE_PREFIX = "forma-rate:"


def api_base(settings) -> str:
    return connection_identity(settings.base_url, settings.api_mode).root


def request_headers(settings) -> dict[str, str]:
    headers = {}
    if settings.api_key:
        if settings.api_mode == "gemini":
            headers["x-goog-api-key"] = settings.api_key
        else:
            headers["Authorization"] = f"Bearer {settings.api_key}"
    if settings.api_mode == "anthropic":
        headers["anthropic-version"] = ANTHROPIC_VERSION
    return headers


def rate_limit_key(settings) -> str:
    """Keep a capped account's pacing across jobs without storing its credential."""
    base = connection_identity(settings.base_url, settings.api_mode).key
    if not getattr(settings, "requests_per_minute", 0):
        return base
    digest = hashlib.sha256((base + "\0" + settings.api_key).encode()).hexdigest()
    return RATE_LIMIT_SCOPE_PREFIX + digest


def _parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        raise ValueError("Invalid message content")
    parts = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("Invalid content block")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append({"text": block["text"]})
        elif block.get("type") == "image_url":
            image = block.get("image_url")
            value = image.get("url") if isinstance(image, dict) else image
            if not isinstance(value, str):
                raise ValueError("Invalid embedded image")
            match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+=*)", value)
            if not match:
                raise ValueError("Only embedded raster images are supported")
            parts.append({"inlineData": {"mimeType": match[1], "data": match[2]}})
        else:
            raise ValueError("Unsupported content block")
    return parts


def _claude_schema(schema):
    """Move unsupported constraints into descriptions; callers validate originals."""
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    result = copy.deepcopy(schema)
    constraints = {}
    for key in (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    ):
        if key in result:
            constraints[key] = result.pop(key)
    if constraints:
        result["description"] = (
            str(result.get("description", "")) + " Constraints: " + repr(constraints)
        ).strip()
    for key in ("properties", "$defs", "definitions"):
        if isinstance(result.get(key), dict):
            result[key] = {name: _claude_schema(value) for name, value in result[key].items()}
    for key in ("items", "additionalProperties", "not"):
        if isinstance(result.get(key), dict):
            result[key] = _claude_schema(result[key])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(result.get(key), list):
            result[key] = [_claude_schema(value) for value in result[key]]
    if result.get("type") == "object":
        result["additionalProperties"] = False
    return result


def adapt_chat_request(settings, body: dict) -> tuple[str, dict]:
    """Convert a non-streaming chat request, never forwarding unknown options."""
    base = api_base(settings)
    if settings.api_mode not in NATIVE_MODES:
        return base + "/chat/completions", body
    if body.get("stream"):
        raise ValueError("Streaming is not supported by this adapter")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Missing messages")
    system, turns = [], []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Invalid message")
        role = message.get("role")
        parts = _parts(message.get("content"))
        if role in {"system", "developer"}:
            if any("text" not in part for part in parts):
                raise ValueError("System messages must contain text")
            system.extend(parts)
        elif role in {"user", "assistant"}:
            turns.append((role, parts))
        else:
            raise ValueError("Unsupported message role")
    if not turns:
        raise ValueError("Missing user messages")
    model = body.get("model") or settings.model
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Missing model")
    maximum = body.get("max_completion_tokens", body.get("max_tokens"))
    if maximum is not None and (
        isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0
    ):
        raise ValueError("Invalid output limit")
    fmt = body.get("response_format") or {}
    if not isinstance(fmt, dict):
        raise ValueError("Invalid output format")
    schema = (
        (fmt.get("json_schema") or {}).get("schema") if fmt.get("type") == "json_schema" else None
    )
    if settings.api_mode == "gemini":
        result = {
            "contents": [
                {"role": "model" if role == "assistant" else role, "parts": parts}
                for role, parts in turns
            ]
        }
        if system:
            result["systemInstruction"] = {"parts": system}
        generation = {}
        if maximum:
            generation["maxOutputTokens"] = maximum
        if fmt.get("type") in {"json_schema", "json_object"}:
            generation["responseMimeType"] = "application/json"
        if schema is not None:
            generation["responseJsonSchema"] = copy.deepcopy(schema)
        if generation:
            result["generationConfig"] = generation
        model = model.removeprefix("models/")
        return base + "/models/" + quote(model, safe="") + ":generateContent", result
    result = {"model": model, "max_tokens": maximum or 16384, "messages": []}
    if system:
        result["system"] = "\n\n".join(part["text"] for part in system)
    for role, parts in turns:
        blocks = []
        for part in parts:
            if "text" in part:
                blocks.append({"type": "text", "text": part["text"]})
            else:
                image = part["inlineData"]
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image["mimeType"],
                            "data": image["data"],
                        },
                    }
                )
        result["messages"].append({"role": role, "content": blocks})
    if schema is not None:
        result["output_config"] = {
            "format": {"type": "json_schema", "schema": _claude_schema(schema)}
        }
    return base + "/messages", result


def _count(usage: dict, key: str) -> int:
    value = usage.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Invalid token usage")
    return value


def normalize_response(settings, data: dict) -> dict:
    """Return a chat envelope, retaining stop/refusal flags and billed usage."""
    if not isinstance(data, dict):
        raise ValueError("Invalid response")
    if settings.api_mode not in NATIVE_MODES:
        return data
    texts, refusal = [], False
    if settings.api_mode == "gemini":
        usage = data.get("usageMetadata") or {}
        candidates = data.get("candidates") or []
        feedback = data.get("promptFeedback") or {}
        if not isinstance(candidates, list) or not isinstance(feedback, dict):
            raise ValueError("Invalid Gemini response")
        if feedback.get("blockReason") not in (None, "BLOCK_REASON_UNSPECIFIED"):
            reason, refusal = "content_filter", True
        elif len(candidates) == 1 and isinstance(candidates[0], dict):
            candidate = candidates[0]
            finish = candidate.get("finishReason")
            if finish == "STOP":
                reason = "stop"
            elif finish == "MAX_TOKENS":
                reason = "length"
            elif finish in {
                "SAFETY",
                "RECITATION",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
                "SPII",
                "IMAGE_SAFETY",
            }:
                reason, refusal = "content_filter", True
            else:
                raise ValueError("Incomplete Gemini response")
            content = candidate.get("content") or {}
            if not isinstance(content, dict) or not isinstance(content.get("parts", []), list):
                raise ValueError("Invalid Gemini content")
            for part in content.get("parts", []):
                if not isinstance(part, dict):
                    raise ValueError("Invalid Gemini part")
                if part.get("thought"):
                    continue
                if "text" in part:
                    if not isinstance(part["text"], str):
                        raise ValueError("Invalid Gemini text")
                    texts.append(part["text"])
                elif not refusal and reason == "stop":
                    raise ValueError("Unexpected Gemini output")
        else:
            raise ValueError("Missing Gemini candidate")
        if not isinstance(usage, dict):
            raise ValueError("Invalid usage")
        inputs = _count(usage, "promptTokenCount")
        cached = _count(usage, "cachedContentTokenCount")
        reasoning = _count(usage, "thoughtsTokenCount")
        outputs = _count(usage, "candidatesTokenCount") + reasoning
        model, identifier = data.get("modelVersion"), data.get("responseId")
    else:
        usage = data.get("usage") or {}
        blocks = data.get("content")
        if not isinstance(blocks, list) or not isinstance(usage, dict):
            raise ValueError("Invalid Claude response")
        finish = data.get("stop_reason")
        stop_details = data.get("stop_details") or {}
        if not isinstance(stop_details, dict):
            raise ValueError("Invalid Claude stop details")
        if finish == "refusal" or stop_details.get("type") == "refusal":
            reason, refusal = "content_filter", True
        elif finish in {"end_turn", "stop_sequence"}:
            reason = "stop"
        elif finish in {"max_tokens", "model_context_window_exceeded", "pause_turn"}:
            reason = "length"
        else:
            raise ValueError("Incomplete Claude response")
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("Invalid Claude block")
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") not in {"thinking", "redacted_thinking"}:
                raise ValueError("Unexpected Claude output")
        cached = _count(usage, "cache_read_input_tokens")
        inputs = (
            _count(usage, "input_tokens") + cached + _count(usage, "cache_creation_input_tokens")
        )
        output_details = usage.get("output_tokens_details") or {}
        if not isinstance(output_details, dict):
            raise ValueError("Invalid Claude output usage")
        # Thinking is a breakdown of output_tokens, not an extra billable total.
        outputs, reasoning = (
            _count(usage, "output_tokens"),
            _count(output_details, "thinking_tokens"),
        )
        model, identifier = data.get("model"), data.get("id")
    text = "".join(texts)
    if reason == "stop" and not text.strip():
        raise ValueError("Empty model response")
    return {
        "id": identifier if isinstance(identifier, str) else "",
        "model": model if isinstance(model, str) else settings.model,
        "choices": [
            {
                "message": {"role": "assistant", "content": text, "refusal": refusal},
                "finish_reason": reason,
            }
        ],
        "usage": {
            "prompt_tokens": inputs,
            "completion_tokens": outputs,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


def native_retry_delay(data) -> float | None:
    """Read Google's RetryInfo without exposing provider error messages."""
    error = data.get("error") if isinstance(data, dict) else None
    details = error.get("details", []) if isinstance(error, dict) else []
    if not isinstance(details, list):
        return None
    values = []
    for item in details:
        if (
            not isinstance(item, dict)
            or item.get("@type") != "type.googleapis.com/google.rpc.RetryInfo"
        ):
            continue
        value = item.get("retryDelay")
        if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
            seconds = float(value[:-1])
            if math.isfinite(seconds):
                values.append(seconds)
    return max(values) if values else None


def terminal_quota_error(settings, data) -> bool:
    """Daily/zero project quota cannot recover through a short retry loop."""
    if settings.api_mode != "gemini" or not isinstance(data, dict):
        return False
    error = data.get("error")
    details = error.get("details", []) if isinstance(error, dict) else []
    if not isinstance(details, list):
        return False
    for item in details:
        if (
            not isinstance(item, dict)
            or item.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure"
        ):
            continue
        violations = item.get("violations", [])
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            identifier = (
                str(violation.get("quotaId", "")) + " " + str(violation.get("quotaMetric", ""))
            )
            if re.search(r"per[_ -]?day|daily", identifier, re.I) or violation.get(
                "quotaValue"
            ) in (0, "0"):
                return True
    return False
