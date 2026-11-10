from __future__ import annotations

import json
import socket
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from forma.config import Settings
from forma.models import FormaError, Options
from forma.pdf import translate_pdf
from forma.pdf_api import MAX_BODY, PDFAPI, completion_body
from forma.provider import JobControl, Provider


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.request_times.append(time.monotonic())
            self.server.requests.append((self.path, dict(self.headers), body))
            self.server.started.set()
            if self.server.block:
                self.server.release.wait(timeout=5)
            time.sleep(self.server.delay)
            data = json.dumps(self.server.response).encode()
            try:
                self.send_response(self.server.status)
                for key, value in self.server.response_headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests, server.request_times, server.response_headers = [], [], {}
    server.response = {"choices": [], "usage": {"prompt_tokens": 31, "completion_tokens": 12}}
    server.status, server.delay, server.block = 200, 0, False
    server.started, server.release = threading.Event(), threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)


def config(server, **kwargs):
    return Settings(
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        model="gpt-6-test",
        api_key="upstream-test-secret",
        **kwargs,
    )


def post(bridge, body=None):
    return httpx.post(
        bridge.base_url + "/chat/completions",
        headers={"Authorization": f"Bearer {bridge.token}"},
        json=body or {"model": bridge.cache_model, "messages": [], "max_tokens": 2048},
        timeout=5,
    )


def test_pdf_api_forwards_explicit_settings_and_accounts_usage(upstream):
    with PDFAPI(config(upstream, reasoning_effort="low", max_output_tokens=4096), 2) as bridge:
        response = post(bridge)
        assert response.status_code == 200
        path, headers, body = upstream.requests[0]
        assert path == "/v1/chat/completions"
        assert headers["Authorization"] == "Bearer upstream-test-secret"
        assert body["model"] == "gpt-6-test"
        assert body["reasoning_effort"] == "low"
        assert body["max_completion_tokens"] == 4096
        assert "max_tokens" not in body and "reasoning" not in body
        assert bridge.summary()["input_tokens"] == 31
        assert bridge.summary()["output_tokens"] == 12
        assert bridge.summary()["requests"] == 1
    assert not bridge.thread.is_alive()


def test_pdf_body_preserves_unconfigured_values_and_unknown_model_compatibility():
    original = {
        "messages": [{"role": "user", "content": "Keep all layout markers"}],
        "max_tokens": 2048,
    }
    known = completion_body(original, Settings(model="gpt-6-test"))
    assert known["max_completion_tokens"] == 2048
    assert "reasoning_effort" not in known
    custom = completion_body(original, Settings(model="custom-llm", reasoning_effort="low"))
    assert custom["max_tokens"] == 2048 and "reasoning_effort" not in custom
    assert custom["messages"] == original["messages"]
    assert "max_tokens" in original  # Do not mutate BabelDOC's parsed request.


def test_pdf_cache_identity_is_stable_and_excludes_local_port_and_token(upstream):
    settings = config(upstream, reasoning_effort="low")
    with PDFAPI(settings, 1) as first, PDFAPI(settings, 1) as second:
        assert first.token != second.token and first.base_url != second.base_url
        assert first.cache_model == second.cache_model
    for overrides in (
        {"reasoning_effort": "none"},
        {"max_output_tokens": 8192},
        {"base_url": "http://localhost:12345/v1"},
        {"model": "gpt-6-other"},
        {"api_mode": "gemini"},
        {"api_mode": "anthropic"},
    ):
        candidate = PDFAPI(settings.with_overrides(**overrides), 1)
        try:
            assert candidate.cache_model != first.cache_model
        finally:
            candidate.loop.close()
    alias = PDFAPI(settings.with_overrides(base_url=settings.base_url + "/chat/completions/"), 1)
    try:
        assert alias.cache_model == first.cache_model
    finally:
        alias.loop.close()


def test_chat_pdf_cache_identity_remains_compatible_with_previous_release():
    settings = Settings(base_url="https://example.invalid/v1", model="model-v1")
    bridge = PDFAPI(settings, 1)
    try:
        assert bridge.cache_model == "model-v1-forma-b30bb60d7dbbe589"
    finally:
        bridge.loop.close()


