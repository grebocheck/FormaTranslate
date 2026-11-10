from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from forma.config import Settings
from forma.model_catalog import MAX_RESPONSE_BYTES, list_models
from forma.models import FormaError


@contextmanager
def catalog_server(*, status=200, payload=None, body=None, headers=None, declare_length=True):
    requests = []
    content = body if body is not None else json.dumps(payload).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append({"path": self.path, "authorization": self.headers.get("Authorization")})
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if declare_length:
                self.send_header("Content-Length", str(len(content)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reads_models_without_configured_model_and_keeps_exact_ids():
    with catalog_server(
        payload={
            "data": [
                {"id": "z-model"},
                {"id": "Alpha"},
                {"id": "z-model"},
                {"id": "provider/embedding"},
            ]
        }
    ) as (base, requests):
        result = list_models(Settings(base_url=base + "/chat/completions/", api_key="fixture-key"))
    assert result == ["Alpha", "provider/embedding", "z-model"]
    assert requests == [{"path": "/v1/models", "authorization": "Bearer fixture-key"}]


def test_local_provider_works_without_auth_and_empty_catalog_is_valid():
    with catalog_server(payload={"data": []}) as (base, requests):
        assert list_models(Settings(base_url=base)) == []
    assert requests == [{"path": "/v1/models", "authorization": None}]


@pytest.mark.parametrize("status", [401, 403, 404, 405, 429, 500])
def test_status_errors_are_safe_and_not_retried(status):
    secret = "private-fixture-key"
    with catalog_server(
        status=status, payload={"error": f"{secret} https://secret-provider.invalid/private"}
    ) as (base, requests):
        with pytest.raises(FormaError) as caught:
            list_models(Settings(base_url=base, api_key=secret))
    message = str(caught.value)
    assert f"HTTP {status}" in message
    assert secret not in message and "secret-provider" not in message
    assert len(requests) == 1
    if status in {404, 405}:
        assert "вручну" in message
    if status in {401, 403}:
        assert "API-ключ" in message


def test_redirect_is_never_followed_or_given_authorization():
    with catalog_server(payload={"data": [{"id": "should-not-be-read"}]}) as (
        target,
        target_requests,
    ):
        with catalog_server(status=307, headers={"Location": target + "/models"}) as (
            base,
            requests,
        ):
            with pytest.raises(FormaError, match="HTTP 307"):
                list_models(Settings(base_url=base, api_key="fixture-key"))
        assert len(requests) == 1
        assert not target_requests


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {},
        {"data": {}},
        {"data": ["model"]},
        {"data": [{}]},
        {"data": [{"id": ""}]},
        {"data": [{"id": "  "}]},
        {"data": [{"id": 3}]},
        {"data": [{"id": "bad\nmodel"}]},
        {"data": [{"id": "bad\x00model"}]},
        {"data": [{"id": "a" * 257}]},
    ],
)
def test_invalid_catalog_shape_and_identifiers_are_rejected(payload):
    with catalog_server(payload=payload) as (base, _):
        with pytest.raises(FormaError, match="некоректний список"):
            list_models(Settings(base_url=base))


def test_invalid_json_does_not_echo_response_contents():
    with catalog_server(body=b"private-fixture-key invalid JSON") as (base, _):
        with pytest.raises(FormaError) as caught:
            list_models(Settings(base_url=base))
    assert "private-fixture-key" not in str(caught.value)


@pytest.mark.parametrize("declare_length", [False, True])
def test_oversized_response_is_rejected_with_or_without_content_length(declare_length):
    body = b" " * (MAX_RESPONSE_BYTES + 1)
    with catalog_server(body=body, declare_length=declare_length) as (base, _):
        with pytest.raises(FormaError, match="розмір"):
            list_models(Settings(base_url=base))


def test_client_timeout_is_capped_and_network_error_does_not_leak(monkeypatch):
    captured = {}

    class FailingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def stream(self, *_):
            raise httpx.ConnectError("private-fixture-key https://secret-provider.invalid")

    monkeypatch.setattr("forma.model_catalog.httpx.Client", FailingClient)
    with pytest.raises(FormaError) as caught:
        list_models(
            Settings(base_url="http://localhost/v1", timeout=120, api_key="private-fixture-key")
        )
    assert captured["timeout"] == 20 and captured["follow_redirects"] is False
    assert "private-fixture-key" not in str(caught.value)
    assert "secret-provider" not in str(caught.value)


def native_catalog(monkeypatch, responder):
    original = httpx.Client
    requests = []

    def handle(request):
        requests.append(request)
        return responder(request, len(requests))

    monkeypatch.setattr(
        "forma.model_catalog.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    return requests


def test_gemini_catalog_pages_filter_generation_and_use_header_key(monkeypatch):
    def respond(request, count):
        if count == 1:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-a",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/embedding-a",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ],
                    "nextPageToken": "opaque+/=?cursor",
                },
            )
        assert request.url.params["pageToken"] == "opaque+/=?cursor"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]},
                ]
            },
        )

    requests = native_catalog(monkeypatch, respond)
    result = list_models(
        Settings(
            api_mode="gemini",
            base_url="https://google.example/v1beta",
            api_key="fixture-native-key",
        )
    )
    assert result == ["gemini-a", "gemini-b"]
    assert all(r.headers.get("x-goog-api-key") == "fixture-native-key" for r in requests)
    assert all("fixture-native-key" not in str(r.url) for r in requests)
    assert len(requests) == 2


def test_anthropic_catalog_pagination_and_retired_models(monkeypatch):
    def respond(request, count):
        if count == 1:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "claude-a", "lifecycle": "active"},
                        {"id": "retired", "lifecycle": "retired"},
                    ],
                    "has_more": True,
                    "last_id": "claude-a",
                },
            )
        assert request.url.params["after_id"] == "claude-a"
        return httpx.Response(200, json={"data": [{"id": "claude-b"}], "has_more": False})

    requests = native_catalog(monkeypatch, respond)
    result = list_models(
        Settings(
            api_mode="anthropic", base_url="https://claude.example/v1", api_key="fixture-native-key"
        )
    )
    assert result == ["claude-a", "claude-b"]
    assert all(r.headers.get("authorization") == "Bearer fixture-native-key" for r in requests)
    assert all(r.headers.get("anthropic-version") == "2026-10-21" for r in requests)


def test_catalog_repeated_cursor_fails_without_following_url(monkeypatch):
    requests = native_catalog(
        monkeypatch,
        lambda *_: httpx.Response(
            200, json={"models": [], "nextPageToken": "https://external.invalid/private"}
        ),
    )
    with pytest.raises(FormaError, match="некоректний список"):
        list_models(Settings(api_mode="gemini", base_url="https://google.example/v1beta"))
    assert len(requests) == 2
    assert all(r.url.host == "google.example" for r in requests)


def test_catalog_total_pagination_size_is_bounded(monkeypatch):
    counter = iter(range(100))
    requests = native_catalog(
        monkeypatch,
        lambda *_: httpx.Response(
            200,
            json={
                "models": [],
                "nextPageToken": str(next(counter)),
                "padding": " " * (MAX_RESPONSE_BYTES // 2),
            },
        ),
    )
    with pytest.raises(FormaError, match="розмір"):
        list_models(Settings(api_mode="gemini", base_url="https://google.example/v1beta"))
    assert len(requests) == 2
