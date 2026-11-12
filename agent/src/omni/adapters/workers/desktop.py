"""One bounded read-only desktop request per helper; stdout is IPC and never contains diagnostics."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.desktop import VERSION, parse_request, parse_response
from ..platform import desktop_native

MAX_FRAME = 65536
Record = dict[str, Any]


def _pairs(items: list[tuple[str, Any]]) -> Record:
    result: Record = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate desktop field")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("nonfinite desktop value")


def frame_data(frame: bytes) -> Record:
    if not 1 <= len(frame) <= MAX_FRAME or not frame.endswith(b"\n") or b"\n" in frame[:-1]:
        raise ValueError("invalid desktop frame")
    value = json.loads(frame, object_pairs_hook=_pairs, parse_constant=_constant)
    json.dumps(value, allow_nan=False)
    if not isinstance(value, dict):
        raise ValueError("invalid desktop object")
    return cast(Record, value)


def decode(frame: bytes) -> Record:
    return parse_request(frame_data(frame))


def perform(request: Record) -> Record:
    request = parse_request(request)
    try:
        if request["operation"] == "probe":
            result = desktop_native.probe()
        elif request["operation"] == "targets":
            result = desktop_native.targets(request["limit"])
        elif request["operation"] == "verify":
            result = desktop_native.verify(request["target"])
        else:
            raise BusinessError("desktop_observation_unavailable", "UIA requires the fixed native helper")
        response = {
            "version": VERSION,
            "request_id": request["request_id"],
            "operation": request["operation"],
            "ok": True,
            "result": result,
            "error_code": None,
        }
        return response
    except Exception:  # noqa: BLE001 -- native boundary returns static codes, never exception bodies
        # Static codes prevent native exception text, paths or UI data from
        # leaking into diagnostic streams or a partially valid result.
        return {
            "version": VERSION,
            "request_id": request["request_id"],
            "operation": request["operation"],
            "ok": False,
            "result": None,
            "error_code": "desktop_native_unavailable",
        }


def encode(response: Record, request: Record) -> bytes:
    def serialize():
        return json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"

    frame = serialize()
    if response["ok"] and request["operation"] == "targets":
        while len(frame) > MAX_FRAME and response["result"]["items"]:
            response["result"]["items"].pop()
            response["result"]["truncated"] = True
            frame = serialize()
    if len(frame) > MAX_FRAME:
        response = {
            "version": VERSION,
            "request_id": request["request_id"],
            "operation": request["operation"],
            "ok": False,
            "result": None,
            "error_code": "desktop_response_too_large",
        }
        frame = serialize()
    try:
        parse_response(response, request)
    except BusinessError:
        response = {
            "version": VERSION,
            "request_id": request["request_id"],
            "operation": request["operation"],
            "ok": False,
            "result": None,
            "error_code": "desktop_native_unavailable",
        }
        frame = serialize()
        parse_response(response, request)
    return frame


def main() -> int:
    try:
        request = decode(sys.stdin.buffer.read(MAX_FRAME + 1))
        if sys.platform == "linux":
            from .linux_desktop import perform as perform_linux

            response = asyncio.run(perform_linux(request))
        else:
            response = perform(request)
        frame = encode(response, request)
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()
        return 0
    except Exception:  # noqa: BLE001 -- malformed frames and unexpected native failures must not print a traceback
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
