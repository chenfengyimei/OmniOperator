"""One addressed Unicode append in a manually focused AT-SPI editor; no key synthesis, clipboard, focus acquisition or retry."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from omni.domain.contracts import BusinessError
from omni.domain.foreground import action as foreground_action
from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_input import X11Input
from omni.adapters.platform.desktop.linux.linux_semantic import EDITABLE, Semantics, digest, result, stable_catalog

Payload = dict[str, object]
WAIT_SECONDS = 10.0


class FocusedText:
    def __init__(
        self,
        desktop: AtspiDesktop,
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.desktop = desktop
        self.semantics = Semantics(desktop)
        self.now = now if now is not None else asyncio.get_running_loop().time
        self.sleep = sleep

    async def _focused(self, target: Payload, facts: Payload) -> bool:
        root = await self.desktop.verified(target)
        _role, flags = await self.desktop.role_state(root)
        return 1 in flags and 12 in cast(set[int], facts["flags"])

    async def append(
        self, target: Payload, value: Payload, monitor: X11Input, permit: Callable[[], Awaitable[bool]]
    ) -> Payload:
        sent = False
        try:
            action = foreground_action(value)
            proof = cast(Payload, action["node"])
            if action["kind"] != "type_text" or proof.get("backend") != "atspi":
                raise failure("desktop_input_unverified")
            path, text = cast(list[int], proof["path"]), cast(str, action["text"])
            deadline = self.now() + WAIT_SECONDS
            while True:
                ref, _parent, facts, catalog, actual = await self.semantics.resolve(target, path)
                if actual != proof or "type_text" not in catalog or len(cast(str, facts["text"])) + len(text) > 512:
                    raise failure("desktop_observation_changed")
                if await self._focused(target, facts):
                    break
                if self.now() >= deadline:
                    raise failure("desktop_input_unverified")
                await self.sleep(min(0.1, deadline - self.now()))
            await monitor.arm()
            if not await permit():
                raise failure("desktop_action_revoked")
            current = await self.semantics.resolve(target, path)
            if current[0] != ref or current[-1] != proof or not await self._focused(target, current[2]):
                raise failure("desktop_observation_changed")
            await monitor.check()
            before = cast(str, current[2]["text"])
            # One addressed RPC is the entire mutation. D-Bus length is bytes,
            # while its insertion position is a Unicode character offset.
            sent = True
            applied = await self.desktop.call(
                ref, EDITABLE, "InsertText", "isi", [len(before), text, len(text.encode("utf-8"))], returns="b"
            )
            await monitor.check()
            after = await self.semantics.resolve(target, path)
            context = await self.semantics.typing_context(ref, after[2])
            expected = before + text
            expected_catalog = stable_catalog(catalog)
            actual_catalog = stable_catalog(after[3])
            # Providers can retain the original caret or advance it after
            # InsertText. Full prefix/suffix content and control identity are
            # verified independently; no SetCaretOffset write is added.
            if (
                applied is not True
                or after[0] != ref
                or after[2]["text"] != expected
                or actual_catalog != expected_catalog
                or context is None
                or context["caret"] not in {len(before), len(expected)}
                or after[-1] != {**proof, "value_sha256": digest(expected), "actions_sha256": digest(after[3])}
                or not await self._focused(target, after[2])
            ):
                raise failure("desktop_action_unverified")
            await monitor.check()
            return result("ok", verification="text_appended")
        except BusinessError as error:
            return result(
                "unknown" if sent else "denied",
                error.code if error.code.startswith("desktop_") else "desktop_action_unverified",
            )
        except Exception:  # noqa: BLE001 -- a lost append receipt cannot authorize another insertion
            return result("unknown" if sent else "denied", "desktop_action_unverified")
