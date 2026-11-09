"""Owner configuration use cases; import only previews and never starts an adapter."""

from __future__ import annotations

from typing import cast

from ..domain.configuration import config_name, config_value
from ..domain.contracts import BusinessError
from ..domain.images import POLICY_DEFAULT, policy
from .ports import DatabasePort, Record, TokenCounter


class ConfigurationService:
    def __init__(self, database: DatabasePort, counter: TokenCounter) -> None:
        self._db, self._counter = database, counter

    async def page(self, agent_id: str, kind: str, after: str = "", limit: int = 100) -> Record:
        rows = await self._db.read(lambda u: u.configuration_page(agent_id, kind, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def get(self, agent_id: str, identity: str) -> Record:
        def read(u):
            row = u.configuration_get(agent_id, identity)
            if row["kind"] == "model":
                row["image_policy"] = u.model_image_policy(agent_id, str(row["revision_id"]))
            return row

        return await self._db.read(read)

    async def create(
        self, agent_id: str, kind: str, name: str, enabled: bool, value: dict, image_policy: dict | None = None
    ) -> Record:
        def create(u):
            row = u.configuration_create(agent_id, kind, name, enabled, value)
            if image_policy is not None:
                if kind != "model":
                    raise BusinessError(
                        "image_policy_invalid", "Only model configurations accept image policies", status=422
                    )
                row["image_policy"] = u.model_image_policy_add(agent_id, str(row["revision_id"]), image_policy)
            return row

        return await self._db.write(create)

    async def save(
        self,
        agent_id: str,
        identity: str,
        name: str,
        enabled: bool,
        value: dict,
        revision: int,
        image_policy: dict | None = None,
    ) -> Record:
        def save(u):
            current = u.configuration_get(agent_id, identity)
            prior = u.model_image_policy(agent_id, str(current["revision_id"])) if current["kind"] == "model" else None
            if prior is None and image_policy is not None:
                raise BusinessError(
                    "image_policy_invalid", "Only model configurations accept image policies", status=422
                )
            row = u.configuration_put(agent_id, identity, name, enabled, value, revision)
            if prior is not None:
                checked = image_policy if image_policy is not None else {k: prior[k] for k in POLICY_DEFAULT}
                row["image_policy"] = u.model_image_policy_add(agent_id, str(row["revision_id"]), checked)
            return row

        return await self._db.write(save)

    async def archive(self, agent_id: str, identity: str, revision: int) -> None:
        await self._db.write(lambda u: u.configuration_archive(agent_id, identity, revision))

    async def history(self, agent_id: str, identity: str, after: int = 0, limit: int = 50) -> Record:
        def read(u):
            rows = u.configuration_history(agent_id, identity, after, limit)
            for row in rows:
                if row["kind"] == "model":
                    row["image_policy"] = u.model_image_policy(agent_id, str(row["id"]))
            return rows

        rows = await self._db.read(read)
        return {"items": list(rows), "next_after": rows[-1]["revision"] if len(rows) == limit else None}

    async def settings_history(self, agent_id: str, after: int = 0, limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.settings_history(agent_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["revision"] if len(rows) == limit else None}

    async def preset(self, agent_id: str, identity: str) -> Record:
        row = await self.get(agent_id, identity)
        if row["kind"] != "preset" or not row["enabled"]:
            raise BusinessError("preset_unavailable", "Select an enabled preset")
        return {"revision_id": row["revision_id"], "value": row["value"]}

    @staticmethod
    def template(kind: str) -> Record:
        return {
            "kind": kind,
            "name": "",
            "enabled": False,
            "value": config_value(kind, {}),
            **({"image_policy": dict(POLICY_DEFAULT)} if kind == "model" else {}),
        }

    @staticmethod
    def preview(document: dict) -> Record:
        if (
            set(document) - {"image_policy"} != {"schema", "kind", "name", "enabled", "value"}
            or type(document["schema"]) is not int
            or document["schema"] != 1
            or type(document["enabled"]) is not bool
        ):
            raise BusinessError(
                "invalid_configuration_import", "Configuration import requires schema 1 and known fields", status=422
            )
        name, _ = config_name(document["name"])
        result = {
            "schema": 1,
            "kind": document["kind"],
            "name": name,
            "enabled": document["enabled"],
            "value": config_value(document["kind"], document["value"]),
        }
        if "image_policy" in document:
            if document["kind"] != "model":
                raise BusinessError("image_policy_invalid", "Only model imports accept image policies", status=422)
            result["image_policy"] = policy(document["image_policy"], result["value"]["model"])
        return result

    async def diagnose(self, agent_id: str, identity: str) -> Record:
        row = await self.get(agent_id, identity)
        value = row["value"]
        if not isinstance(value, dict):
            raise BusinessError("invalid_configuration", "Invalid business configuration")
        reference = value.get("credential_ref", "")
        if reference:
            try:
                await self._db.read(lambda u: u.credential_authorize(str(reference)))
            except BusinessError as exc:
                return {
                    "available": False,
                    "state": exc.code,
                    "configuration_enabled": row["enabled"],
                    "credential_authorization_only": True,
                }
        if row["kind"] != "model":
            return {
                "available": False,
                "state": "adapter_not_connected" if row["kind"] != "preset" else "stored_preset",
                "configuration_enabled": row["enabled"],
            }
        try:
            available = self._counter.supports(
                str(value["counter_mode"]), str(value["counter_ref"]), str(value["model"])
            )
        except Exception:  # noqa: BLE001 -- an unavailable injected counter is a capability diagnosis
            available = False
        return {
            "available": bool(available and row["enabled"] and value["model"]),
            "image_protocol": cast(dict, row["image_policy"])["protocol"],
            "image_state": "image_capability_disabled"
            if cast(dict, row["image_policy"])["protocol"] == "disabled"
            else "image_counter_unavailable"
            if value["counter_mode"] != "utf8_upper_bound" or value["counter_ref"] != "utf8-envelope-v1"
            else "configured_pending_real_acceptance",
            "state": "counter_unavailable"
            if not available
            else "model_unconfigured"
            if not value["model"]
            else "counter_available",
            "capacity_source": "owner_declared",
            "counting_method": value["counter_mode"],
            "configuration_enabled": row["enabled"],
        }
