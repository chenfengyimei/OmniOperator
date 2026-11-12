"""Typed configuration revisions and Agent preferences; no connections, DDL or credentials."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from ...domain.configuration import config_name, config_value, fingerprint
from ...domain.contracts import BusinessError
from ...domain.credentials import reference_fingerprint
from ...domain.settings import KEYS, validate_settings
from . import schema as s
from .credential_repository import CredentialRepositories
from .qq_authority import qq_run, qq_run_authorized

OPTION_TABLES = {
    "model": s.model_options,
    "channel": s.channel_options,
    "mcp": s.mcp_options,
    "preset": s.preset_options,
}


class ConfigurationRepositories(CredentialRepositories):
    def _configuration_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("agent_not_found", "Agent does not exist", status=404)

    def _configuration_revision(self, agent_id: str, revision_id: str) -> dict:
        row = self._one(s.configuration_revisions, agent_id, revision_id)
        table = OPTION_TABLES[row["kind"]]
        value = (
            self.connection.execute(
                select(table).where(table.c.revision_id == revision_id, table.c.agent_id == agent_id)
            )
            .mappings()
            .one()
        )
        return {
            **row,
            "revision_id": row["id"],
            "value": {k: v for k, v in value.items() if k not in {"revision_id", "agent_id", "kind"}},
        }

    def configuration_get(self, agent_id: str, config_id: str) -> dict[str, Any]:
        entity = self._one(s.configurations, agent_id, config_id)
        if entity["deleted_at"]:
            raise BusinessError("configuration_archived", "Configuration was archived", status=410)
        revision = self.connection.execute(
            select(s.configuration_revisions.c.id).where(
                s.configuration_revisions.c.config_id == config_id,
                s.configuration_revisions.c.revision == entity["revision"],
            )
        ).scalar_one()
        value = self._configuration_revision(agent_id, revision)
        return {**entity, "revision_id": revision, "fingerprint": value["fingerprint"], "value": value["value"]}

    def configuration_page(self, agent_id: str, kind: str, after: str = "", limit: int = 100) -> list[dict]:
        self._configuration_agent(agent_id)
        if kind not in OPTION_TABLES or not 1 <= limit <= 200:
            raise BusinessError("invalid_configuration_query", "Known kind and bounded page required", status=422)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.configurations)
                .where(
                    s.configurations.c.agent_id == agent_id,
                    s.configurations.c.kind == kind,
                    s.configurations.c.deleted_at.is_(None),
                    s.configurations.c.id > after,
                )
                .order_by(s.configurations.c.id)
                .limit(limit)
            ).mappings()
        ]

    def _configuration_record(self, agent_id: str, entity: dict, value: dict) -> str:
        if entity["kind"] in {"model", "channel", "mcp"} and value.get("credential_ref"):
            self.credential_register(value["credential_ref"])
        revision = self._record(
            s.configuration_revisions,
            {
                "config_id": entity["id"],
                "kind": entity["kind"],
                "revision": entity["revision"],
                "name": entity["name"],
                "enabled": entity["enabled"],
                "fingerprint": fingerprint(entity["kind"], entity["name"], entity["enabled"], value),
            },
            agent_id,
        )
        self.connection.execute(
            OPTION_TABLES[entity["kind"]]
            .insert()
            .values(revision_id=revision["id"], agent_id=agent_id, kind=entity["kind"], **value)
        )
        return revision["id"]

    def configuration_create(self, agent_id: str, kind: str, name: str, enabled: bool, value: dict) -> dict:
        self._configuration_agent(agent_id)
        name, key = config_name(name)
        value = config_value(kind, value)
        if type(enabled) is not bool:
            raise BusinessError("invalid_configuration", "enabled must be boolean", status=422)
        entity = self._record(
            s.configurations,
            {
                "kind": kind,
                "name": name,
                "name_key": key,
                "enabled": enabled,
                "revision": 1,
                "permission_epoch": 1,
                "deleted_at": None,
            },
            agent_id,
        )
        revision = self._configuration_record(agent_id, entity, value)
        self.audit(agent_id, "configuration.created", entity["id"], {"kind": kind, "revision_id": revision})
        return self.configuration_get(agent_id, entity["id"])

    def configuration_put(
        self, agent_id: str, config_id: str, name: str, enabled: bool, value: dict, revision: int
    ) -> dict:
        current = self.configuration_get(agent_id, config_id)
        if current["revision"] != revision:
            raise BusinessError("revision_conflict", "Configuration changed; retain your draft and reload")
        name, key = config_name(name)
        value = config_value(current["kind"], value)
        if type(enabled) is not bool:
            raise BusinessError("invalid_configuration", "enabled must be boolean", status=422)
        next_row = {**current, "name": name, "name_key": key, "enabled": enabled, "revision": revision + 1}
        identity = self._configuration_record(agent_id, next_row, value)
        count = self.connection.execute(
            update(s.configurations)
            .where(s.configurations.c.id == config_id, s.configurations.c.revision == revision)
            .values(
                name=name,
                name_key=key,
                enabled=enabled,
                revision=revision + 1,
                permission_epoch=current["permission_epoch"] + (enabled != current["enabled"]),
            )
        ).rowcount
        if count != 1:
            raise BusinessError("revision_conflict", "Configuration changed")
        self.audit(agent_id, "configuration.changed", config_id, {"kind": current["kind"], "revision_id": identity})
        return self.configuration_get(agent_id, config_id)

    def configuration_archive(self, agent_id: str, config_id: str, revision: int) -> None:
        current = self.configuration_get(agent_id, config_id)
        if revision != current["revision"]:
            raise BusinessError("revision_conflict", "Configuration changed")
        if self.connection.execute(
            select(s.settings.c.agent_id).where(
                s.settings.c.agent_id == agent_id, s.settings.c.model_profile_id == config_id
            )
        ).first():
            raise BusinessError("configuration_in_use", "Select another active model before archiving")
        self.connection.execute(
            update(s.configurations)
            .where(s.configurations.c.id == config_id)
            .values(deleted_at=self.clock.now(), permission_epoch=current["permission_epoch"] + 1)
        )
        self.audit(agent_id, "configuration.archived", config_id, {"revision": revision})

    def configuration_history(self, agent_id: str, config_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        self._one(s.configurations, agent_id, config_id)
        rows = self.connection.execute(
            select(s.configuration_revisions.c.id)
            .where(
                s.configuration_revisions.c.agent_id == agent_id,
                s.configuration_revisions.c.config_id == config_id,
                s.configuration_revisions.c.revision > after,
            )
            .order_by(s.configuration_revisions.c.revision)
            .limit(min(100, max(1, limit)))
        ).scalars()
        return [self._configuration_revision(agent_id, identity) for identity in rows]

    def settings(self, agent_id: str) -> dict:
        row = self.connection.execute(select(s.settings).where(s.settings.c.agent_id == agent_id)).mappings().first()
        if not row:
            raise BusinessError("agent_not_found", "Agent does not exist", status=404)
        value = {k: row[k] for k in KEYS if k != "disabled_tools"}
        value["disabled_tools"] = list(
            self.connection.execute(
                select(s.settings_disabled_tools.c.tool)
                .where(s.settings_disabled_tools.c.agent_id == agent_id)
                .order_by(s.settings_disabled_tools.c.tool)
            ).scalars()
        )
        return {"revision": row["revision"], "policy_revision": row["permission_epoch"], "value": value}

    def _settings_history_record(self, agent_id: str, value: dict, revision: int, epoch: int) -> str:
        return self._record(s.settings_revisions, {**value, "revision": revision, "permission_epoch": epoch}, agent_id)[
            "id"
        ]

    def settings_put(self, agent_id: str, value: dict, revision: int) -> dict:
        value = (
            {**value, "disabled_tools": list(value.get("disabled_tools", []))}
            if isinstance(value, dict) and isinstance(value.get("disabled_tools"), list)
            else value
        )
        validate_settings(value)
        current = self.settings(agent_id)
        if current["revision"] != revision:
            raise BusinessError("revision_conflict", "Settings changed; retain your draft and reload")
        model = self.configuration_get(agent_id, value["model_profile_id"])
        if model["kind"] != "model" or not model["enabled"]:
            raise BusinessError("model_configuration_unavailable", "Select an enabled model configuration")
        epoch = current["policy_revision"] + (
            set(value["disabled_tools"]) != set(current["value"]["disabled_tools"])
            or value["workspace"] != current["value"]["workspace"]
        )
        self.connection.execute(
            update(s.settings)
            .where(s.settings.c.agent_id == agent_id, s.settings.c.revision == revision)
            .values(
                **{k: v for k, v in value.items() if k != "disabled_tools"},
                revision=revision + 1,
                permission_epoch=epoch,
                updated_at=self.clock.now(),
            )
        )
        self.connection.execute(
            delete(s.settings_disabled_tools).where(s.settings_disabled_tools.c.agent_id == agent_id)
        )
        for tool in value["disabled_tools"]:
            self.connection.execute(s.settings_disabled_tools.insert().values(agent_id=agent_id, tool=tool))
        identity = self._settings_history_record(agent_id, value, revision + 1, epoch)
        self.audit(agent_id, "settings.changed", identity, {"revision": revision + 1, "policy_revision": epoch})
        return self.settings(agent_id)

    def settings_history(self, agent_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        self.settings(agent_id)
        return [
            dict(r)
            for r in self.connection.execute(
                select(s.settings_revisions)
                .where(s.settings_revisions.c.agent_id == agent_id, s.settings_revisions.c.revision > after)
                .order_by(s.settings_revisions.c.revision)
                .limit(min(100, max(1, limit)))
            ).mappings()
        ]

    def capture_configuration(self, agent_id: str, *, model_required: bool = True, model_id: str | None = None) -> dict:
        current = self.settings(agent_id)
        model = (
            self.configuration_get(agent_id, model_id or current["value"]["model_profile_id"])
            if model_required
            else None
        )
        if model is not None and (model["kind"] != "model" or not model["enabled"]):
            raise BusinessError("model_configuration_revoked", "Selected model is disabled")
        preference = self.connection.execute(
            select(s.settings_revisions.c.id).where(
                s.settings_revisions.c.agent_id == agent_id, s.settings_revisions.c.revision == current["revision"]
            )
        ).scalar_one()
        return {
            "settings_revision_id": preference,
            "model_revision_id": model["revision_id"] if model else None,
            "model_permission_epoch": model["permission_epoch"] if model else None,
            "policy_epoch": current["policy_revision"],
        }

    def run_settings(self, agent_id: str, run_id: str) -> dict:
        run = self._one(s.runs, agent_id, run_id)
        if run.get("execution_kind") == "owner" and run["settings_revision_id"]:
            preference = self._one(s.settings_revisions, agent_id, run["settings_revision_id"])
            return {**{k: preference[k] for k in KEYS}, "execution_kind": "owner", "model_revision_id": None}
        if not run["settings_revision_id"] or not run["model_revision_id"]:
            raise BusinessError("configuration_unresolved", "Historical configuration is unresolved")
        preference = self._one(s.settings_revisions, agent_id, run["settings_revision_id"])
        model = self._configuration_revision(agent_id, run["model_revision_id"])
        qq = qq_run(self, agent_id, run_id)
        return {
            **{k: preference[k] for k in KEYS},
            **model["value"],
            "model_profile_id": model["config_id"],
            "call_reservation_micro": self.route_reservation(agent_id, run_id, preference["call_reservation_micro"]),
            **({"persona": qq["persona"], "workspace": ""} if qq else {}),
            "model_revision_id": model["revision_id"],
            "model_permission_epoch": run["model_permission_epoch"],
        }

    def route_reservation(self, agent_id: str, run_id: str, default: int) -> int:
        # Assembled routing repositories resolve the captured per-tier reservation.
        return default

    def assert_model_authorized(self, agent_id: str, run_id: str) -> None:
        self.skill_authorize_run(agent_id, run_id)
        qq_run_authorized(self, agent_id, run_id)
        run = self._one(s.runs, agent_id, run_id)
        if not run["model_revision_id"]:
            raise BusinessError("configuration_unresolved", "Historical configuration is unresolved")
        revision = self._one(s.configuration_revisions, agent_id, run["model_revision_id"])
        entity = self._one(s.configurations, agent_id, revision["config_id"])
        if entity["deleted_at"] or not entity["enabled"] or entity["permission_epoch"] != run["model_permission_epoch"]:
            raise BusinessError("model_configuration_revoked", "Model authorization changed; submit a new turn")
        reference = self.connection.execute(
            select(s.model_options.c.credential_ref).where(
                s.model_options.c.revision_id == run["model_revision_id"], s.model_options.c.agent_id == agent_id
            )
        ).scalar_one()
        self.credential_authorize(reference)

    def skill_authorize_run(self, agent_id: str, run_id: str) -> None:
        # Assembled repositories validate active procedural-skill authority.
        return None

    def model_dispatch_authority(self, agent_id: str, run_id: str) -> dict:
        self.assert_model_authorized(agent_id, run_id)
        run = self._one(s.runs, agent_id, run_id)
        reference = self.connection.execute(
            select(s.model_options.c.credential_ref).where(
                s.model_options.c.revision_id == run["model_revision_id"], s.model_options.c.agent_id == agent_id
            )
        ).scalar_one()
        generation = ""
        if reference:
            revision, epoch = self.connection.execute(
                select(s.credential_references.c.revision, s.credential_references.c.authority_epoch).where(
                    s.credential_references.c.owner_id == self._credential_owner(),
                    s.credential_references.c.reference_sha256 == reference_fingerprint(reference),
                )
            ).one()
            generation = f"{revision}:{epoch}"
        return {
            "model_revision_id": run["model_revision_id"],
            "permission_epoch": run["model_permission_epoch"],
            "credential_generation": generation,
        }

    def record_metering(
        self, agent_id: str, call_id: str, tokens: int, method: str, counter_id: str, output: int, capacity: int
    ) -> None:
        call = self._one(s.model_calls, agent_id, call_id)
        self.assert_model_authorized(agent_id, call["run_id"])
        if call["state"] != "reserved":
            raise BusinessError("already_settled", "Model call is already settled")
        self.connection.execute(
            update(s.model_calls)
            .where(s.model_calls.c.id == call_id)
            .values(
                input_token_estimate=tokens,
                counting_method=method,
                counter_id=counter_id,
                output_token_limit=output,
                capacity_tokens=capacity,
            )
        )
