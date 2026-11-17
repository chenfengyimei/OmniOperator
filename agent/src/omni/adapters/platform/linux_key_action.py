"""One approved native key pair for an explicitly bound focused semantic node; no focus acquisition or replay."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from ...domain.contracts import BusinessError
from ...domain.foreground import action as foreground_action
from .linux_atspi import AtspiDesktop
from .linux_bus import failure
from .linux_semantic import Semantics, digest, result, stable_catalog
from .linux_text import WAIT_SECONDS
from .linux_window_binding import WindowBinding

Payload = dict[str, object]


def expected_edit(text: str, context: Payload | None, key: str) -> tuple[str, int] | None:
    """Prove only a concrete changed text/caret result; transport or an unchanged state is not success."""
    if context is None:
        return None
    caret = cast(int, context["caret"])
    start = text.rfind("\n", 0, caret) + 1
    end = text.find("\n", caret)
    end = len(text) if end < 0 else end
    updated, position = text, caret
    if key == "Left":
        position = max(0, caret - 1)
    elif key == "Right":
        position = min(len(text), caret + 1)
    elif key == "Home":
        position = start
    elif key == "End":
        position = end
    elif key == "Up" and start:
        previous = text.rfind("\n", 0, start - 1) + 1
        position = min(previous + caret - start, start - 1)
    elif key == "Down" and end < len(text):
        following = text.find("\n", end + 1)
        position = min(end + 1 + caret - start, len(text) if following < 0 else following)
    elif key == "Backspace" and caret:
        updated, position = text[: caret - 1] + text[caret:], caret - 1
    elif key == "Delete" and caret < len(text):
        updated = text[:caret] + text[caret + 1 :]
    elif key == "Enter" and len(text) < 512:
        updated, position = text[:caret] + "\n" + text[caret:], caret + 1
    # Wrapping, grapheme handling, submission, focus navigation and Escape can
    # have different application behavior. A mismatch remains unknown, never a
    # new guessed action or a second press.
    return (updated, position) if (updated, position) != (text, caret) else None


class FocusedKey:
    def __init__(
        self,
        desktop: AtspiDesktop,
        binding: WindowBinding,
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.desktop, self.binding = desktop, binding
        self.semantics = Semantics(desktop)
        self.now = now if now is not None else asyncio.get_running_loop().time
        self.sleep = sleep
        self.attempted = False

    async def _focused(self, target: Payload, facts: Payload) -> bool:
        root = await self.desktop.verified(target)
        _role, flags = await self.desktop.role_state(root)
        return 1 in flags and 12 in cast(set[int], facts["flags"])

    async def press(self, target: Payload, value: Payload, permit: Callable[[], Awaitable[bool]]) -> Payload:
        sent = False
        try:
            if self.attempted:
                raise failure("desktop_action_revoked")
            self.attempted = True
            action = foreground_action(value)
            proof = cast(Payload, action["node"])
            if action["kind"] != "press_key" or proof.get("backend") != "atspi":
                raise failure("desktop_input_unverified")
            path, key = cast(list[int], proof["path"]), cast(str, action["text"])
            deadline = self.now() + WAIT_SECONDS
            while True:
                ref, _parent, facts, catalog, actual = await self.semantics.resolve(target, path)
                if actual != proof or "press_key" not in catalog:
                    raise failure("desktop_observation_changed")
                if await self._focused(target, facts):
                    break
                if self.now() >= deadline:
                    raise failure("desktop_input_unverified")
                await self.sleep(min(0.1, deadline - self.now()))
            await self.binding.prepare(target)
            focus = self.binding.focus
            if focus is None:
                raise failure("desktop_input_unverified")
            await focus.prepare_key(key)
            await focus.prepare_sources()
            await focus.key_packet()  # all copied configuration proved BEFORE ready
            if not await permit():
                raise failure("desktop_action_revoked")
            await self.binding.check()
            packet = await focus.key_packet()  # new current request sequence after permit/readbacks
            current = await self.semantics.resolve(target, path)
            if current[0] != ref or current[-1] != proof or not await self._focused(target, current[2]):
                raise failure("desktop_observation_changed")
            keyboard = cast(Payload, catalog["press_key"])
            expected = expected_edit(cast(str, facts["text"]), cast(Payload | None, keyboard["text"]), key)
            sent = True
            await packet.emit()
            stop = self.now() + 0.75
            while True:
                await self.binding.check()
                after = await self.semantics.resolve(target, path)
                if after[0] != ref or not await self._focused(target, after[2]):
                    raise failure("desktop_action_unverified")
                static_before = stable_catalog(catalog)
                static_after = stable_catalog(after[3])
                post_key = cast(Payload, after[3].get("press_key") or {})
                post_context = cast(Payload | None, post_key.get("text"))
                if (
                    static_before != static_after
                    or post_key.get("window") != keyboard["window"]
                    or after[-1]
                    != {**proof, "value_sha256": digest(after[2]["text"]), "actions_sha256": digest(after[3])}
                ):
                    raise failure("desktop_observation_changed")
                if (
                    expected is not None
                    and post_context is not None
                    and (after[2]["text"], post_context["caret"]) == expected
                ):
                    await self.binding.check()
                    return result("ok", verification="key_verified")
                if expected is None or self.now() >= stop:
                    raise failure("desktop_action_unverified")
                await self.sleep(0.05)
        except BusinessError as error:
            return result(
                "unknown" if sent else "denied",
                error.code if error.code.startswith("desktop_") else "desktop_action_unverified",
            )
        except Exception:  # noqa: BLE001 -- uncertain input never causes another press or exports provider text
            return result("unknown" if sent else "denied", "desktop_action_unverified")
