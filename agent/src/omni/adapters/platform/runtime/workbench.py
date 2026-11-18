"""Open an explicitly requested local workbench after proving this process's ready boot; never reads owner tokens."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, cast

import httpx
import psutil

from omni.domain.contracts import BusinessError
from omni.adapters.platform.runtime.runtime_identity import read_private
from omni.adapters.platform.services.service_readiness import verify_proof

log = logging.getLogger("omni.v2.workbench")


class WorkbenchBrowser:
    def __init__(self, home: Path, host: str, port: int, *, open_url: Callable[[str], object] | None = None) -> None:
        try:
            address = ipaddress.ip_address(
                {"localhost": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
            )
        except ValueError as exc:
            raise BusinessError(
                "workbench_listener_invalid", "Browser launch requires a literal local listener", status=422
            ) from exc
        if not address.is_loopback or not 1 <= port <= 65535:
            raise BusinessError(
                "workbench_listener_invalid", "Browser launch requires a fixed local listener", status=422
            )
        authority = f"[{address}]" if address.version == 6 else str(address)
        self._origin = f"http://{authority}:{port}"
        self._control, self._created = home / "run", psutil.Process().create_time()
        self._open = open_url or webbrowser.open
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> WorkbenchBrowser:
        if self._thread is not None:
            raise RuntimeError("Workbench browser is already owned")
        self._thread = threading.Thread(target=self._wait, name="workbench-browser")
        self._thread.start()
        return self

    def __exit__(
        self, _kind: type[BaseException] | None, _error: BaseException | None, _traceback: TracebackType | None
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _ready(self, client: httpx.Client, deadline: float | None = None) -> bool:
        deadline = time.monotonic() + 90 if deadline is None else deadline
        raw = read_private(self._control / "runtime.json")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise BusinessError("workbench_identity_invalid", "Workbench identity must be an object")
        identity = cast(dict[str, Any], value)
        if (
            identity.get("format") != "omni-runtime-identity-1"
            or identity.get("state") != "running"
            or type(identity.get("pid")) is not int
            or identity["pid"] != os.getpid()
            or type(identity.get("process_created")) not in {int, float}
            or identity["process_created"] != self._created
            or identity.get("service_plan_sha256") is not None
        ):
            raise BusinessError("workbench_identity_mismatch", "Workbench is not this manual runtime boot")
        key = read_private(self._control / "readiness.key", 32)
        if len(key) != 32:
            raise BusinessError("workbench_key_invalid", "Workbench readiness key is invalid")
        challenge = secrets.token_hex(32)
        with client.stream(
            "GET",
            self._origin + "/health/identity",
            params={"challenge": challenge},
            headers={"Accept-Encoding": "identity"},
        ) as response:
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise BusinessError("workbench_proof_invalid", "Workbench proof must be uncompressed")
            content = bytearray()
            for chunk in response.iter_bytes():
                if self._stop.is_set() or time.monotonic() >= deadline:
                    return False
                if len(content) + len(chunk) > 65536:
                    raise BusinessError("workbench_proof_invalid", "Workbench proof is too large")
                content.extend(chunk)
        proof = json.loads(content)
        if not isinstance(proof, dict):
            raise BusinessError("workbench_proof_invalid", "Workbench proof must be an object")
        report = verify_proof(cast(dict[str, Any], proof), identity, challenge, key)
        return report["ready"] is True and read_private(self._control / "runtime.json") == raw

    def _wait(self) -> None:
        deadline = time.monotonic() + 90
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=3) as client:
                while not self._stop.is_set() and time.monotonic() < deadline:
                    try:
                        ready = self._ready(client, deadline)
                    except (BusinessError, OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
                        ready = False
                    if ready and not self._stop.is_set():
                        self._open(self._origin + "/")
                        return
                    self._stop.wait(0.2)
        except Exception:  # noqa: BLE001 - browser startup is optional; do not interrupt the owned server
            log.warning("Workbench browser unavailable", extra={"event": "workbench.browser_unavailable"})
