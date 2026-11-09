"""Addressed AT-SPI semantic proof and single mutation; no global input, focus acquisition or retry."""

from __future__ import annotations

import hashlib
import json

from ...domain.contracts import BusinessError
from ...domain.desktop_actions import node_proof, semantic
from .linux_atspi import ACCESSIBLE, PASSWORD_ROLE, TEXT, AtspiDesktop, reference
from .linux_bus import RemoteError, failure
from .linux_session import number

ACTION, EDITABLE, SELECTION = ("org.a11y.atspi." + item for item in ("Action", "EditableText", "Selection"))
SAFE_ACTIONS = {"click", "press", "activate"}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def result(status: str, code="", verification="not_verified") -> dict:
    return {
        "status": status,
        "effect": {"ok": "applied", "denied": "none", "unknown": "unknown"}[status],
        "error_code": code,
        "verification": "not_dispatched" if status == "denied" else verification,
    }


class Semantics:
    def __init__(self, desktop: AtspiDesktop) -> None:
        self.desktop = desktop

    async def interfaces(self, ref) -> list[str]:
        values = await self.desktop.call(ref, ACCESSIBLE, "GetInterfaces", returns="as")
        if (
            not isinstance(values, list)
            or len(values) > 64
            or any(not isinstance(v, str) or len(v) > 255 for v in values)
        ):
            raise failure("desktop_bus_response_invalid")
        return values

    async def facts(self, ref) -> dict:
        role, flags = await self.desktop.role_state(ref)
        if role == PASSWORD_ROLE:
            raise failure("desktop_password_control")
        name = await self.desktop.property(ref, ACCESSIBLE, "Name", "s")
        if not isinstance(name, str) or len(name) > 512:
            raise failure("desktop_observation_changed")
        interfaces = await self.interfaces(ref)
        text = ""
        if TEXT in interfaces:
            count = number(await self.desktop.property(ref, TEXT, "CharacterCount", "i"), 2147483647)
            if count > 512:
                raise failure("desktop_observation_changed")
            text = await self.desktop.call(ref, TEXT, "GetText", "ii", [0, count], returns="s")
            if not isinstance(text, str) or len(text) != count:
                raise failure("desktop_bus_response_invalid")
        return {"role": role, "flags": flags, "name": name, "text": text, "interfaces": interfaces}

    async def catalogue(self, ref, parent, facts) -> dict:
        entries = {}
        if not {8, 24, 25, 30} <= facts["flags"]:
            return entries
        if EDITABLE in facts["interfaces"] and TEXT in facts["interfaces"] and 7 in facts["flags"]:
            entries["set_text"] = True
        if ACTION in facts["interfaces"]:
            count = number(await self.desktop.property(ref, ACTION, "NActions", "i"), 16)
            names = [await self.desktop.call(ref, ACTION, "GetName", "i", [i], returns="s") for i in range(count)]
            if any(not isinstance(n, str) or len(n) > 80 for n in names):
                raise failure("desktop_bus_response_invalid")
            eligible = [i for i, name in enumerate(names) if name in SAFE_ACTIONS]
            if len(eligible) == 1:
                entries["invoke"] = {"index": eligible[0], "names": names}
        if parent is not None and 22 in facts["flags"]:
            parent_role, parent_flags = await self.desktop.role_state(parent)
            if (
                parent_role != PASSWORD_ROLE
                and 18 not in parent_flags
                and {8, 24, 25, 30} <= parent_flags
                and SELECTION in await self.interfaces(parent)
            ):
                entries["select"] = True
        return entries

    @staticmethod
    def make_proof(path, trail, facts, catalog) -> dict:
        return node_proof(
            {
                "backend": "atspi",
                "path": path,
                "ancestry_sha256": digest(trail),
                "role": "atspi-role-" + str(facts["role"]),
                "name_sha256": digest(facts["name"]),
                "value_sha256": digest(facts["text"]),
                "actions_sha256": digest(catalog),
            }
        )

    async def observed(self, ref, parent, path, trail, facts) -> tuple[dict | None, list[str]]:
        try:
            catalog = await self.catalogue(ref, parent, facts)
            return (self.make_proof(path, trail, facts, catalog), list(catalog)) if catalog else (None, [])
        except (BusinessError, RemoteError):
            return None, []

    async def resolve(self, target: dict, path: list[int]) -> tuple:
        ref = await self.desktop.verified(target)
        trail, parent = [], None
        for position in range(len(path) + 1):
            facts = await self.facts(ref)
            trail.append((list(ref), facts["role"], digest(facts["name"])))
            if position == len(path):
                catalog = await self.catalogue(ref, parent, facts)
                proof = self.make_proof(path, trail, facts, catalog)
                return ref, parent, facts, catalog, proof
            index = path[position]
            if index >= await self.desktop.children_count(ref):
                raise failure("desktop_observation_changed")
            child = await self.desktop.child(ref, index)
            if child[0] != ref[0] or reference(await self.desktop.property(child, ACCESSIBLE, "Parent", "(so)")) != ref:
                raise failure("desktop_observation_changed")
            parent, ref = ref, child
        raise failure("desktop_observation_changed")

    async def proof(self, target: dict, path: list[int]) -> tuple[dict | None, list[str]]:
        try:
            _, _, _, catalog, proof = await self.resolve(target, path)
            return (proof, list(catalog)) if catalog else (None, [])
        except (BusinessError, RemoteError):
            return None, []  # the normal bounded read remains available without write proof

    async def act(self, target: dict, action: dict, monitor, permit) -> dict:
        action = semantic(action)
        sent = False
        try:
            if action["node"].get("backend") != "atspi":
                raise failure("desktop_observation_changed")
            ref, parent, _facts, catalog, proof = await self.resolve(target, action["node"]["path"])
            if proof != action["node"] or action["kind"] not in catalog:
                raise failure("desktop_observation_changed")
            await monitor.arm()
            if not await permit():
                raise failure("desktop_action_revoked")
            # Approval waiting is outside every native write. Re-read the full
            # proof and user-input barrier after the parent grants dispatch.
            actual = await self.resolve(target, proof["path"])
            if actual[-1] != proof:
                raise failure("desktop_observation_changed")
            await monitor.check()
            kind = action["kind"]
            sent = True  # the RPC may execute even if its reply is lost
            if kind == "set_text":
                applied = await self.desktop.call(ref, EDITABLE, "SetTextContents", "s", [action["text"]], returns="b")
            elif kind == "invoke":
                applied = await self.desktop.call(ref, ACTION, "DoAction", "i", [catalog[kind]["index"]], returns="b")
            else:
                applied = await self.desktop.call(
                    parent, SELECTION, "SelectChild", "i", [proof["path"][-1]], returns="b"
                )
            await monitor.check()
            if type(applied) is not bool or not applied:
                raise failure("desktop_action_unverified")
            current = await self.resolve(target, proof["path"])
            expected = {**proof, "value_sha256": digest(action["text"])} if kind == "set_text" else proof
            if current[-1] != expected:
                raise failure("desktop_observation_changed")
            if kind == "set_text" and current[2]["text"] == action["text"]:
                outcome = result("ok", verification="value_matches")
            elif kind == "select":
                selected = await self.desktop.call(
                    parent, SELECTION, "IsChildSelected", "i", [proof["path"][-1]], returns="b"
                )
                outcome = (
                    result("ok", verification="selected")
                    if selected is True
                    else result("unknown", "desktop_action_unverified")
                )
            else:
                outcome = result("unknown", "desktop_action_unverified")
            await monitor.check()
            return outcome
        except BusinessError as error:
            return result(
                "unknown" if sent else "denied",
                error.code if error.code.startswith("desktop_") else "desktop_action_unverified",
            )
        except Exception:  # noqa: BLE001 -- native calls may have executed; never export provider text or repeat an RPC
            return result("unknown" if sent else "denied", "desktop_action_unverified")
