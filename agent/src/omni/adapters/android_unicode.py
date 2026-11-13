"""Owned Unicode/IME transaction over managed workers; secrets stay in private IPC."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import cast

from ..application.ports import AsyncSecretPort, Record
from ..domain.contracts import ActionResult, BusinessError, Effect, InvocationContext, Outcome
from .workers.client import WorkerClient


class UnicodeFlow:
    def __init__(self, factory: Callable[[], WorkerClient], secrets: AsyncSecretPort | None) -> None:
        self.factory, self.secrets = factory, secrets

    async def run(
        self,
        client: WorkerClient,
        args: Record,
        context: InvocationContext,
        guard: Callable[[], Awaitable[None]],
        environment: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ActionResult:
        snapshot: Record | None = None
        uncertain = False
        selection_started = False
        result = ActionResult(
            Outcome.DENIED, "Unicode input was not dispatched", error_code="android_unicode_unavailable"
        )
        restored = False

        async def invoke(method: str, values: Record, check: Callable[[], Awaitable[None]] = guard) -> ActionResult:
            return await client.invoke(
                method, values, operation_id=str(uuid.uuid4()), context=context, before_dispatch=check
            )

        try:
            async with asyncio.timeout(22):
                # Re-read the original UI before changing any input-method setting.
                result = await invoke("android_ready", args)
                if result.status == Outcome.OK:
                    if self.secrets is None:
                        raise BusinessError("android_unicode_unavailable", "Credential backend is unavailable")
                    await guard()
                    unicode_profile = cast(Record, args["unicode_profile"])
                    profile = cast(Record, unicode_profile["profile"])
                    key = await self.secrets.get(cast(str, profile["credential_ref"]))
                    await guard()
                    if key is None:
                        raise BusinessError("android_pairing_key_missing", "Store the helper pairing key first")
                    prepared = await invoke("android_unicode_prepare", args)
                    result = prepared
                    if prepared.status == Outcome.OK:
                        snapshot = dict(prepared.data)
                        # This commit must succeed before the first IME selection write.
                        await environment("planned", cast(dict[str, object], snapshot))
                        values = {**args, "prepare_nonce": snapshot["prepare_nonce"]}
                        selection_started = True
                        result = await invoke("android_unicode_begin", values)
                        uncertain = result.status in {Outcome.ERROR, Outcome.UNKNOWN}
                        if result.status == Outcome.OK:
                            result = await invoke("android_unicode_ready", {**values, "pairing_key": key})
                            uncertain = result.status in {Outcome.ERROR, Outcome.UNKNOWN}
                            if result.status == Outcome.OK:
                                result = await invoke("android_unicode_commit", values)
                                uncertain = result.status in {Outcome.ERROR, Outcome.UNKNOWN}
                        key = None
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- private IPC failures cannot expose pairing keys
            # Timeouts/cancellation during selection or commit require observation, never replay.
            uncertain = selection_started
            result = ActionResult(
                Outcome.UNKNOWN if selection_started else Outcome.DENIED,
                "Unicode/IME outcome requires review" if selection_started else "Unicode input was not dispatched",
                Effect.UNKNOWN if selection_started else Effect.NONE,
                error_code=error.code if isinstance(error, BusinessError) else "android_unicode_interrupted",
            )
        finally:
            if snapshot is not None:
                observer = client
                extra: WorkerClient | None = None
                try:
                    if uncertain:
                        # An uncertain command may still be running. Reclaim it before a read-only observation.
                        while not await client.close(1):
                            await asyncio.sleep(0.1)
                        extra = observer = self.factory()
                    async with asyncio.timeout(8):
                        cleanup = await observer.invoke(
                            "android_unicode_environment"
                            if uncertain or not selection_started
                            else "android_unicode_restore",
                            cast(
                                Record,
                                {"connection": args["connection"], "device": args["device"], "environment": snapshot},
                            ),
                            operation_id=str(uuid.uuid4()),
                            context=context,
                        )
                    if cleanup.data:
                        await environment("observed", dict(cleanup.data))
                        restored = cleanup.data.get("result_code") == "restored"
                    else:
                        await environment("observed", {"result_code": "cleanup_observation_unknown"})
                except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- persist uncertainty before propagating storage errors
                    restored = False
                    await environment("observed", {"result_code": "cleanup_observation_unknown"})
                finally:
                    if extra is not None:
                        while not await extra.close(1):
                            await asyncio.sleep(0.1)
        if snapshot is not None and not restored:
            return ActionResult(
                Outcome.UNKNOWN,
                "Unicode input and IME restoration require owner review",
                Effect.UNKNOWN,
                error_code="android_ime_cleanup_pending",
                data={"input_status": result.status.value, "ime_restored": False},
            )
        return ActionResult(
            result.status,
            result.text,
            result.effect,
            error_code=result.error_code,
            verification=result.verification,
            data={"ime_restored": restored} if snapshot else {},
        )

    async def recover(
        self,
        client: WorkerClient,
        args: Record,
        context: InvocationContext,
        guard: Callable[[], Awaitable[None]],
        environment: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ActionResult:
        try:
            async with asyncio.timeout(22):
                result = await client.invoke(
                    "android_unicode_restore",
                    args,
                    operation_id=str(uuid.uuid4()),
                    context=context,
                    before_dispatch=guard,
                )
            if result.data:
                await environment("observed", dict(result.data))
            else:
                await environment("observed", {"result_code": "recovery_observation_unknown"})
            return result
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- native failure text may contain private editor data
            await environment("observed", {"result_code": "recovery_observation_unknown"})
            return ActionResult(
                Outcome.UNKNOWN,
                "IME recovery outcome requires review",
                Effect.UNKNOWN,
                error_code="android_ime_cleanup_pending",
            )