def test_pdf_api_rejects_unauthorized_paths_and_oversize_before_upstream(upstream):
    with PDFAPI(config(upstream), 1) as bridge:
        assert httpx.post(bridge.base_url + "/chat/completions", json={}).status_code == 401
        headers = {"Authorization": f"Bearer {bridge.token}"}
        assert httpx.post(bridge.base_url + "/other", headers=headers, json={}).status_code == 404
        assert (
            httpx.post(
                bridge.base_url + "/chat/completions", headers=headers, content=b"{"
            ).status_code
            == 400
        )
        with socket.create_connection(("127.0.0.1", bridge.server.server_port)) as connection:
            connection.sendall(
                (
                    "POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                    f"Authorization: Bearer {bridge.token}\r\nContent-Length: {MAX_BODY + 1}\r\n\r\n"
                ).encode()
            )
            assert b"413" in connection.recv(1024)
        assert not upstream.requests
        with socket.create_connection(("127.0.0.1", bridge.server.server_port)) as connection:
            connection.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                b"Authorization: Bearer \xff\r\nContent-Length: 2\r\n\r\n{}"
            )
            assert b"401" in connection.recv(1024)


@pytest.mark.parametrize("status", [400, 401, 429, 302])
def test_pdf_api_preserves_status_retry_after_and_redacts_echoed_key(upstream, status):
    upstream.status = status
    upstream.response = {"error": {"message": "Echo upstream-test-secret"}}
    upstream.response_headers = {"Retry-After": "2", "Location": "/untrusted-redirect"}
    with PDFAPI(config(upstream), 1) as bridge:
        response = post(bridge)
        assert response.status_code == status
        assert response.headers["Retry-After"] == "2"
        assert "upstream-test-secret" not in response.text
        assert len(upstream.requests) == 1
        if status in (400, 401):
            with pytest.raises(FormaError, match="параметри" if status == 400 else "ключ"):
                bridge.check_error()
        else:
            bridge.check_error()


def test_pdf_api_enforces_concurrency_even_when_engine_sends_extra_requests(upstream):
    upstream.delay = 0.15
    with PDFAPI(config(upstream), 2) as bridge, ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(post, bridge) for _ in range(6)]
        assert all(future.result().status_code == 200 for future in futures)
        assert bridge.summary()["peak_concurrent_requests"] == 2
        assert bridge.summary()["requests"] == 6


def test_pdf_api_cancels_slow_upstream_without_waiting_for_request_timeout(upstream):
    upstream.block = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        with PDFAPI(config(upstream, timeout=180), 1) as bridge:
            future = pool.submit(post, bridge)
            assert upstream.started.wait(timeout=3)
            start = time.monotonic()
        assert time.monotonic() - start < 2
        assert not bridge.thread.is_alive()
        with pytest.raises(httpx.HTTPError):
            future.result(timeout=2)
    upstream.release.set()


def test_pdf_api_respects_configured_timeout(upstream):
    upstream.block = True
    with PDFAPI(config(upstream, timeout=0.1), 1) as bridge:
        response = post(bridge)
        assert response.status_code == 504
        assert bridge.summary()["statuses"] == {"504": 1}
    upstream.release.set()


def test_pdf_api_bounds_oversized_upstream_response(upstream, monkeypatch):
    monkeypatch.setattr("forma.pdf_api.MAX_RESPONSE", 512)
    upstream.response = {"message": "x" * 1024}
    with PDFAPI(config(upstream), 1) as bridge:
        response = post(bridge)
        assert response.status_code == 502
        assert len(response.content) < 512


def test_pdf_api_redacts_json_escaped_credentials_and_preserves_retry_after_ms(upstream):
    secret = 'fake-key-with-"quote'
    upstream.status = 429
    upstream.response = {"error": {"message": "Echo " + secret}}
    upstream.response_headers = {"Retry-After-Ms": "2000"}
    with PDFAPI(config(upstream).with_overrides(api_key=secret), 1) as bridge:
        response = post(bridge)
        assert secret not in response.json()["error"]["message"]
        assert "[REDACTED]" in response.json()["error"]["message"]
        assert response.headers["Retry-After-Ms"] == "2000"


@pytest.mark.parametrize("suffix", ["/chat/completions", "/responses/"])
def test_pdf_api_normalizes_endpoint_suffix(upstream, suffix):
    settings = config(upstream)
    with PDFAPI(settings.with_overrides(base_url=settings.base_url + suffix), 1) as bridge:
        assert post(bridge).status_code == 200
        assert upstream.requests[0][0] == "/v1/chat/completions"


