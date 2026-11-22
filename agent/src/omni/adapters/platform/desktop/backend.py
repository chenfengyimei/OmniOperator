"""Bounded, one-shot desktop metadata helpers; construction never probes a native desktop."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import sys
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from omni.adapters.platform.desktop import desktop_capture, macos_helper
from omni.adapters.platform.desktop.linux.linux_lease_client import LeaseClientPort, LinuxLeaseClient
from omni.adapters.platform.desktop.macos_launch import LaunchedHelper, LaunchError, launch_helper
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.application.ports import Record
from omni.domain.contracts import JSON, BusinessError
from omni.domain.desktop import (
    LINUX_ERRORS,
    MAX_FRAME,
    VERSION,
    CapturedFrame,
    encode_native_target,
    parse_request,
    parse_response,
)
from omni.domain.desktop_actions import linux_ready
from omni.domain.foreground import TIMEOUT_S as FOREGROUND_TIMEOUT_S
from omni.domain.foreground import permit, ready

MAX_PENDING = 4
MAX_RESIDENT = 2
TIMEOUT_S = 5.0
Spawn = Callable[..., Awaitable[OwnedProcess]]
MacLauncher = Callable[..., Awaitable[LaunchedHelper]]
Owned = OwnedProcess | LaunchedHelper
BeforeDispatch = Callable[[], Awaitable[None]]


def _error(code: str) -> BusinessError:
    return BusinessError(code, "Desktop helper request did not complete", status=503)


def _decode(frame: bytes) -> dict[str, Any]:
    if not frame or len(frame) > MAX_FRAME or not frame.endswith(b"\n") or b"\n" in frame[:-1] or b"\r" in frame:
        raise _error("desktop_protocol_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise ValueError("nonfinite value")

    value = json.loads(frame, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise _error("desktop_protocol_invalid")
    return cast(dict[str, Any], value)


def helper_environment() -> dict[str, str]:
    names = (
        "SystemRoot",
        "WINDIR",
        "PATH",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "APPDATA",
    )
    result = {name: os.environ[name] for name in names if name in os.environ}
    if sys.platform == "linux":
        uid = getattr(os, "geteuid", lambda: None)()
        if uid is not None:
            result["XDG_RUNTIME_DIR"] = "/run/user/" + str(uid)
        display = os.environ.get("DISPLAY", "")
        if re.fullmatch(r"(?:unix/)?:[0-9]{1,4}(?:\.[0-9]{1,2})?", display):
            result["DISPLAY"] = display
        wayland = os.environ.get("WAYLAND_DISPLAY", "")
        if wayland not in {"", ".", ".."} and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", wayland):
            result["WAYLAND_DISPLAY"] = wayland
        if os.environ.get("XDG_SESSION_TYPE") in {"x11", "wayland"}:
            result["XDG_SESSION_TYPE"] = os.environ["XDG_SESSION_TYPE"]
        authority = os.environ.get("XAUTHORITY", "")
        if authority and len(authority) <= 4000 and "\0" not in authority and Path(authority).is_absolute():
            result["XAUTHORITY"] = authority
    # No arbitrary DBUS_SESSION_BUS_ADDRESS, instance path, credential or Python startup hook is inherited.
    if os.environ.get("OMNI_TEST_OFFLINE") == "1":
        for name in ("OMNI_TEST_OFFLINE", "OMNI_TEST_VIOLATIONS", "LITELLM_LOCAL_MODEL_COST_MAP"):
            if name in os.environ:
                result[name] = os.environ[name]
        result["PYTHONPATH"] = os.pathsep.join(
            path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path and os.path.isabs(path)
        )
    result.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    return result


@dataclass(eq=False)
class _Host:
    starting: asyncio.Task[Owned] | None = None
    owned: Owned | None = None
    readers: list[asyncio.Task[None]] = field(default_factory=lambda: list[asyncio.Task[None]]())
    collected: asyncio.Task[int] | None = None
    exited: asyncio.Task[int] | None = None
    native_key_permitted: bool = False  # retained native release owner, including paired pointer buttons
    using: bool = False
    cleanup: asyncio.Task[bool] | None = None
    output: bytearray = field(default_factory=bytearray)
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    overflow: asyncio.Event = field(default_factory=asyncio.Event)
    closing: bool = False
    released: bool = False
    capture_path: Path | None = None
    capture_owned: bool = False
    file_work: asyncio.Task[Any] | None = None
    ready_frame: dict[str, object] | None = None
    ready_seen: bool = False
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class DesktopBackend:
    def __init__(
        self,
        *,
        process_factory: Spawn | None = None,
        capture_root: Path | None = None,
        capacity: asyncio.Semaphore | None = None,
        lease_backend: LeaseClientPort | None = None,
        macos_transport: Literal["posix", "launchservices"] = "posix",
        macos_launcher: MacLauncher | None = None,
    ) -> None:
        self._factory: Spawn = process_factory or OwnedProcess.spawn
        # Production composition selects the LaunchServices transport so the
        # helper owns its TCC permissions; offline simulations keep POSIX argv.
        self._macos_transport = macos_transport
        self._macos_launcher: MacLauncher = macos_launcher or launch_helper
        self._pending: set[asyncio.Task[Any]] = set()
        self._hosts: set[_Host] = set()
        self._slots = capacity if capacity is not None else asyncio.Semaphore(MAX_RESIDENT)
        self._window_leases = (
            lease_backend
            if lease_backend is not None
            else LinuxLeaseClient(self._factory, self._slots, helper_environment)
        )
        self._closing = False
        self._capture_root = capture_root.absolute() if capture_root else None
        self._files = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-desktop-files")
        self._staging: Record | None = None

    def health(self) -> Record:
        leases = self._window_leases.health()
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "resident": len(self._hosts) + int(leases["resident"]),
            "cleanup_pending": sum(host.closing for host in self._hosts) + int(leases["cleanup_pending"]),
            "max_pending": MAX_PENDING,
            "max_resident": MAX_RESIDENT,
            "capture_staging": dict(self._staging) if self._staging else None,
            "window_leases": cast(JSON, leases),
        }

    def stop_accepting(self) -> None:
        self._closing = True
        self._window_leases.stop_accepting()

    async def probe(self, *, before_dispatch: BeforeDispatch | None = None) -> Record:
        return await self._request("probe", None, 1, before_dispatch)

    async def targets(self, limit: int, *, before_dispatch: BeforeDispatch | None = None) -> Record:
        return await self._request("targets", None, limit, before_dispatch)

    async def verify(self, target: Record, *, before_dispatch: BeforeDispatch | None = None) -> Record:
        try:
            return await self._request("verify", target, 1, before_dispatch)
        except BusinessError as caught:
            if (
                self._window_leases.is_leased(cast(dict[str, object], target))
                and caught.code == "desktop_target_changed"
            ):
                return {"matches": False, "target": None, "reason": "desktop_target_changed"}
            raise

    async def observe(self, target: Record, *, before_dispatch: BeforeDispatch | None = None) -> Record:
        if sys.platform not in {"win32", "darwin", "linux"}:
            raise _error("desktop_observation_unavailable")
        return await self._request("observe", target, 1, before_dispatch)

    async def capture(self, target: Record, *, before_dispatch: BeforeDispatch | None = None) -> CapturedFrame:
        if sys.platform not in {"win32", "darwin", "linux"} or self._capture_root is None:
            raise _error("desktop_capture_unavailable")
        return await self._request("capture", target, 1, before_dispatch)

    async def act(self, target: Record, action: Record, *, before_dispatch: BeforeDispatch) -> Record:
        return await self._write("act", target, action, before_dispatch)

    async def foreground(self, target: Record, action: Record, *, before_dispatch: BeforeDispatch) -> Record:
        return await self._write("foreground", target, action, before_dispatch)

    async def _write(self, operation: str, target: Record, action: Record, before_dispatch: BeforeDispatch) -> Record:
        sent = [False]
        try:
            if sys.platform not in {"win32", "darwin"} and not (
                sys.platform == "linux"
                and (
                    operation == "act"
                    or operation == "foreground"
                    and action.get("kind") in {"type_text", "press_key", "click"}
                )
            ):
                raise _error("desktop_action_unavailable")
            return await self._request(operation, target, 1, before_dispatch, action=action, sent=sent)
        except BusinessError as error:
            return {
                "status": "unknown" if sent[0] else "denied",
                "effect": "unknown" if sent[0] else "none",
                "error_code": error.code,
                "verification": "not_verified" if sent[0] else "not_dispatched",
            }

    def quiescent(self) -> bool:
        return not self._hosts and not self._pending and self._window_leases.quiescent()

    async def reclaim(self, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for host in list(self._hosts):
            if host.closing:
                await self._reclaim(host, max(0, deadline - asyncio.get_running_loop().time()))
        await self._window_leases.reclaim(max(0, deadline - asyncio.get_running_loop().time()))
        return self.quiescent()

    async def _request(
        self,
        operation: str,
        target: Record | None,
        limit: int,
        before_dispatch: BeforeDispatch | None,
        *,
        action: Record | None = None,
        sent: list[bool] | None = None,
    ) -> Any:
        data = {
            "version": VERSION,
            "request_id": str(uuid.uuid4()),
            "operation": operation,
            "target": target,
            "limit": limit,
        }
        if operation == "capture":
            assert self._capture_root is not None
            data["output_path"] = str(self._capture_root / str(uuid.uuid4()) / "frame.png")
        if operation in {"act", "foreground"}:
            data["action"] = action
        request = parse_request(data)
        if self._closing:
            raise _error("desktop_closing")
        if len(self._pending) >= MAX_PENDING:
            raise _error("desktop_busy")
        deadline = asyncio.get_running_loop().time() + (
            FOREGROUND_TIMEOUT_S if operation == "foreground" else TIMEOUT_S
        )

        async def perform() -> Any:
            try:
                async with asyncio.timeout_at(deadline):
                    leased = target is not None and self._window_leases.is_leased(cast(dict[str, object], target))
                    native = dict(request)
                    if leased:
                        native["target"] = await self._window_leases.guard(
                            cast(dict[str, object], target), deadline, before_dispatch
                        )
                    result = await self._run(native, deadline, before_dispatch, sent)
                    if leased:
                        await self._window_leases.guard(cast(dict[str, object], target), deadline, before_dispatch)
                        await self._dispatch_gate(before_dispatch)
                        if operation == "verify":
                            result = {**result, "target": target if result["matches"] else None}
                    elif operation == "targets" and sys.platform == "linux":
                        items = cast(list[dict[str, object]], result["items"])
                        originals = [item for item in items if item["backend"] == "x11"]
                        if originals:
                            aliases = iter(await self._window_leases.bind(originals, deadline, before_dispatch))
                            result = {
                                **result,
                                "items": [next(aliases) if item["backend"] == "x11" else item for item in items],
                            }
                            await self._dispatch_gate(before_dispatch)
                    return result
            except TimeoutError:
                raise _error("desktop_timeout") from None
            except BusinessError:
                raise
            except Exception:  # noqa: BLE001 -- guardian transport faults are bounded native metadata too
                raise _error("desktop_transport_failed") from None

        task = asyncio.create_task(perform(), name="desktop-discovery")
        self._pending.add(task)

        def finished(future: asyncio.Task[Any]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _drain(self, host: _Host, stream: asyncio.StreamReader, *, stdout: bool) -> None:
        while block := await stream.read(4096):
            if stdout:
                before = host.stdout_bytes
                host.stdout_bytes += len(block)
                if before < MAX_FRAME:
                    host.output.extend(block[: MAX_FRAME - before])
                if host.ready_frame is not None and not host.ready_seen and b"\n" in host.output:
                    host.ready_seen = True
                    end = host.output.index(b"\n") + 1
                    try:
                        control = _decode(bytes(host.output[:end]))
                    except (BusinessError, ValueError, TypeError, UnicodeError, RecursionError):
                        control = None
                    if control == host.ready_frame:
                        del host.output[:end]
                        host.ready.set()
                total = host.stdout_bytes
            else:
                host.stderr_bytes += len(block)
                total = host.stderr_bytes
            if total > MAX_FRAME or host.stdout_bytes + host.stderr_bytes > MAX_FRAME:
                host.overflow.set()
            # Raw stderr is discarded; both pipes remain drained while reclamation is unresolved.

    async def _spawn(self, host: _Host, operation: str = "probe") -> Owned:
        argv = [
            sys.executable,
            "-P" if os.environ.get("OMNI_TEST_OFFLINE") == "1" else "-I",
            "-u",
            "-m",
            "omni.adapters.workers.desktop",
        ]
        if sys.platform == "linux" and operation in {"act", "foreground"}:
            argv[-1] = "omni.adapters.workers.linux_action"
        if sys.platform == "darwin":
            executable = await self._file(host, macos_helper.resolve)
            if executable is not None and self._macos_transport == "launchservices":
                try:
                    host.owned = await self._macos_launcher(
                        executable,
                        macos_helper.bundle_path(),
                        deadline=asyncio.get_running_loop().time() + TIMEOUT_S,
                    )
                except LaunchError:
                    raise _error("desktop_native_unavailable") from None
            elif executable is not None:
                argv = [str(executable), "--ipc"]
            elif operation != "probe":
                raise _error("desktop_macos_helper_missing")
        elif sys.platform == "win32" and operation in {"observe", "capture", "act", "foreground"}:
            # Resolve the OS installation, never PATH, a caller command or a V1 script.
            import ctypes

            directory = ctypes.create_unicode_buffer(32768)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            size = kernel.GetSystemDirectoryW(directory, len(directory))
            if not 0 < size < len(directory):
                raise _error("desktop_observation_unavailable")
            executable = Path(directory.value) / "WindowsPowerShell/v1.0/powershell.exe"
            script = Path(__file__).resolve().parents[2] / "workers/windows_uia.ps1"
            if not executable.is_file() or not script.is_file() or script.is_symlink():
                raise _error("desktop_observation_unavailable")
            argv = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Mta", "-File", str(script)]
        if host.owned is None:
            host.owned = await self._factory(
                argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_FRAME + 1,
                env=helper_environment(),
                cwd=str(Path(__file__).resolve().parents[1]),
            )
        process = host.owned.process
        assert process.stdout is not None and process.stderr is not None
        for stream, stdout in ((process.stdout, True), (process.stderr, False)):
            task = asyncio.create_task(
                self._drain(host, stream, stdout=stdout), name="desktop-pipe", context=contextvars.Context()
            )
            task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
            host.readers.append(task)

        async def collected() -> int:
            await asyncio.gather(*host.readers)
            return await process.wait()

        host.collected = asyncio.create_task(collected(), name="desktop-result", context=contextvars.Context())
        host.collected.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        return host.owned

    async def _reclaim_actual(self, host: _Host, timeout_s: float) -> bool:
        if host.starting is not None:
            try:
                await asyncio.shield(host.starting)
            except Exception:  # noqa: BLE001 -- a failed spawn still retains its actual ownership record
                if host.owned is not None:
                    return False
        if host.owned is not None:
            if host.owned.process.stdin is not None:
                host.owned.process.stdin.close()
            if host.native_key_permitted:
                # An authorized XTEST press can leave a release/query owned by
                # this exact helper. Closing stdin revokes future work; killing
                # it would drop that native responsibility. Keep pipes/slot.
                if host.exited is None:
                    host.exited = asyncio.create_task(host.owned.process.wait(), name="desktop-native-release")
                    host.exited.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                done, _ = await asyncio.wait((host.exited,), timeout=max(0, timeout_s))
                if not done or host.exited.cancelled() or host.exited.exception() is not None:
                    return False
                if host.using:
                    # Exit settles native release, but the request consumer
                    # still owns its final receipt. External close must not
                    # cancel its collector or clear the bytes underneath it.
                    return False
            if not await host.owned.terminate(timeout_s):
                return False
        tasks: list[asyncio.Task[Any]] = [*host.readers, *((host.collected,) if host.collected else ())]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        host.output.clear()
        if host.file_work is not None:
            await asyncio.gather(asyncio.shield(host.file_work), return_exceptions=True)
        if host.capture_path is not None and host.capture_owned:
            root, path = self._capture_root, host.capture_path
            assert root is not None
            try:
                await self._file(host, lambda: desktop_capture.discard(root, path), cleanup=True)
            except Exception:  # noqa: BLE001 -- retain ownership and report cleanup pending
                return False
        if not host.released:
            host.released = True
            self._hosts.discard(host)
            self._slots.release()
        return True

    async def _file(self, host: _Host, operation: Callable[[], Any], *, cleanup: bool = False) -> Any:
        if host.closing and not cleanup:
            raise _error("desktop_closing")

        async def actual() -> Any:
            return await asyncio.get_running_loop().run_in_executor(
                self._files, contextvars.copy_context().run, operation
            )

        host.file_work = asyncio.create_task(actual(), name="desktop-file")
        host.file_work.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(host.file_work)

    async def _reclaim(self, host: _Host, wait_s: float) -> bool:
        host.closing = True
        if host.cleanup is None or (
            host.cleanup.done()
            and (host.cleanup.cancelled() or host.cleanup.exception() is not None or not host.cleanup.result())
        ):
            host.cleanup = asyncio.create_task(
                self._reclaim_actual(host, 1), name="desktop-reclaim", context=contextvars.Context()
            )
            host.cleanup.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        done, _ = await asyncio.wait((host.cleanup,), timeout=max(0, wait_s))
        return bool(
            done and not host.cleanup.cancelled() and host.cleanup.exception() is None and host.cleanup.result()
        )

    async def _dispatch_gate(self, before_dispatch: BeforeDispatch | None) -> None:
        if self._closing:
            raise _error("desktop_closing")
        if before_dispatch is not None:
            try:
                await before_dispatch()
            except BusinessError as error:
                raise BusinessError(
                    error.code, "Desktop observation authorization changed before dispatch", status=error.status
                ) from None
        if self._closing:
            raise _error("desktop_closing")

    async def _run(
        self,
        request: dict[str, Any],
        deadline: float,
        before_dispatch: BeforeDispatch | None,
        sent: list[bool] | None = None,
    ) -> Any:
        host: _Host | None = None
        overflow: asyncio.Task[bool] | None = None
        armed: asyncio.Task[bool] | None = None
        failed = False
        capture_root = self._capture_root
        linux_action = request["operation"] == "act" and request["target"]["platform"] == "linux"
        two_phase = request["operation"] == "foreground" or linux_action
        try:
            async with asyncio.timeout_at(deadline):
                await self._slots.acquire()
                if self._closing:
                    self._slots.release()
                    raise _error("desktop_closing")
                host = _Host(using=True)
                if two_phase:
                    host.ready_frame = linux_ready(request) if linux_action else ready(request)
                self._hosts.add(host)
                await self._dispatch_gate(before_dispatch)
                if capture_root is not None and request["operation"] in {"probe", "capture"}:
                    known = {
                        item.capture_path.parent.name
                        for item in self._hosts
                        if item.capture_path and item.capture_owned
                    }
                    try:
                        self._staging = await self._file(host, lambda: desktop_capture.inventory(capture_root, known))
                    except (OSError, BusinessError):
                        self._staging = {
                            "residual_count": None,
                            "truncated": False,
                            "error_code": "desktop_staging_unavailable",
                        }
                        if request["operation"] == "capture":
                            raise _error("desktop_staging_unavailable") from None
                    await self._dispatch_gate(before_dispatch)
                if request["operation"] == "capture":
                    assert capture_root is not None
                    capture_path = Path(request["output_path"])
                    host.capture_path = capture_path

                    def prepare_capture() -> None:
                        desktop_capture.prepare(capture_root, capture_path.parent.name)
                        host.capture_owned = True

                    await self._file(host, prepare_capture)
                    await self._dispatch_gate(before_dispatch)
                host.starting = asyncio.create_task(
                    self._spawn(host, request["operation"]), name="desktop-spawn", context=contextvars.Context()
                )
                host.starting.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                await asyncio.shield(host.starting)
                assert host.owned is not None and host.owned.process.stdin is not None and host.collected is not None
                wire_request = request
                if request["operation"] in {"observe", "capture", "act", "foreground"}:
                    wire_request = {**request, "target": encode_native_target(request["target"])}
                frame = (
                    json.dumps(wire_request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    + b"\n"
                )
                if len(frame) > MAX_FRAME:
                    raise _error("desktop_request_too_large")
                await self._dispatch_gate(before_dispatch)
                if sent is not None and not two_phase:
                    sent[0] = True
                host.owned.process.stdin.write(frame)
                await host.owned.process.stdin.drain()
                overflow = asyncio.create_task(host.overflow.wait(), context=contextvars.Context())
                if two_phase:
                    armed = asyncio.create_task(host.ready.wait(), context=contextvars.Context())
                    await asyncio.wait((armed, host.collected, overflow), return_when=asyncio.FIRST_COMPLETED)
                    if host.overflow.is_set():
                        raise _error("desktop_output_limit")
                    if host.ready.is_set():
                        # Native has proven the manually focused target. Authority is checked
                        # again after that wait; no input is allowed by the initial frame.
                        await self._dispatch_gate(before_dispatch)
                        permission = linux_ready(request, permitted=True) if linux_action else permit(request)
                        grant = json.dumps(permission, separators=(",", ":")).encode("utf-8") + b"\n"
                        if sent is not None:
                            sent[0] = True
                        if (
                            request["operation"] == "foreground"
                            and request["target"]["platform"] == "linux"
                            and request["action"]["kind"] in {"press_key", "click"}
                        ):
                            host.native_key_permitted = True
                        host.owned.process.stdin.write(grant)
                        await host.owned.process.stdin.drain()
                host.owned.process.stdin.close()
                done, _ = await asyncio.wait((host.collected, overflow), return_when=asyncio.FIRST_COMPLETED)
                if host.overflow.is_set():
                    raise _error("desktop_output_limit")
                if host.collected not in done or host.collected.result() != 0:
                    raise _error("desktop_helper_failed")
                try:
                    response = parse_response(_decode(bytes(host.output)), request)
                except (BusinessError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
                    raise _error("desktop_protocol_invalid") from None
                if not response["ok"]:
                    reported = str(response["error_code"])
                    recognized = reported in LINUX_ERRORS | {
                        "desktop_native_unavailable",
                        "desktop_response_too_large",
                        "desktop_observation_unavailable",
                        "desktop_target_changed",
                        "desktop_permission_denied",
                        "desktop_capture_unavailable",
                        "desktop_capture_protected",
                        "desktop_capture_too_large",
                        "desktop_capture_failed",
                    }
                    raise _error(reported if recognized else "desktop_helper_error")
                result = cast(Record, response["result"])
                if two_phase and sent is not None and not sent[0] and result["effect"] != "none":
                    raise _error("desktop_protocol_invalid")
                if request["operation"] == "capture":
                    result_path = host.capture_path
                    assert capture_root is not None and result_path is not None
                    return await self._file(host, lambda: desktop_capture.read(capture_root, result_path, result))
                if (
                    request["operation"] == "probe"
                    and self._staging
                    and self._staging["error_code"] == "desktop_staging_unavailable"
                ):
                    for capability in cast(list[Record], result["capabilities"]):
                        if capability["id"] == "screen_capture":
                            capability.update(
                                cast(
                                    Record,
                                    {
                                        "available": False,
                                        "error_code": "desktop_staging_unavailable",
                                        "recovery": ["截图暂存区无法核验；请检查实例目录权限后重新检查能力。"],
                                    },
                                )
                            )
                return result
        except TimeoutError:
            failed = True
            raise _error("desktop_timeout") from None
        except BusinessError:
            failed = True
            raise
        except asyncio.CancelledError:
            failed = True
            raise
        except Exception:  # noqa: BLE001 -- helper faults are never serialized as raw native diagnostics
            failed = True
            raise _error("desktop_transport_failed") from None
        finally:
            if host is not None:
                host.using = False
            if armed is not None:
                armed.cancel()
                await asyncio.gather(armed, return_exceptions=True)
            if overflow is not None:
                overflow.cancel()
                await asyncio.gather(overflow, return_exceptions=True)
            if (
                host is not None
                and not await self._reclaim(host, max(0, deadline - asyncio.get_running_loop().time()))
                and not failed
            ):
                raise _error("desktop_cleanup_pending")

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for host in list(self._hosts):
            await self._reclaim(host, max(0, deadline - asyncio.get_running_loop().time()))
        leases_closed = await self._window_leases.close(max(0, deadline - asyncio.get_running_loop().time()))
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        complete = leases_closed and not self._hosts and not any(not task.done() for task in self._pending)
        if complete:
            self._files.shutdown(wait=False)
        return complete
