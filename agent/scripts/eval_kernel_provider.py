"""Record provider-reported usage at the common loopback inference boundary."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def provider_port(endpoint: str) -> int:
    url = urlsplit(endpoint)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.path != "/v1"
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Use a dedicated loopback OpenAI-compatible endpoint ending in /v1")
    return url.port


def response_usage(body: bytes, *, streaming: bool) -> tuple[int, int] | None:
    try:
        if streaming:
            events = [line[6:] for line in body.splitlines() if line.startswith(b"data: ")]
            if not events or events.pop() != b"[DONE]":
                return None
            replies = [json.loads(event) for event in events]
        else:
            replies = [json.loads(body)]
        usage = next((row["usage"] for row in reversed(replies) if isinstance(row.get("usage"), dict)), {})
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if type(prompt) is int and type(completion) is int and min(prompt, completion) >= 0:
            return prompt, completion
    except (UnicodeError, ValueError, AttributeError):
        pass
    return None


class Meter:
    def __init__(self, endpoint: str, profile: dict, replicate: int, *, reject_first: bool = False) -> None:
        self.port = provider_port(endpoint)
        self.profile = profile
        self.seed = 86407 + replicate
        self.reject_first = reject_first
        self._lock = threading.Lock()
        self.requests: list[dict] = []

    def admit(self, payload: dict) -> dict | None:
        with self._lock:
            if len(self.requests) >= self.profile["max_calls"]:
                return None
            messages = payload.get("messages", [])
            row = {
                "tools": [tool["function"]["name"] for tool in payload.get("tools") or []],
                "message_count": len(messages),
                "message_bytes": len(json.dumps(messages, ensure_ascii=False).encode("utf-8")),
                "usage": None,
            }
            if self.reject_first and not self.requests:
                row.update(fault="rejected_before_generation", status=429, usage=(0, 0))
            self.requests.append(row)
            return row

    def totals(self) -> dict:
        with self._lock:
            usage = [row["usage"] for row in self.requests]
        settled = all(entry is not None for entry in usage)
        return {
            "model_calls": len(usage),
            "input_tokens": sum(entry[0] for entry in usage) if settled else None,
            "output_tokens": sum(entry[1] for entry in usage) if settled else None,
            "cost_micro": 0 if settled else None,
        }

    @contextmanager
    def listen(self):
        meter = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send_payload(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != "/v1/models":
                    self.send_error(404)
                    return
                data = {"object": "list", "data": [{"id": meter.profile["model"], "object": "model"}]}
                self.send_payload(200, json.dumps(data).encode())

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2 * 1024 * 1024:
                        raise ValueError("Request body exceeds the evaluation limit")
                    request = json.loads(self.rfile.read(length))
                    streaming = bool(request.get("stream"))
                    output_limit = meter.profile["output_tokens"]
                    requested_output = request.get("max_tokens") or request.get("max_completion_tokens")
                    if type(requested_output) is int and requested_output > 0:
                        output_limit = min(output_limit, requested_output)
                    allowed = {"messages", "tools", "tool_choice", "parallel_tool_calls", "response_format"}
                    payload = {key: value for key, value in request.items() if key in allowed and value is not None}
                    payload.update(
                        model=meter.profile["model"],
                        stream=streaming,
                        max_tokens=output_limit,
                        temperature=0.2,
                        seed=meter.seed,
                    )
                    if streaming:
                        payload["stream_options"] = {"include_usage": True}
                    row = meter.admit(payload)
                except (AttributeError, KeyError, TypeError, ValueError):
                    self.send_error(400, "Invalid inference request")
                    return
                if row is None:
                    self.send_payload(429, b'{"error":{"message":"Trial model-call limit reached"}}')
                    return
                if row.get("fault") == "rejected_before_generation":
                    self.send_payload(
                        429,
                        b'{"error":{"message":"Fixture admission rejected before generation",'
                        b'"type":"rate_limit_error","code":"rate_limit_exceeded"},'
                        b'"usage":{"prompt_tokens":0,"completion_tokens":0}}',
                    )
                    return
                connection = http.client.HTTPConnection("127.0.0.1", meter.port, timeout=180)
                try:
                    connection.request("POST", self.path, json.dumps(payload), {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    body = response.read(2 * 1024 * 1024 + 1)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError("Provider response exceeds the evaluation limit")
                    with meter._lock:
                        row["status"] = response.status
                        if response.status == 200:
                            row["usage"] = response_usage(body, streaming=streaming)
                        elif response.status in (400, 404, 413, 422, 429):
                            row["usage"] = (0, 0)
                    self.send_payload(response.status, body, response.getheader("Content-Type", "application/json"))
                except (OSError, ValueError, http.client.HTTPException):
                    self.close_connection = True
                finally:
                    connection.close()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