@pytest.mark.parametrize(
    "choice,expected",
    [
        (
            {"finish_reason": "length", "message": {"content": "Nonempty truncated paragraph"}},
            "обірвалася",
        ),
        ({"finish_reason": "stop", "message": {"content": None, "refusal": "Denied"}}, "відмовив"),
        ({"finish_reason": "content_filter", "message": {"content": None}}, "відмовив"),
        ({"finish_reason": "stop", "message": {"content": "   "}}, "порожній"),
    ],
)
def test_pdf_api_blocks_incomplete_responses_and_still_accounts_usage(upstream, choice, expected):
    upstream.response["choices"] = [choice]
    with PDFAPI(config(upstream), 1) as bridge:
        response = post(bridge)
        assert response.status_code == 422
        assert bridge.summary()["input_tokens"] == 31
        assert bridge.summary()["output_tokens"] == 12
        with pytest.raises(FormaError, match=expected):
            bridge.check_error()


@pytest.mark.parametrize("refused", [False, True])
def test_incomplete_pdf_response_cannot_export_even_if_engine_writes_file(
    upstream, pdf_file, tmp_path, monkeypatch, refused
):
    upstream.response["choices"] = [
        {
            "finish_reason": "stop" if refused else "length",
            "message": {"content": "Partial paragraph", "refusal": "Denied" if refused else None},
        }
    ]
    monkeypatch.setattr("forma.pdf.find_babeldoc", lambda _: "babeldoc-test")

    class Process:
        returncode = 0

        def __init__(self, arguments, **kwargs):
            config_path = Path(arguments[arguments.index("--config") + 1])
            config = tomllib.loads(config_path.read_text())["babeldoc"]
            response = httpx.post(
                config["openai-base-url"] + "/chat/completions",
                headers={"Authorization": "Bearer " + config["openai-api-key"]},
                json={"model": config["openai-model"], "messages": []},
            )
            assert response.status_code == 422
            (Path(config["output"]) / "partial.mono.pdf").write_bytes(pdf_file.read_bytes())

        def poll(self):
            return 0

    monkeypatch.setattr("forma.pdf.subprocess.Popen", Process)
    with pytest.raises(FormaError, match="відмовив" if refused else "обірвалася"):
        translate_pdf(
            pdf_file,
            tmp_path / "out",
            Options(source_language="en"),
            config(upstream),
            JobControl(),
            lambda *_: None,
        )
    assert not (tmp_path / "out").exists()


def native_config(server, mode, **overrides):
    return config(server, api_mode=mode).with_overrides(
        model="gemini-test" if mode == "gemini" else "claude-test", **overrides
    )


def native_response(mode, *, text="Переклад 123", finish=None):
    if mode == "gemini":
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": finish or "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 31, "candidatesTokenCount": 12},
            "modelVersion": "gemini-test",
        }
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}],
        "stop_reason": finish or "end_turn",
        "usage": {"input_tokens": 31, "output_tokens": 12},
    }


def native_post(bridge):
    return post(
        bridge,
        {
            "model": bridge.cache_model,
            "messages": [
                {"role": "system", "content": "Translate into Ukrainian. Preserve numbers."},
                {"role": "user", "content": "Translate 123"},
            ],
            "max_tokens": 2048,
            "temperature": 0,
        },
    )


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_pdf_bridge_maps_requests_and_accounts_usage(upstream, mode):
    upstream.response = native_response(mode)
    settings = native_config(upstream, mode, max_output_tokens=4096)
    with PDFAPI(settings, 2) as bridge:
        response = native_post(bridge)
        assert response.status_code == 200
        result = response.json()
        assert result["choices"][0]["message"]["content"] == "Переклад 123"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert bridge.summary()["input_tokens"] == 31
        assert bridge.summary()["output_tokens"] == 12
        path, raw_headers, body = upstream.requests[0]
        headers = {key.lower(): value for key, value in raw_headers.items()}
        assert "upstream-test-secret" not in path
        assert bridge.cache_model not in json.dumps(body)
        if mode == "gemini":
            assert "authorization" not in headers
            assert path == "/v1/models/gemini-test:generateContent"
            assert headers["x-goog-api-key"] == settings.api_key
            assert body["generationConfig"]["maxOutputTokens"] == 4096
            assert body["contents"][0]["parts"][0]["text"] == "Translate 123"
            assert body["systemInstruction"]["parts"][0]["text"].startswith("Translate")
        else:
            assert path == "/v1/messages"
            assert headers["authorization"] == "Bearer " + settings.api_key
            assert headers["anthropic-version"] == "2026-10-21"
            assert body["model"] == "claude-test"
            assert body["max_tokens"] == 4096
            assert body["system"].startswith("Translate")


