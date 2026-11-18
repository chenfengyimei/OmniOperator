"""Owner-side pinned root broker connection; no elevation, root code launch or automatic request retry."""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import struct
import sys
import uuid
from pathlib import Path
from typing import Any, cast

from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    MAX_FRAME,
    MAX_REQUESTS,
    admin_operation,
    admin_scope,
    bound_operation,
    decode,
    encode,
    request,
    scoped_operations,
    socket_path,
    validate_response,
    verify_broker,
)
from omni.domain.service_deployment import sha
from omni.domain.service_exchange import FORMAT as EXCHANGE_FORMAT
from omni.domain.service_exchange import PHASE_FORMAT


def unavailable(code: str) -> Record:
    return {
        "available": False,
        "installed": None,
        "definition_present": None,
        "definition_matches": None,
        "enabled": None,
        "state": "unknown",
        "pid": None,
        "error_code": code,
    }


class PrivilegedServiceClient:
    def __init__(
        self, operation: Record, endpoint: str, broker_sha256: str, *, _approval: Record | None = None
    ) -> None:
        self.operation: dict[str, Any] = cast(dict[str, Any], admin_operation(operation))
        self.approval: Record = self.operation if _approval is None else cast(Record, admin_scope(_approval))
        if self.operation not in scoped_operations(self.approval).values():
            raise BusinessError(
                "service_admin_scope_changed", "The connection principal must belong to its approved scope"
            )
        self.endpoint, self.broker_sha256 = Path(endpoint), sha(broker_sha256)
        verify_broker(self.approval, self.broker_sha256)
        if str(self.endpoint) != str(Path(socket_path(self.approval))):
            raise BusinessError(
                "service_admin_endpoint_invalid", "Use this operation's fixed local administrator socket"
            )
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: asyncio.Task[Record] | None = None
        self._sequence = 0
        self._failed = self._closing = self._closed = False
        self._close_request: asyncio.Task[Record] | None = None

    def _check_endpoint(self) -> tuple[int, int]:
        owner = int(self.operation["deployment"]["request"]["principal"]["id"])
        platform = "darwin" if self.operation["deployment"]["request"]["target"] == "macos-system" else "linux"
        if (
            sys.platform != platform
            or getattr(os, "getuid", lambda: -1)() != owner
            or getattr(os, "geteuid", lambda: -1)() != owner
        ):
            raise BusinessError("service_admin_owner_required", "Only the approved ordinary owner may use the broker")
        for path in self.endpoint.parents:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise BusinessError(
                    "service_admin_endpoint_invalid", "The administrator socket directory is not root controlled"
                )
        info = self.endpoint.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner or info.st_mode & 0o077:
            raise BusinessError(
                "service_admin_endpoint_invalid", "The administrator socket is not private to this owner"
            )
        if platform == "darwin":
            from omni.adapters.platform.services.darwin_admin_files import protected

            protected(self.endpoint, owner=owner)
        return info.st_dev, info.st_ino

    async def _connect(self) -> None:
        if sys.platform != "linux" and sys.platform != "darwin":
            raise BusinessError("service_admin_owner_required", "The scoped broker requires its native Unix platform")
        identity = self._check_endpoint()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_connect(connection, str(self.endpoint))
            if sys.platform == "darwin":
                from omni.adapters.platform.services.darwin_admin_peer import peer_identity

                pid, uid, _ = peer_identity(connection)
            else:
                pid, uid, _ = struct.unpack(
                    "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                )
            if pid <= 0 or uid != 0 or self._check_endpoint() != identity:
                raise BusinessError(
                    "service_admin_peer_invalid", "The connected administrator identity is not verified root"
                )
            self._reader, self._writer = await asyncio.open_unix_connection(sock=connection, limit=MAX_FRAME + 1)
        except BaseException:
            connection.close()
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    @closed.setter
    def closed(self, value: bool) -> None:
        self._closed = value

    @property
    def pending(self) -> asyncio.Task[Record] | None:
        return self._pending

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def sequence(self) -> int:
        return self._sequence

    async def rpc(self, command: str, *, operation_key: str = "") -> Record:
        """Public bridge: one owned request through the pinned broker connection."""
        return await self._rpc(command, operation_key=operation_key)

    async def exchange(self, command: str, *, operation_key: str = "") -> Record:
        """Public bridge: leases settle through the same single owned connection."""
        return await self._exchange(command, operation_key=operation_key)

    async def _exchange(self, command: str, *, operation_key: str = "") -> Record:
        if self._writer is None:
            await self._connect()
        assert self._reader is not None and self._writer is not None
        if self._sequence >= MAX_REQUESTS or (self._sequence == MAX_REQUESTS - 1 and command != "close"):
            raise BusinessError("service_admin_request_limit", "Administrator request capacity is exhausted")
        value = request(
            self.approval, command, self._sequence, str(uuid.uuid4()), self.broker_sha256, operation_key=operation_key
        )
        self._sequence += 1
        try:
            self._writer.write(encode(value))
            await self._writer.drain()
            payload = await self._reader.readuntil(b"\n")
            return cast(Record, validate_response(decode(payload), value))
        except BaseException:
            self._failed = True
            raise

    async def _rpc(self, command: str, *, operation_key: str = "") -> Record:
        if self._closing or self._failed or self._pending is not None:
            raise BusinessError(
                "service_admin_unavailable", "The administrator request is already owned or unavailable"
            )
        task = asyncio.create_task(
            self._exchange(command, operation_key=operation_key), name="service-administrator-rpc"
        )
        self._pending = task

        def finished(actual: asyncio.Task[Record]) -> None:
            if not actual.cancelled():
                actual.exception()
            if self._pending is actual:
                self._pending = None

        task.add_done_callback(finished)
        # A timeout releases only the waiter. Actual native work, response reader
        # and close obligation remain owned until the broker settles or disconnects.
        done, _ = await asyncio.wait((task,), timeout=60)
        if not done:
            self._closing = True
            raise BusinessError("service_admin_timeout", "The actual administrator request remains owned")
        return await asyncio.shield(task)

    def _plan(self, plan: Record) -> None:
        if plan != self.operation["deployment"]:
            raise BusinessError("service_admin_plan_changed", "The broker cannot service another deployment")

    async def inspect(self, plan: Record) -> Record:
        self._plan(plan)
        try:
            result = await self._rpc("inspect")
            return {**result, "admin_broker_sha256": self.broker_sha256}
        except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            return unavailable(error.code if isinstance(error, BusinessError) else "service_admin_unavailable")

    async def change(self, plan: Record, action: str) -> Record:
        self._plan(plan)
        if action not in {"install", "uninstall"} or action != self.operation["action"]:
            raise BusinessError("service_admin_action_changed", "Only the approved administrator action is available")
        try:
            return await self._rpc("change")
        except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            return {
                "effect": "unknown",
                "error_code": error.code if isinstance(error, BusinessError) else "service_admin_unavailable",
            }

    async def start(self, plan: Record) -> Record:
        self._plan(plan)
        if self.operation["action"] != "start":
            raise BusinessError("service_admin_action_changed", "Only the approved administrator start is available")
        try:
            return await self._rpc("change")
        except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            return {
                "effect": "unknown",
                "error_code": error.code if isinstance(error, BusinessError) else "service_admin_unavailable",
            }

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._pending is not None:
            done, _ = await asyncio.wait((self._pending,), timeout=max(0, timeout_s))
            if not done:
                return False
        if self._failed and self._writer is not None:
            # A broken/untrusted reply is not an administrator cleanup receipt.
            # Half-close to request cancellation, while retaining the transport
            # and owner instance lock. Do not claim root work has been reclaimed.
            try:
                if self._writer.can_write_eof():
                    self._writer.write_eof()
            except OSError:
                pass
            return False
        if self._writer is not None and not self._failed:
            if self._close_request is None or (
                self._close_request.done()
                and not self._close_request.cancelled()
                and self._close_request.exception() is None
                and self._close_request.result().get("closed") is not True
            ):
                self._close_request = asyncio.create_task(self._exchange("close"), name="service-administrator-close")
                self._close_request.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            done, _ = await asyncio.wait(
                (self._close_request,), timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if not done:
                return False
            try:
                report = self._close_request.result()
            except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                self._failed = True
                return False
            else:
                if report.get("closed") is not True or report.get("cleanup_pending") is not False:
                    return False
        if self._writer is not None:
            self._writer.close()
            try:
                async with asyncio.timeout(max(0.001, deadline - asyncio.get_running_loop().time())):
                    await self._writer.wait_closed()
            except (OSError, TimeoutError):
                return False
            self._writer = None
        self._closed = True
        return True


class ExchangeAdministrator(PrivilegedServiceClient):
    """The exchange owns the one root connection; child ports own only their settled native operation."""

    def __init__(self, plan: Record, endpoint: str, broker_sha256: str) -> None:
        scope = admin_scope(plan)
        if scope.get("format") not in {EXCHANGE_FORMAT, PHASE_FORMAT}:
            raise BusinessError("service_admin_exchange_required", "Bind the complete reviewed system exchange")
        super().__init__(
            cast(Record, next(iter(scoped_operations(scope).values()))),
            endpoint,
            broker_sha256,
            _approval=cast(Record, scope),
        )
        self._holder: AdministratorLease | None = None
        self._leases: list[AdministratorLease] = []

    @property
    def holder(self) -> AdministratorLease | None:
        return self._holder

    @holder.setter
    def holder(self, value: AdministratorLease | None) -> None:
        self._holder = value

    def lease(self, key: str, *, read_only: bool = False) -> AdministratorLease:
        bound_operation(self.approval, key)
        if self._closing or self._closed or len(self._leases) >= 8:
            raise BusinessError(
                "service_admin_lease_limit", "Administrator leases are bounded and cannot reopen a closed session"
            )
        result = AdministratorLease(self, key, read_only)
        self._leases.append(result)
        return result

    async def probe(self) -> bool:
        # A resumed exchange may already have replaced the one native label.
        # Darwin rejects an old loaded definition rather than returning its
        # contents. Try only the other explicitly approved definition, after
        # the first query's actual cleanup. Neither query dispatches a change.
        for key in scoped_operations(self.approval):
            if key == "start_target":
                continue
            lease = self.lease(key, read_only=True)
            report = await lease.inspect(cast(Record, bound_operation(self.approval, key)["deployment"]))
            if not await lease.close(6):
                return False
            if report.get("available") is True:
                return True
        return False

    async def close(self, timeout_s: float = 30) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        holder = self._holder
        if holder is not None and holder.settle is not None and not holder.settle.done():
            done, _ = await asyncio.wait((holder.settle,), timeout=max(0, timeout_s))
            if not done:
                return False
        closed = await super().close(max(0, deadline - asyncio.get_running_loop().time()))
        if closed:
            for lease in self._leases:
                lease.closed = True
            self._holder = None
        return closed


class AdministratorLease:
    def __init__(self, parent: ExchangeAdministrator, key: str, read_only: bool) -> None:
        self.parent, self.key, self.read_only = parent, key, read_only
        self.operation = bound_operation(parent.approval, key)
        self._closed = False
        self._settle: asyncio.Task[Record] | None = None

    @property
    def settle(self) -> asyncio.Task[Record] | None:
        return self._settle

    @property
    def closed(self) -> bool:
        return self._closed

    @closed.setter
    def closed(self, value: bool) -> None:
        self._closed = value

    def _plan(self, deployment: Record) -> None:
        if deployment != self.operation["deployment"]:
            raise BusinessError("service_admin_plan_changed", "The lease binds only its reviewed deployment")

    async def _call(self, command: str) -> Record:
        if self._closed or self.parent.closed or self._settle is not None:
            raise BusinessError("service_admin_lease_closed", "This native operation lease has been released")
        if self.parent.holder not in (None, self):
            raise BusinessError(
                "service_admin_lease_owned", "Another native operation still owns administrator cleanup"
            )
        self.parent.holder = self
        return await self.parent.rpc(command, operation_key=self.key)

    async def inspect(self, plan: Record) -> Record:
        self._plan(plan)
        try:
            report = await self._call("inspect")
            return {
                **report,
                "admin_broker_sha256": self.parent.broker_sha256,
                "admin_scope_sha256": self.parent.approval["sha256"],
                "admin_operation_key": self.key,
            }
        except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            return unavailable(exc.code if isinstance(exc, BusinessError) else "service_admin_unavailable")

    async def _change(self, deployment: Record, action: str) -> Record:
        self._plan(deployment)
        if self.read_only or action != self.operation["action"]:
            raise BusinessError(
                "service_admin_action_changed", "The lease cannot perform another action or write through a query port"
            )
        try:
            return await self._call("change")
        except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            return {
                "effect": "unknown",
                "error_code": exc.code if isinstance(exc, BusinessError) else "service_admin_unavailable",
            }

    async def change(self, plan: Record, action: str) -> Record:
        if action not in {"install", "uninstall"}:
            raise BusinessError("service_admin_action_changed", "Choose only the bound registration action")
        return await self._change(plan, action)

    async def start(self, plan: Record) -> Record:
        return await self._change(plan, "start")

    async def close(self, timeout_s: float = 30) -> bool:
        if self._closed or self.parent.closed:
            self._closed = True
            return True
        if self.parent.holder is not self:
            self._closed = True  # An unused lease has no RPC or native work of its own.
            return True
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self.parent.pending is not None:
            done, _ = await asyncio.wait((self.parent.pending,), timeout=max(0, timeout_s))
            if not done:
                return False
        if self.parent.failed:
            return False
        if self.parent.sequence >= MAX_REQUESTS - 1:
            return await self.parent.close(max(0, deadline - asyncio.get_running_loop().time()))
        if self._settle is None:
            self._settle = asyncio.create_task(
                self.parent.exchange("settle", operation_key=self.key), name="service-administrator-settle"
            )
            self._settle.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        done, _ = await asyncio.wait((self._settle,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if not done:
            return False
        try:
            report = self._settle.result()
        except (BusinessError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return False
        if report.get("settled") is not True or report.get("cleanup_pending") is not False:
            return False
        self._closed, self.parent.holder = True, None
        return True
