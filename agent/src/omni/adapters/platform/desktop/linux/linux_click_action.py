"""One approved component-bound native move/click with concrete application state readback; no replay or focus acquisition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_node_point import NodePoint
from omni.adapters.platform.desktop.linux.linux_semantic import Semantics, digest, result, stable_catalog
from omni.adapters.platform.desktop.linux.linux_text import WAIT_SECONDS
from omni.adapters.platform.desktop.linux.linux_window_binding import WindowBinding
from omni.domain.contracts import BusinessError
from omni.domain.foreground import action as foreground_action

Payload = dict[str, object]


def expected_state(context: Payload) -> tuple[str, bool] | None:
    state = cast(dict[str, bool], context["state"])
    for bit in ("4", "23", "10", "12"):
        if bit in state and (bit not in {"23", "12"} or not state[bit]):
            return bit, not state[bit]
    return None


class FocusedClick:
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

    async def click(self, target: Payload, value: Payload, permit: Callable[[], Awaitable[bool]]) -> Payload:
        sent = False
        try:
            if self.attempted:
                raise failure("desktop_action_revoked")
            self.attempted = True
            action = foreground_action(value)
            proof = cast(Payload, action["node"])
            if action["kind"] != "click" or proof.get("backend") != "atspi":
                raise failure("desktop_input_unverified")
            path = cast(list[int], proof["path"])
            deadline = self.now() + WAIT_SECONDS
            while True:
                ref, _parent, _facts, catalog, actual = await self.semantics.resolve(target, path)
                if actual != proof or "click" not in catalog:
                    raise failure("desktop_observation_changed")
                root = await self.desktop.verified(target)
                _role, flags = await self.desktop.role_state(root)
                if 1 in flags:
                    break
                if self.now() >= deadline:
                    raise failure("desktop_input_unverified")
                await self.sleep(min(0.1, deadline - self.now()))
            await self.binding.prepare(target)
            focus = self.binding.focus
            if focus is None:
                raise failure("desktop_input_unverified")
            context = cast(Payload, catalog["click"])
            point = NodePoint(self.desktop, self.binding, target, ref, context)
            await focus.prepare_pointer(await point.prepare())
            await focus.prepare_sources()
            await focus.prepare_button()
            await focus.prepare_pointer_actions()
            await focus.prepare_pointer_configuration()
            await focus.prepare_motion()
            await focus.motion_packet()  # all native preparation before ready; no movement yet
            if not await permit():
                raise failure("desktop_action_revoked")
            await point.check()
            current = await self.semantics.resolve(target, path)
            if current[0] != ref or current[-1] != proof:
                raise failure("desktop_observation_changed")
            motion = await focus.motion_packet()
            sent = True
            await motion.emit()
            await point.check()
            current = await self.semantics.resolve(target, path)
            if current[0] != ref or current[-1] != proof:
                raise failure("desktop_observation_changed")
            packet = await focus.button_packet()
            await packet.emit()
            expected = expected_state(context)
            stop = self.now() + 0.75
            while True:
                await point.check()
                after = await self.semantics.resolve(target, path)
                pointer = cast(Payload, after[3].get("click") or {})
                if (
                    after[0] != ref
                    or stable_catalog(catalog) != stable_catalog(after[3])
                    or after[-1] != {**proof, "actions_sha256": digest(after[3])}
                ):
                    raise failure("desktop_observation_changed")
                if expected is not None and cast(Payload, pointer.get("state") or {}).get(expected[0]) is expected[1]:
                    await point.check()
                    return result("ok", verification="click_verified")
                if expected is None or self.now() >= stop:
                    raise failure("desktop_action_unverified")
                await self.sleep(0.05)
        except BusinessError as error:
            return result(
                "unknown" if sent else "denied",
                error.code if error.code.startswith("desktop_") else "desktop_action_unverified",
            )
        except Exception:  # noqa: BLE001 -- cursor/button input may have executed; never replay or export provider text
            return result("unknown" if sent else "denied", "desktop_action_unverified")
