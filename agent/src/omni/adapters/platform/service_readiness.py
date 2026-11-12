"""Bounded, per-boot HMAC readiness proof. Never reads or sends an owner credential."""

from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import httpx
import psutil

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.service_control import ControlPlan, drain_request, verify_receipt
from ...domain.service_deployment import service_argv, validate_plan
from .runtime_identity import READINESS_FIELDS, canonical_proof, read_private

PROBE_SECONDS = 5.0
MAX_PROBES = 4


def _decode(content: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in values:
            if name in result:
                raise ValueError("Duplicate readiness field")
            result[name] = value
        return result

    def constant(_value: str) -> None:
        raise ValueError("Nonfinite readiness value")

    value = json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("Readiness proof must be an object")
    return cast(dict[str, Any], value)


def _preflight(plan: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    request, runtime = plan["request"], plan["runtime"]
    root = Path(request["home"]) / "run"
    identity = _decode(read_private(root / "runtime.json"))
    expected = {
        "format": "omni-runtime-identity-1",
        "instance_id": request["instance_id"],
        "build_id": runtime["build_id"],
        "api_version": 1,
        "schema_version": runtime["schema_head"],
        "service_plan_sha256": plan["sha256"],
        "state": "running",
    }
    if any(
        identity.get(name) != value or type(identity.get(name)) is not type(value) for name, value in expected.items()
    ):
        raise BusinessError("service_identity_mismatch", "Runtime belongs to another deployment or is draining")
    boot = identity.get("boot_id")
    if not isinstance(boot, str) or str(uuid.UUID(boot)) != boot:
        raise BusinessError("service_identity_mismatch", "Runtime boot identity is invalid")
    pid, created = identity.get("pid"), identity.get("process_created")
    if (
        type(pid) is not int
        or pid < 1
        or not isinstance(created, (int, float))
        or isinstance(created, bool)
        or not math.isfinite(created)
        or created <= 0
    ):
        raise BusinessError("service_process_unverified", "Runtime process identity is invalid")
    process = psutil.Process(pid)
    if abs(process.create_time() - created) > 0.001 or not process.is_running():
        raise BusinessError("service_process_changed", "Runtime process identity has changed")
    if os.path.normcase(os.path.realpath(process.exe())) != os.path.normcase(runtime["process_python"]):
        raise BusinessError("service_process_changed", "Runtime executable differs from the deployment")
    actual, expected_argv = process.cmdline(), service_argv(plan)
    if len(actual) != len(expected_argv) or actual[1:] != expected_argv[1:]:
        raise BusinessError("service_process_changed", "Runtime command differs from the deployment")
    raw_host = str(request["host"])
    host = {"localhost": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(raw_host, raw_host)
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise BusinessError(
            "service_local_probe_unavailable", "Readiness requires a literal loopback listener"
        ) from exc
    if not address.is_loopback:
        raise BusinessError("service_local_probe_unavailable", "Readiness requires a loopback listener")
    if not any(
        item.status == psutil.CONN_LISTEN
        and item.laddr.port == request["port"]
        and (item.laddr.ip == str(address) or item.laddr.ip in {"0.0.0.0", "::"})
        for item in process.net_connections(kind="tcp")
    ):
        raise BusinessError("service_listener_unverified", "The deployment process does not own the listener")
    # The key never crosses the connection. A replaced listener can learn only a public nonce.
    key = read_private(root / "readiness.key", 32)
    if len(key) != 32:
        raise BusinessError("service_readiness_key_invalid", "Readiness key must contain exactly 32 private bytes")
    authority = f"[{address}]" if address.version == 6 else str(address)
    return identity, key, f"http://{authority}:{request['port']}"


def verify_proof(proof: dict[str, Any], identity: dict[str, Any], challenge: str, key: bytes) -> dict[str, Any]:
    if set(proof) != {"identity", "challenge", "signature"} or proof["challenge"] != challenge:
        raise BusinessError("service_response_invalid", "Readiness response challenge or fields differ")
    report = proof["identity"]
    if not isinstance(report, dict):
        raise BusinessError("service_response_invalid", "Readiness response shape is invalid")
    checked = cast(dict[str, Any], report)
    if (
        set(checked) != {*READINESS_FIELDS, "ready"}
        or type(checked["ready"]) is not bool
        or not isinstance(proof["signature"], str)
        or re.fullmatch(r"[a-f0-9]{64}", proof["signature"]) is None
    ):
        raise BusinessError("service_response_invalid", "Readiness response shape is invalid")
    if any(
        checked[name] != identity.get(name) or type(checked[name]) is not type(identity.get(name))
        for name in READINESS_FIELDS
    ):
        raise BusinessError("service_identity_mismatch", "Readiness response belongs to another boot or program")
    expected = canonical_proof(checked, challenge, key)
    expected_signature = expected["signature"]
    if not isinstance(expected_signature, str) or not hmac.compare_digest(proof["signature"], expected_signature):
        raise BusinessError("service_signature_invalid", "Readiness signature does not prove this private boot")
    return {**{name: checked[name] for name in READINESS_FIELDS}, "ready": checked["ready"], "error_code": None}


class ServiceReadiness:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-service-proof")
        self._pending: set[asyncio.Future[Any]] = set()
        self._tasks: set[asyncio.Task[Record]] = set()
        self._closing = self._closed = False

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._tasks),
            "preflight_pending": len(self._pending),
            "max_pending": MAX_PROBES,
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def probe(self, plan: Record) -> Record:
        if self._closing or len(self._tasks) >= MAX_PROBES or len(self._pending) >= MAX_PROBES:
            return {"ready": False, "error_code": "service_probe_busy"}
        checked = validate_plan(plan)
        if cast(dict[str, Any], checked["runtime"])["platform"] != sys.platform:
            return {"ready": False, "error_code": "service_target_unsupported"}
        deadline = asyncio.get_running_loop().time() + PROBE_SECONDS
        task = asyncio.create_task(self._probe(checked, deadline), name="service-readiness-proof")
        self._tasks.add(task)

        def finished(done: asyncio.Task[Record]) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _probe(self, plan: dict[str, Any], deadline: float) -> Record:
        if self._closing:
            return {"ready": False, "error_code": "service_probe_busy"}
        try:
            async with asyncio.timeout_at(deadline):
                future = asyncio.get_running_loop().run_in_executor(self._executor, _preflight, plan)
                self._pending.add(future)

                def finished(done: asyncio.Future[Any]) -> None:
                    self._pending.discard(done)
                    if not done.cancelled():
                        done.exception()

                future.add_done_callback(finished)
                identity, key, origin = await asyncio.shield(future)
                challenge = secrets.token_hex(32)
                async with (
                    httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3) as client,
                    client.stream(
                        "GET",
                        origin + "/health/identity",
                        params={"challenge": challenge},
                        headers={"Accept-Encoding": "identity"},
                    ) as response,
                ):
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise BusinessError("service_response_invalid", "Readiness proof must be uncompressed")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 65536:
                            raise BusinessError("service_response_invalid", "Readiness proof is too large")
                        content.extend(chunk)
                return verify_proof(_decode(bytes(content)), identity, challenge, key)
        except (
            BusinessError,
            OSError,
            psutil.Error,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            TimeoutError,
            RecursionError,
        ) as exc:
            return {
                "ready": False,
                "error_code": exc.code if isinstance(exc, BusinessError) else "service_probe_unavailable",
            }

    async def drain(self, operation: ControlPlan) -> Record:
        if self._closing or len(self._tasks) >= MAX_PROBES or len(self._pending) >= MAX_PROBES:
            return {"effect": "none", "accepted": False, "error_code": "service_probe_busy"}
        task = asyncio.create_task(self._drain(operation), name="reviewed-service-drain")
        self._tasks.add(task)
        task.add_done_callback(
            lambda done: (self._tasks.discard(done), done.exception() if not done.cancelled() else None)
        )
        return await asyncio.shield(task)

    async def _drain(self, operation: ControlPlan) -> Record:
        dispatched = False
        try:
            async with asyncio.timeout(PROBE_SECONDS):
                future = asyncio.get_running_loop().run_in_executor(self._executor, _preflight, operation["deployment"])
                self._pending.add(future)
                future.add_done_callback(
                    lambda done: (self._pending.discard(done), done.exception() if not done.cancelled() else None)
                )
                identity, key, origin = await asyncio.shield(future)
                if operation["action"] != "stop" or identity["boot_id"] != operation["boot_id"]:
                    raise BusinessError("service_control_boot_changed", "The reviewed boot is no longer running")
                challenge = secrets.token_hex(32)

                async def response_json(
                    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
                ) -> dict[str, Any]:
                    async with client.stream(
                        method, origin + path, headers={"Accept-Encoding": "identity"}, **kwargs
                    ) as response:
                        response.raise_for_status()
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise BusinessError("service_response_invalid", "Control receipts must be uncompressed")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(data) + len(chunk) > 16384:
                                raise BusinessError("service_response_invalid", "Control receipt is too large")
                            data.extend(chunk)
                        return _decode(bytes(data))

                async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3) as client:
                    proof = await response_json(client, "GET", "/health/identity", params={"challenge": challenge})
                    verify_proof(proof, identity, challenge, key)
                    client.cookies.clear()
                    request = drain_request(operation, dt.datetime.now(dt.timezone.utc).isoformat(), key)
                    dispatched = True
                    receipt = await response_json(client, "POST", "/api/v1/service/drain", json=request)
                    verify_receipt(receipt, request, key)
                return {"effect": "unknown", "accepted": True, "error_code": ""}
        except (
            BusinessError,
            OSError,
            psutil.Error,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            TimeoutError,
            RecursionError,
        ) as exc:
            return {
                "effect": "unknown" if dispatched else "none",
                "accepted": False,
                "error_code": exc.code if isinstance(exc, BusinessError) else "service_drain_unavailable",
            }

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        pending = {future for future in (*self._tasks, *self._pending) if not future.done()}
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=max(0, timeout_s))
            if unfinished:
                return False
        if any(not future.done() for future in (*self._tasks, *self._pending)):
            return False
        if not self._closed:
            self._executor.shutdown(wait=False)
            self._closed = True
        return True


decode = _decode