@pytest.mark.parametrize(
    "mode,finish,expected",
    [
        ("gemini", "MAX_TOKENS", "обірвалася"),
        ("anthropic", "max_tokens", "обірвалася"),
        ("gemini", "SAFETY", "відмовив"),
        ("anthropic", "refusal", "відмовив"),
    ],
)
def test_native_pdf_bridge_rejects_partial_or_refused_text(upstream, mode, finish, expected):
    upstream.response = native_response(mode, finish=finish)
    with PDFAPI(native_config(upstream, mode), 1) as bridge:
        response = native_post(bridge)
        assert response.status_code == 422
        assert bridge.summary()["input_tokens"] == 31
        assert bridge.summary()["output_tokens"] == 12
        with pytest.raises(FormaError, match=expected):
            bridge.check_error()
        # A queued BabelDOC retry may arrive before its process is terminated.
        # Never spend another request after a fatal translation failure.
        assert native_post(bridge).status_code == 422
        assert len(upstream.requests) == 1


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
@pytest.mark.parametrize("payload", [[], {}, {"unexpected": "upstream-test-secret"}])
def test_native_pdf_bridge_never_returns_malformed_success(upstream, mode, payload):
    upstream.response = payload
    with PDFAPI(native_config(upstream, mode), 1) as bridge:
        response = native_post(bridge)
        assert response.status_code == 502
        assert "upstream-test-secret" not in response.text
        with pytest.raises(FormaError, match="некоректну відповідь"):
            bridge.check_error()


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_native_pdf_bridge_hides_native_error_payloads(upstream, mode):
    upstream.status = 401
    upstream.response = {"error": {"message": 'Echo upstream-test-secret and document text'}}
    upstream.response_headers = {"Retry-After": "upstream-test-secret"}
    with PDFAPI(native_config(upstream, mode), 1) as bridge:
        response = native_post(bridge)
        assert response.status_code == 401
        assert response.json() == {"error": {"message": "PDF provider request failed"}}
        assert "retry-after" not in response.headers
        with pytest.raises(FormaError, match="ключ"):
            bridge.check_error()


def test_pdf_pacing_is_shared_across_parallel_document_bridges(upstream):
    control = JobControl(2)
    settings = config(upstream, requests_per_minute=300)
    with (
        PDFAPI(settings, 1, control=control) as first,
        PDFAPI(settings, 1, control=control) as second,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = [pool.submit(post, bridge) for bridge in (first, second)]
        assert all(future.result().status_code == 200 for future in futures)
        assert len(upstream.requests) == 2
        assert upstream.request_times[1] - upstream.request_times[0] >= 0.16
        # Only a pacing reservation is consumed; PDF worker capacity is managed
        # by the document runner and remains available to this unit test.
        with control.request_slots(2):
            pass


def test_pdf_bridge_respects_cooldown_before_starting_another_request(upstream):
    upstream.status = 429
    upstream.response_headers = {"Retry-After": "0.2"}
    with PDFAPI(config(upstream), 2) as bridge:
        assert post(bridge).status_code == 429
        upstream.status = 200
        assert post(bridge).status_code == 200
        assert upstream.request_times[1] - upstream.request_times[0] >= 0.16


def test_pdf_and_office_share_one_request_rate_gate(upstream):
    control = JobControl(2)
    settings = config(upstream, requests_per_minute=300)
    upstream.response["choices"] = [
        {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}
    ]
    with PDFAPI(settings, 1, control=control) as bridge:
        provider = Provider(settings, control)
        try:
            assert post(bridge).status_code == 200
            provider.ping()
        finally:
            provider.close()
        assert len(upstream.requests) == 2
        assert upstream.request_times[1] - upstream.request_times[0] >= 0.16


def test_pdf_pacing_survives_separate_job_controls(upstream):
    settings = config(upstream, requests_per_minute=300)
    with PDFAPI(settings, 1) as first:
        assert post(first).status_code == 200
    # Closing a bridge consumes time, so explicitly reserve the same account's
    # next start before opening the next job. The reservation contains no key.
    first.control.defer_requests(first.rate_limit_key, 0.3)
    start = time.monotonic()
    with PDFAPI(settings, 1) as second:
        assert first.control is not second.control
        assert second.rate_limit_key == first.rate_limit_key
        assert settings.api_key not in second.rate_limit_key
        assert post(second).status_code == 200
        assert upstream.request_times[-1] - start >= 0.25


def test_gemini_pdf_bridge_reuses_retry_info_as_shared_cooldown(upstream):
    upstream.status = 429
    upstream.response = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0.2s"}
            ]
        }
    }
    with PDFAPI(native_config(upstream, "gemini"), 2) as bridge:
        response = native_post(bridge)
        assert response.status_code == 429
        assert float(response.headers["retry-after"]) == 0.2
        upstream.status, upstream.response = 200, native_response("gemini")
        assert native_post(bridge).status_code == 200
        assert upstream.request_times[1] - upstream.request_times[0] >= 0.16


