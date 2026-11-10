"""Private, short-lived Chat Completions adapter for the external PDF engine.

BabelDOC 0.7.1 has no CLI controls for the native Chat reasoning_effort field,
request timeout or completion budget. Keep its prompts and layout pipeline intact
while applying the same explicit provider settings as Forma's Office translator.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import secrets
import socket
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from forma.models import FormaError

MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
REASONING_MODELS = ("gpt-5", "gpt-6", "o1", "o3", "o4")


def provider_base(url):
    base = url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        base = base.removesuffix(suffix)
    return base


def completion_body(body: dict, settings) -> dict:
    body = {**body, "model": settings.model}
    reasoning_model = settings.model.startswith(REASONING_MODELS)
    if settings.reasoning_effort and reasoning_model:
        body["reasoning_effort"] = settings.reasoning_effort
    if settings.max_output_tokens:
        body.pop("max_tokens", None)
        body["max_completion_tokens"] = settings.max_output_tokens
    elif reasoning_model and "max_tokens" in body:
        # Preserve BabelDOC's limit, using the native reasoning-model spelling.
        body["max_completion_tokens"] = body.pop("max_tokens")
    return body


def completion_error(data: bytes) -> str:
    try:
        choices = json.loads(data).get("choices", [])
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            reason = choice.get("finish_reason")
            message = choice.get("message") or {}
            if reason == "length":
                return (
                    "Відповідь PDF-перекладача обірвалася через ліміт токенів. "
                    "Збільште ліміт відповіді або перевірте режим міркування моделі; "
                    "неповний переклад не збережено."
                )
            if reason == "content_filter" or (isinstance(message, dict) and message.get("refusal")):
                return "API відмовив у перекладі фрагмента PDF. Неповний переклад не збережено."
            if isinstance(message, dict) and "content" in message:
                content = message["content"]
                if not isinstance(content, str) or not content.strip():
                    return "API повернув порожній переклад PDF. Перевірте модель і ліміт відповіді."
    except (ValueError, AttributeError, TypeError):
        pass
    return ""


def _redact(data, secret):
    if not secret:
        return data

    def clean(value):
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]")
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {clean(key): clean(item) for key, item in value.items()}
        return value

    try:
        return json.dumps(clean(json.loads(data)), ensure_ascii=False).encode()
    except (ValueError, RecursionError):
        # Non-JSON error pages may echo arbitrary escaped authentication data.
        return b'{"error":{"message":"Invalid response from PDF provider"}}'


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, bridge):
        self.bridge = bridge
        self.connections = set()
        self.workers = set()
        self.connections_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)

    def process_request(self, request, client_address):
        with self.connections_lock:
            self.connections.add(request)
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        with self.connections_lock:
            self.workers.add(threading.current_thread())
        request.settimeout(10)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.connections_lock:
                self.connections.discard(request)
                self.workers.discard(threading.current_thread())

    def close_connections(self):
        with self.connections_lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def join_workers(self):
        deadline = time.monotonic() + 1
        with self.connections_lock:
            workers = list(self.workers)
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))


class _Handler(BaseHTTPRequestHandler):
    # Closing after each response bounds idle local connections during teardown.
    protocol_version = "HTTP/1.0"

    def log_message(self, *_):
        pass

    def _reply(self, status, data, headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            for key in ("retry-after", "retry-after-ms"):
                if headers and key in headers:
                    self.send_header(key, headers[key])
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass
        self.close_connection = True

    def do_POST(self):
        bridge = self.server.bridge
        if not hmac.compare_digest(
            self.headers.get("Authorization", "").encode("utf-8"),
            f"Bearer {bridge.token}".encode(),
        ):
            self._reply(401, b'{"error":{"message":"Local PDF authorization required"}}')
            return
        if self.path != "/v1/chat/completions":
            self._reply(404, b'{"error":{"message":"Unsupported PDF API path"}}')
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
                self._reply(413, b'{"error":{"message":"Invalid PDF request size"}}')
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("stream"):
                raise ValueError
        except (ValueError, OSError):
            self._reply(400, b'{"error":{"message":"Invalid PDF API request"}}')
            return
        if bridge.closing:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(bridge._forward(body), bridge.loop)
            status, data, headers = future.result()
        except (concurrent.futures.CancelledError, RuntimeError):
            return
        except Exception:
            # Exceptions may contain URLs or echoed credentials; never return them.
            self._reply(502, b'{"error":{"message":"PDF provider request failed"}}')
            return
        self._reply(status, data, headers)


class PDFAPI:
    def __init__(self, settings, workers):
        self.settings, self.workers = settings, workers
        self.closing = False
        self.fatal_error = ""
        self.upstream_base = provider_base(settings.base_url)
        self.token = secrets.token_urlsafe(32)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.tasks = set()
        self.lock = threading.Lock()
        self.calls = self.active = self.peak = self.input_tokens = self.output_tokens = 0
        self.elapsed = 0.0
        self.statuses = Counter()
        # BabelDOC's cache does not include the upstream URL or our request
        # overrides. Partition it without sending this synthetic ID upstream.
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "protocol": 1,
                    "base_url": self.upstream_base,
                    "model": settings.model,
                    "reasoning_effort": settings.reasoning_effort,
                    "max_output_tokens": settings.max_output_tokens,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]
        self.cache_model = f"{settings.model}-forma-{fingerprint}"

    def __enter__(self):
        self.thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self._open(), self.loop).result(timeout=5)
            self.server = _Server(self)
            self.server_thread = threading.Thread(
                target=lambda: self.server.serve_forever(poll_interval=0.1), daemon=True
            )
            self.server_thread.start()
        except Exception:
            self._close_loop()
            raise
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    async def _open(self):
        self.semaphore = asyncio.Semaphore(self.workers)
        self.client = httpx.AsyncClient(
            timeout=self.settings.timeout,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=self.workers, max_keepalive_connections=self.workers
            ),
        )

    async def _forward(self, original):
        if self.closing:
            raise asyncio.CancelledError
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with self.semaphore:
                started = time.monotonic()
                with self.lock:
                    self.calls += 1
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                status, data, headers = (
                    502,
                    b'{"error":{"message":"PDF provider request failed"}}',
                    {},
                )
                try:
                    authorization = (
                        {"Authorization": f"Bearer {self.settings.api_key}"}
                        if self.settings.api_key
                        else {}
                    )
                    async with self.client.stream(
                        "POST",
                        self.upstream_base + "/chat/completions",
                        json=completion_body(original, self.settings),
                        headers=authorization,
                    ) as response:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > MAX_RESPONSE:
                                raise ValueError("Response limit exceeded")
                            content.extend(chunk)
                        status, data = response.status_code, bytes(content)
                        for key in ("retry-after", "retry-after-ms"):
                            value = response.headers.get(key)
                            if value and "\n" not in value and "\r" not in value:
                                headers[key] = value
                    fatal = {
                        400: "API відхилив параметри PDF-запиту. Перевірте модель, ліміт відповіді та режим міркування.",
                        401: "API відхилив ключ для PDF-перекладу. Перевірте ключ у налаштуваннях.",
                        403: "API заборонив PDF-переклад. Перевірте доступ до вибраної моделі.",
                        404: "API не знайшов модель або адресу PDF-перекладу. Перевірте налаштування.",
                    }.get(status)
                    if fatal:
                        with self.lock:
                            self.fatal_error = fatal
                    data = _redact(data, self.settings.api_key)
                    self._usage(data)
                    incomplete = completion_error(data) if 200 <= status < 300 else ""
                    if incomplete:
                        with self.lock:
                            self.fatal_error = incomplete
                        # Do not let BabelDOC cache a partial/refused paragraph.
                        status, data = (
                            422,
                            b'{"error":{"message":"Incomplete PDF translation response"}}',
                        )
                except httpx.TimeoutException:
                    status, data = 504, b'{"error":{"message":"PDF provider request timed out"}}'
                except (httpx.HTTPError, ValueError):
                    pass
                finally:
                    with self.lock:
                        self.active -= 1
                        self.elapsed += time.monotonic() - started
                        self.statuses[str(status)] += 1
                return status, data, headers
        finally:
            self.tasks.discard(task)

    def _usage(self, data):
        try:
            usage = json.loads(data).get("usage", {})
            inputs = usage.get("prompt_tokens", 0)
            outputs = usage.get("completion_tokens", 0)
            if not isinstance(inputs, int) or not isinstance(outputs, int):
                return
            with self.lock:
                self.input_tokens += max(0, inputs)
                self.output_tokens += max(0, outputs)
        except (ValueError, AttributeError, TypeError):
            pass

    def summary(self):
        with self.lock:
            return {
                "requests": self.calls,
                "peak_concurrent_requests": self.peak,
                "mean_request_seconds": round(self.elapsed / max(1, self.calls), 3),
                "statuses": dict(self.statuses),
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            }

    def check_error(self):
        with self.lock:
            message = self.fatal_error
        if message:
            raise FormaError(message)

    async def _shutdown(self):
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if hasattr(self, "client"):
            await self.client.aclose()

    def _close_loop(self):
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(timeout=3)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)
            if not self.thread.is_alive():
                self.loop.close()

    def __exit__(self, *_):
        self.closing = True
        self.server.shutdown()
        self.server.close_connections()
        self.server.server_close()
        self.server_thread.join(timeout=1)
        self._close_loop()
        self.server.join_workers()
