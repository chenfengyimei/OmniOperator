"""Trusted extension installation, scoped catalogs and explicit authorization; no plugin imports or process creation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeVar, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome, ToolSpec
from ..domain.extensions import (
    MAX_ARTIFACT_BYTES,
    artifact_descriptors,
    artifact_summary,
    parse_authorization,
    parse_install,
    request_fingerprint,
    tool_artifacts,
    tool_spec,
)
from .objects import ObjectService
from .ports import DatabasePort, ExtensionHostsPort, ExtensionPackagesPort, Record

T = TypeVar("T")


class ExtensionService:
    def __init__(
        self, database: DatabasePort, objects: ObjectService, packages: ExtensionPackagesPort, hosts: ExtensionHostsPort
    ) -> None:
        self._db, self._objects, self._packages, self._hosts = database, objects, packages, hosts
        self._pending: set[asyncio.Task] = set()
        self._invocations: dict[asyncio.Task, tuple[str, str, int]] = {}
        self._errors: dict[tuple[str, str], str] = {}
        self._closing = False

    def _remember_error(self, record: Record, code: str) -> None:
        key = str(record["agent_id"]), str(record["extension_id"])
        if key not in self._errors and len(self._errors) >= 256:
            self._errors.pop(next(iter(self._errors)))
        self._errors[key] = code

    def _public(self, record: Record) -> Record:
        result = {
            key: value
            for key, value in record.items()
            if key not in {"object_id", "cancel_operations", "retired_authority_epoch"}
        }
        result["runtime_error_code"] = self._errors.get((str(record["agent_id"]), str(record["extension_id"])), "")
        result["runtime"] = self._hosts.health()
        result["workers"] = self._hosts.health(str(record["agent_id"]), str(record["extension_id"]))["workers"]
        result["trust_model"] = "trusted_python_not_os_sandbox"
        result["requires_action_approval"] = True
        return result

    async def _execute(self, operation: Callable[[], Awaitable[T]], *, cancel_operation: bool = False) -> T:
        if self._closing:
            raise BusinessError("extensions_closing", "Extensions are draining", status=503)
        if len(self._pending) >= 64:
            raise BusinessError("extension_busy", "Extension operations are bounded", retryable=True, status=503)

        async def perform():
            return await operation()

        task = asyncio.create_task(perform(), name="extension-operation")
        self._pending.add(task)

        def complete(future):
            self._pending.discard(future)
            self._invocations.pop(future, None)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancel_operation and not task.done() and not task.cancelling():
                task.cancel()
            raise

    async def _retire(self, record: Record) -> None:
        epoch = cast(int, record.get("retired_authority_epoch", 0))
        if epoch <= 0:
            return
        agent, identity = str(record["agent_id"]), str(record["extension_id"])
        current = asyncio.current_task()
        for task, (owner, extension, authority) in list(self._invocations.items()):
            if (
                task is not current
                and (owner, extension) == (agent, identity)
                and authority <= epoch
                and not task.done()
                and not task.cancelling()
            ):
                task.cancel()
        await self._hosts.revoke(agent, identity, epoch)

    async def _prepare(self, record: Record) -> str:
        agent = str(record["agent_id"])
        _, payload = await self._objects.get(agent, str(record["object_id"]))
        return await self._packages.prepare(agent, record, payload)

    def _artifact_directory(self, agent_id: str, extension_id: str) -> Path | None:
        """A fresh per-extension directory; the child can only write here, and only this call reads it."""
        raw = self._hosts.artifact_path(agent_id, extension_id)
        if not raw:
            return None
        path = Path(raw)
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @staticmethod
    def _cleanup_artifacts(directory: Path | None) -> None:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    async def _publish_artifacts(
        self, record: Record, result: ActionResult, directory: Path | None
    ) -> tuple[tuple[str, ...], list[JSON]]:
        """Revalidate child-reported bytes/hashes, then publish immutable objects for this action."""
        if directory is None:
            raise ValueError
        descriptors = artifact_descriptors(
            result.data.get("artifacts"), tool_artifacts(record["manifest"], str(record["local_name"]))
        )
        root = directory.resolve()
        identities: list[str] = []
        published: list[JSON] = []
        for descriptor in descriptors:
            path = (root / descriptor["name"]).resolve()
            if path.parent != root or path.is_symlink() or not path.is_file():
                raise ValueError
            content = path.read_bytes()
            if len(content) != descriptor["bytes"] or len(content) > MAX_ARTIFACT_BYTES:
                raise ValueError
            if hashlib.sha256(content).hexdigest() != descriptor["sha256"]:
                raise ValueError
            obj = await self._objects.publish(str(record["agent_id"]), content, descriptor["media_type"])
            identities.append(str(obj["id"]))
            published.append(
                cast(JSON, {"id": str(obj["id"]), "media_type": descriptor["media_type"], "bytes": descriptor["bytes"]})
            )
        return tuple(identities), published

    async def _drift(self, record: Record, code: str = "extension_snapshot_drift") -> None:
        changed = await self._db.write(
            lambda u: u.extension_mark_drift(
                str(record["agent_id"]), str(record["extension_id"]), str(record["extension_revision_id"]), code
            )
        )
        self._remember_error(record, code)
        await self._retire(changed)

    async def install(self, agent_id: str, request: Record) -> Record:
        normalized, digest = parse_install(request), request_fingerprint(request)

        async def perform():
            prior = await self._db.read(lambda u: u.extension_install_replay(agent_id, normalized["key"], digest))
            if prior:
                return self._public(prior)
            captured = await self._packages.capture(normalized["source"])
            obj = await self._objects.publish(agent_id, captured.content, "application/vnd.omni.extension+zip")
            metadata: Record = {
                "manifest": captured.manifest,
                "files": list(captured.files),
                "package_sha256": captured.package_sha256,
                "catalog_sha256": captured.catalog_sha256,
            }
            result = await self._db.write(lambda u: u.extension_install(agent_id, normalized, metadata, str(obj["id"])))
            await self._retire(result)
            return self._public(result)

        return await self._execute(perform)

    async def get(self, agent_id: str, extension_id: str) -> Record:
        async def perform():
            return self._public(await self._db.read(lambda u: u.extension_get(agent_id, extension_id)))

        return await self._execute(perform)

    async def page(self, agent_id: str, after: int = 0, limit: int = 20) -> Record:
        async def perform():
            records = await self._db.read(lambda u: u.extension_page(agent_id, after, limit))
            return {
                "items": [self._public(row) for row in records],
                "next_after": records[-1]["sequence"] if len(records) == limit else None,
            }

        return await self._execute(perform)

    async def actions(self, agent_id: str, extension_id: str, after: str = "", limit: int = 20) -> Record:
        """Bounded owner view of what this plugin actually returned; no arguments or free text."""

        async def perform():
            records = await self._db.read(lambda u: u.extension_action_page(agent_id, extension_id, after, limit))
            items = []
            for record in records:
                raw_result = record.get("result")
                result = raw_result if isinstance(raw_result, dict) else {}
                raw_data = result.get("data")
                data = raw_data if isinstance(raw_data, dict) else {}
                raw_artifacts = data.get("artifacts")
                artifacts = raw_artifacts if isinstance(raw_artifacts, list) else []
                items.append(
                    {
                        "id": record["id"],
                        "tool": record["tool"],
                        "state": record["state"],
                        "effect": record["effect"],
                        "ended_at": record["ended_at"],
                        "artifacts": [
                            {
                                "id": str(item["id"]),
                                "media_type": str(item["media_type"]),
                                "bytes": int(cast(int, item["bytes"])),
                            }
                            for item in artifacts[:4]
                            if isinstance(item, dict)
                            and isinstance(item.get("id"), str)
                            and isinstance(item.get("media_type"), str)
                            and type(item.get("bytes")) is int
                        ],
                    }
                )
            return {"items": items, "next_after": items[-1]["id"] if len(items) == limit else None}

        return await self._execute(perform)

    async def authorize(self, agent_id: str, extension_id: str, request: Record) -> Record:
        checked = parse_authorization(request)

        async def perform():
            record = await self._db.read(lambda u: u.extension_get(agent_id, extension_id))
            if record["revision"] != checked["revision"]:
                raise BusinessError("revision_conflict", "Plugin changed; inspect its current version")
            try:
                await self._prepare(record)
            except BusinessError as exc:
                if exc.code == "extension_snapshot_drift":
                    await self._drift(record)
                raise
            result = await self._db.write(lambda u: u.extension_authorize(agent_id, extension_id, checked))
            await self._retire(result)
            self._errors.pop((agent_id, extension_id), None)
            return self._public(result)

        return await self._execute(perform)

    async def disable(self, agent_id: str, extension_id: str, revision: int) -> Record:
        async def perform():
            result = await self._db.write(lambda u: u.extension_disable(agent_id, extension_id, revision))
            if not result.get("retired_authority_epoch"):
                # Explicitly retry resource cleanup for an already disabled slot;
                # no new authorization, revision or audit is created by this retry.
                result = {**result, "retired_authority_epoch": max(0, cast(int, result["authority_epoch"]) - 1)}
            await self._retire(result)
            return self._public(result)

        return await self._execute(perform)

    async def tools_for_run(self, agent_id: str, run_id: str) -> Sequence[ToolSpec]:
        async def perform():
            records = await self._db.read(lambda u: u.extension_run_tools(agent_id, run_id))
            usable: set[str] = set()
            checked: dict[str, bool] = {}
            for record in records:
                version = str(record["extension_revision_id"])
                if version not in checked:
                    try:
                        await self._prepare(record)
                        checked[version] = True
                    except BusinessError as exc:
                        checked[version] = False
                        self._remember_error(record, exc.code)
                        if exc.code == "extension_snapshot_drift":
                            await self._drift(record)
                if checked[version]:
                    usable.add(str(record["binding_id"]))
            # File I/O may have overlapped revocation; never substitute a later grant.
            latest = await self._db.read(lambda u: u.extension_run_tools(agent_id, run_id))
            return tuple(tool_spec(record) for record in latest if str(record["binding_id"]) in usable)

        return await self._execute(perform)

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        async def perform():
            try:
                encoded = json.dumps(
                    dict(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
                if len(encoded) > 262144:
                    raise ValueError("Plugin arguments exceed the JSON payload limit")
                frozen = json.loads(encoded)
                digest = hashlib.sha256(encoded).hexdigest()
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return ActionResult(
                    Outcome.DENIED, "Plugin arguments are not a bounded JSON value", error_code="invalid_tool_arguments"
                )
            record = None
            try:
                record = await self._db.read(
                    lambda u: u.extension_dispatch(context.agent_id, context.run_id, operation_id, name)
                )
                if record["arguments_sha256"] != digest:
                    raise BusinessError(
                        "extension_arguments_changed", "Plugin arguments differ from the approved intent"
                    )
                task = asyncio.current_task()
                assert task is not None
                self._invocations[task] = (
                    context.agent_id,
                    str(record["extension_id"]),
                    cast(int, record["authority_epoch"]),
                )
                location = await self._prepare(record)
            except BusinessError as exc:
                if record is not None:
                    self._remember_error(record, exc.code)
                    if exc.code == "extension_snapshot_drift":
                        await self._drift(record)
                return ActionResult(
                    Outcome.DENIED, "Plugin is unavailable or its authorization changed", error_code=exc.code
                )

            preflight_error = ""

            async def before_dispatch():
                nonlocal preflight_error
                try:
                    await self._packages.verify(location, record)
                    latest = await self._db.read(
                        lambda u: u.extension_dispatch(context.agent_id, context.run_id, operation_id, name)
                    )
                    if latest["arguments_sha256"] != digest:
                        raise BusinessError(
                            "extension_arguments_changed", "Plugin arguments differ from approved intent"
                        )
                except BusinessError as exc:
                    preflight_error = exc.code
                    raise

            artifact_directory: Path | None = None
            try:
                artifact_directory = self._artifact_directory(context.agent_id, str(record["extension_id"]))
                try:
                    result = await self._hosts.invoke(
                        record,
                        location,
                        name,
                        frozen,
                        operation_id=operation_id,
                        context=context,
                        before_dispatch=before_dispatch,
                    )
                except BusinessError as exc:
                    return ActionResult(
                        Outcome.ERROR, "No extension host is available", error_code=exc.code, retryable=exc.retryable
                    )
                if preflight_error:
                    self._remember_error(record, preflight_error)
                    if preflight_error == "extension_snapshot_drift":
                        await self._drift(record)
                    return ActionResult(
                        Outcome.DENIED,
                        "Plugin dispatch was rejected before code execution",
                        error_code=preflight_error,
                    )
                if result.error_code == "extension_snapshot_drift":
                    await self._drift(record)
                if result.artifacts:
                    return ActionResult(
                        Outcome.UNKNOWN,
                        "Plugin returned unsupported artifact references",
                        Effect.UNKNOWN,
                        error_code="extension_artifact_protocol",
                    )
                if result.data.get("artifacts"):
                    try:
                        identities, published = await self._publish_artifacts(record, result, artifact_directory)
                    except (ValueError, OSError, KeyError, TypeError, BusinessError):
                        return ActionResult(
                            Outcome.UNKNOWN,
                            "Plugin artifacts could not be verified and published; verify the external result",
                            Effect.UNKNOWN,
                            error_code="extension_artifact_invalid",
                        )
                    summary = artifact_summary(result.data.get("artifacts"))
                    text = (result.text + "\n" + summary)[:12000] if summary else result.text
                    return ActionResult(
                        result.status,
                        text,
                        result.effect,
                        error_code=result.error_code,
                        retryable=result.retryable,
                        artifacts=identities,
                        verification=result.verification,
                        data={
                            **{key: value for key, value in result.data.items() if key != "artifacts"},
                            "artifacts": published,
                        },
                    )
                return result
            finally:
                self._cleanup_artifacts(artifact_directory)

        return await self._execute(perform, cancel_operation=True)

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "unavailable": len(self._errors),
            "hosts": self._hosts.health(),
        }

    def review_quiescent(self, agent_id: str, extension_id: str, authority_epoch: int) -> bool:
        if any(
            not task.done() and owner == agent_id and extension == extension_id and epoch <= authority_epoch
            for task, (owner, extension, epoch) in self._invocations.items()
        ):
            return False
        workers = self._hosts.health(agent_id, extension_id).get("workers", [])
        return isinstance(workers, list) and all(
            isinstance(worker, dict)
            and (
                cast(int, worker["authority_epoch"]) > authority_epoch
                or not (worker.get("busy") or worker.get("starting") or worker.get("cleanup_pending"))
            )
            for worker in workers
        )

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        for task in list(self._invocations):
            if not task.done() and not task.cancelling():
                task.cancel()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True
