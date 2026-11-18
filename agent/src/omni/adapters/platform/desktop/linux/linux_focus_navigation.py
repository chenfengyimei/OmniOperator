"""Bounded complete AT-SPI focus readback in one native-bound window; never predicts tab order or sends input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from omni.adapters.platform.desktop.linux.linux_atspi import (
    ACCESSIBLE,
    MAX_DEPTH,
    MAX_NODES,
    MAX_TEXT,
    AtspiDesktop,
    reference,
)
from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_semantic import Semantics, digest
from omni.adapters.platform.desktop.linux.linux_window_binding import WindowBinding

Payload = dict[str, object]
Ref = tuple[str, str]
FOCUSED = 12
INTERACTIVE = frozenset({8, 11, 24, 25, 30})


@dataclass(frozen=True)
class FocusNode:
    ref: Ref
    path: tuple[int, ...]
    identity: str
    flags: frozenset[int]


@dataclass(frozen=True)
class FocusTree:
    nodes: tuple[FocusNode, ...]
    focused: Ref | None


class FocusNavigation:
    def __init__(self, desktop: AtspiDesktop, binding: WindowBinding) -> None:
        self.desktop, self.binding = desktop, binding
        self.semantics = Semantics(desktop)
        self._target: Payload | None = None
        self._before: FocusTree | None = None
        self._attempted = self._failed = False

    async def _read(self) -> FocusTree:
        assert self._target is not None
        root = await self.desktop.verified(self._target)
        nodes: list[FocusNode] = []
        seen: set[Ref] = set()
        focused: list[Ref] = []
        remaining = MAX_TEXT

        async def visit(ref: Ref, parent: Ref | None, path: tuple[int, ...]) -> None:
            nonlocal remaining
            if ref[0] != root[0] or ref in seen or len(nodes) >= MAX_NODES:
                raise failure("desktop_observation_changed")
            seen.add(ref)
            if (
                parent is not None
                and reference(await self.desktop.property(ref, ACCESSIBLE, "Parent", "(so)")) != parent
            ):
                raise failure("desktop_observation_changed")
            # facts rejects password roles BEFORE reading their name, text or
            # descendants. An incomplete/private tree cannot prove unique focus.
            facts = await self.semantics.facts(ref)
            flags = frozenset(cast(set[int], facts["flags"]))
            if parent is None and 1 not in flags:
                raise failure("desktop_input_unverified")
            if FOCUSED in flags:
                if parent is None or not INTERACTIVE <= flags or focused:
                    raise failure("desktop_input_unverified")
                focused.append(ref)
            interfaces = cast(list[str], facts["interfaces"])
            strings = [cast(str, facts["name"]), cast(str, facts["text"]), *interfaces]
            remaining -= sum(len(item.encode("utf-8")) for item in strings)
            if remaining < 0 or len(set(interfaces)) != len(interfaces):
                raise failure("desktop_observation_changed")
            nodes.append(FocusNode(ref, path, digest({"role": facts["role"], "strings": strings}), flags - {FOCUSED}))
            count = await self.desktop.children_count(ref)
            if count > MAX_NODES - len(nodes) or (count and len(path) + 1 >= MAX_DEPTH):
                raise failure("desktop_observation_changed")
            for index in range(count):
                await visit(await self.desktop.child(ref, index), ref, (*path, index))

        await visit(root, None, ())
        if await self.desktop.verified(self._target) != root:
            raise failure("desktop_target_changed")
        return FocusTree(tuple(nodes), focused[0] if focused else None)

    async def _stable(self) -> FocusTree:
        if self._failed or self._target is None:
            raise failure("desktop_input_unverified")
        try:
            await self.binding.check()
            before, after = await self._read(), await self._read()
            await self.binding.check()
            if before != after:
                raise failure("desktop_observation_changed")
            return after
        except BaseException:
            self._failed = True
            raise

    async def prepare(self, target: Payload, path: list[int], ref: Ref) -> None:
        if self._attempted:
            raise failure("desktop_action_revoked")
        self._attempted = True
        self._target = dict(target)
        before = await self._stable()
        if before.focused != ref or not any(node.ref == ref and node.path == tuple(path) for node in before.nodes):
            self._failed = True
            raise failure("desktop_observation_changed")
        self._before = before

    async def check(self) -> None:
        if self._before is None or await self._stable() != self._before:
            self._failed = True
            raise failure("desktop_observation_changed")

    async def changed(self) -> bool:
        if self._before is None:
            raise failure("desktop_input_unverified")
        after = await self._stable()
        # Same complete tree, process, native recipient and every other state.
        # Only one old focus disappearing and one new focus appearing counts.
        # No destination order, popup closure or cross-window success is guessed.
        if after.nodes != self._before.nodes:
            self._failed = True
            raise failure("desktop_observation_changed")
        return after.focused is not None and after.focused != self._before.focused


class KeyState(FocusNavigation):
    """Verify one advertised state flip on the same focused node and complete window tree."""

    async def prepare(self, target: Payload, path: list[int], ref: Ref) -> None:
        await super().prepare(target, path, ref)
        assert self._before is not None
        node = next(item for item in self._before.nodes if item.ref == ref)
        if not {41, 22, 9} & node.flags:
            self._failed = True
            raise failure("desktop_input_unverified")

    async def changed(self) -> bool:
        if self._before is None:
            raise failure("desktop_input_unverified")
        after = await self._stable()
        if after.focused != self._before.focused or len(after.nodes) != len(self._before.nodes):
            self._failed = True
            raise failure("desktop_observation_changed")
        changed = False
        for before_node, after_node in zip(self._before.nodes, after.nodes, strict=True):
            if before_node.ref != self._before.focused:
                valid = before_node == after_node
            else:
                allowed = {bit for capability, bit in ((41, 4), (22, 23), (9, 10)) if capability in before_node.flags}
                delta = before_node.flags ^ after_node.flags
                valid = (
                    before_node.ref == after_node.ref
                    and before_node.path == after_node.path
                    and before_node.identity == after_node.identity
                    and (not delta or len(delta) == 1 and delta <= allowed)
                )
                changed = bool(delta)
            if not valid:
                self._failed = True
                raise failure("desktop_observation_changed")
        return changed
