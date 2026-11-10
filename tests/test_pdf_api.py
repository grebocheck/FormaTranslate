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
from forma.provider import JobControl


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
    server.requests, server.response_headers = [], {}
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