def test_gemini_pdf_bridge_stops_after_terminal_project_quota(upstream):
    upstream.status = 429
    upstream.response = {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "86400s"},
            ]
        }
    }
    with PDFAPI(native_config(upstream, "gemini"), 2) as bridge:
        assert native_post(bridge).status_code == 429
        with pytest.raises(FormaError, match="квоту API"):
            bridge.check_error()
        assert native_post(bridge).status_code == 422
        assert len(upstream.requests) == 1


def test_pdf_bridge_cancels_long_pacing_wait_without_sending(upstream):
    with ThreadPoolExecutor(max_workers=1) as pool:
        with PDFAPI(config(upstream, requests_per_minute=1), 1) as bridge:
            assert post(bridge).status_code == 200
            future = pool.submit(post, bridge)
            # The second local request enters the rate gate without spending
            # another upstream request, then shutdown cancels its async wait.
            deadline = time.monotonic() + 2
            while not bridge.tasks and time.monotonic() < deadline:
                time.sleep(0.01)
            assert bridge.tasks
            start = time.monotonic()
        assert time.monotonic() - start < 2
        with pytest.raises(httpx.HTTPError):
            future.result(timeout=2)
        assert len(upstream.requests) == 1


@pytest.mark.parametrize("resume", [False, True])
def test_pdf_bridge_pauses_new_requests_and_can_resume_or_close(upstream, resume):
    control = JobControl(1)
    control.paused.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with PDFAPI(config(upstream), 1, control=control) as bridge:
            future = pool.submit(post, bridge)
            deadline = time.monotonic() + 2
            while not bridge.tasks and time.monotonic() < deadline:
                time.sleep(0.01)
            assert bridge.tasks
            time.sleep(0.15)
            assert not upstream.requests
            assert not future.done()
            if resume:
                control.paused.clear()
                assert future.result(timeout=2).status_code == 200
                assert len(upstream.requests) == 1
            start = time.monotonic()
        assert time.monotonic() - start < 2
        if not resume:
            with pytest.raises(httpx.HTTPError):
                future.result(timeout=2)
            assert not upstream.requests


@pytest.mark.parametrize("mode", ["gemini", "anthropic"])
def test_pdf_native_mode_reaches_babeldoc_without_exposing_key(
    upstream, pdf_file, tmp_path, monkeypatch, mode
):
    upstream.response = native_response(mode)
    monkeypatch.setattr("forma.pdf.find_babeldoc", lambda _: "babeldoc-test")

    class Process:
        returncode = 0

        def __init__(self, arguments, **kwargs):
            assert "upstream-test-secret" not in " ".join(arguments)
            config_path = Path(arguments[arguments.index("--config") + 1])
            values = tomllib.loads(config_path.read_text())["babeldoc"]
            assert values["openai-api-key"] != "upstream-test-secret"
            response = httpx.post(
                values["openai-base-url"] + "/chat/completions",
                headers={"Authorization": "Bearer " + values["openai-api-key"]},
                json={
                    "model": values["openai-model"],
                    "messages": [{"role": "user", "content": "Translate 123"}],
                },
            )
            assert response.status_code == 200
            (Path(values["output"]) / "sample.mono.pdf").write_bytes(pdf_file.read_bytes())

        def poll(self):
            return 0

    monkeypatch.setattr("forma.pdf.subprocess.Popen", Process)
    result = translate_pdf(
        pdf_file,
        tmp_path / "out",
        Options(source_language="en", target_language="en"),
        native_config(upstream, mode),
        JobControl(),
        lambda *_: None,
    )
    assert result.output.exists()
    assert result.input_tokens == 31 and result.output_tokens == 12
    assert "upstream-test-secret" not in result.report.read_text()
