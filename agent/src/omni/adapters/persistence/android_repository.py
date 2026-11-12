"""Device grants bind immutable configuration/device identities; actions remain in the shared ledger."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, cast

from sqlalchemy import select, update

from ...domain.android import ACCESS_SPEC, access_request, connection, digest
from ...domain.android import device as device_record
from ...domain.contracts import BusinessError
from . import schema as s
from .action_predicates import unresolved_action
from .configuration_repository import ConfigurationRepositories

if TYPE_CHECKING:
    from .repository import Repositories


class AndroidRepositories(ConfigurationRepositories):
    def _android_access_record(self, row: dict) -> dict:
        packages = list(
            self.connection.execute(
                select(s.android_access_packages.c.package)
                .where(
                    s.android_access_packages.c.access_id == row["id"],
                    s.android_access_packages.c.agent_id == row["agent_id"],
                )
                .order_by(s.android_access_packages.c.package)
                .limit(17)
            ).scalars()
        )
        kinds = list(
            self.connection.execute(
                select(s.android_access_kinds.c.kind)
                .where(
                    s.android_access_kinds.c.access_id == row["id"],
                    s.android_access_kinds.c.agent_id == row["agent_id"],
                )
                .order_by(s.android_access_kinds.c.kind)
                .limit(9)
            ).scalars()
        )
        if (
            not 1 <= len(packages) <= 16
            or len(kinds) > 8
            or digest({"packages": packages, "kinds": kinds}) != row["scope_sha256"]
        ):
            raise BusinessError(
                "android_access_scope_changed",
                "Persisted application/action scope does not match its approved fingerprint",
            )
        return {**row, "packages": packages, "kinds": kinds}

    def _android_model_run(self, agent_id: str, run_id: str) -> dict:
        unit = cast("Repositories", self)
        unit.assert_context(agent_id, run_id)
        self.assert_model_authorized(agent_id, run_id)
        run = unit.get_run(agent_id, run_id)
        if (
            run["execution_kind"] != "model"
            or run["state"] not in {"running", "waiting_approval"}
            or unit.qq_run(agent_id, run_id)
        ):
            raise BusinessError("android_run_denied", "Android model access requires the active owner model run")
        return run

    def android_access_create(self, agent_id: str, run_id: str, action_id: str, value: dict) -> dict:
        request = access_request(value)
        unit = cast("Repositories", self)
        unit.approved_dispatch(agent_id, run_id, action_id, request, ACCESS_SPEC.name)
        run = self._android_model_run(agent_id, run_id)
        authority = self.android_authorize(agent_id, request["grant_id"])
        target = authority["grant"]["device"]
        if target["serial"] != request["target_serial"] or target["user_id"] != request["target_user"]:
            raise BusinessError("android_target_changed", "Requested identity does not match the device grant")
        prior = (
            self.connection.execute(
                select(s.android_access).where(
                    s.android_access.c.agent_id == agent_id, s.android_access.c.source_action_id == action_id
                )
            )
            .mappings()
            .first()
        )
        if prior:
            return self._android_access_record(dict(prior))
        expires = (
            dt.datetime.fromisoformat(self.clock.now()) + dt.timedelta(seconds=request["ttl_seconds"])
        ).isoformat(timespec="microseconds")
        row = self._record(
            s.android_access,
            {
                "grant_id": request["grant_id"],
                "run_id": run_id,
                "source_action_id": action_id,
                "session_epoch": unit.capture_session(agent_id, run["session_id"]),
                "policy_revision": self.settings(agent_id)["policy_revision"],
                "scope_sha256": digest({"packages": sorted(request["packages"]), "kinds": sorted(request["kinds"])}),
                "expires_at": expires,
                "revoked_at": None,
                "revision": 1,
            },
            agent_id,
        )
        for name in request["packages"]:
            self._record(s.android_access_packages, {"access_id": row["id"], "package": name}, agent_id)
        for kind in request["kinds"]:
            self._record(s.android_access_kinds, {"access_id": row["id"], "kind": kind}, agent_id)
        self.audit(
            agent_id,
            "android.run_access_granted",
            row["id"],
            {
                "run_id": run_id,
                "grant_id": request["grant_id"],
                "scope_sha256": row["scope_sha256"],
                "request_sha256": digest(request),
            },
        )
        return self._android_access_record(row)

    def android_access_authorize(self, agent_id: str, run_id: str, access_id: str) -> dict:
        row = self._android_access_record(self._one(s.android_access, agent_id, access_id))
        run = self._android_model_run(agent_id, run_id)
        unit = cast("Repositories", self)
        source = unit.action(agent_id, row["source_action_id"])
        if (
            row["run_id"] != run_id
            or row["revoked_at"]
            or row["expires_at"] <= self.clock.now()
            or row["session_epoch"] != unit.capture_session(agent_id, run["session_id"])
            or row["policy_revision"] != self.settings(agent_id)["policy_revision"]
            or source["run_id"] != run_id
            or source["tool"] != ACCESS_SPEC.name
            or source["state"] != "ok"
            or source["effect"] != "applied"
        ):
            raise BusinessError(
                "android_access_revoked", "Run device access expired, was revoked or belongs to another run"
            )
        return {**self.android_authorize(agent_id, row["grant_id"]), "access": row, "session_id": run["session_id"]}

    def android_access_revoke(self, agent_id: str, access_id: str, revision: int) -> dict:
        row = self._one(s.android_access, agent_id, access_id)
        if type(revision) is not int or revision != row["revision"]:
            raise BusinessError("revision_conflict", "Run device access changed", status=409)
        if not row["revoked_at"]:
            self.connection.execute(
                update(s.android_access)
                .where(s.android_access.c.id == access_id)
                .values(revoked_at=self.clock.now(), revision=revision + 1)
            )
            self.audit(agent_id, "android.run_access_revoked", access_id, {"run_id": row["run_id"]})
        return self._one(s.android_access, agent_id, access_id)

    def android_access_page(self, agent_id: str, after: str, limit: int) -> list[dict]:
        self.settings(agent_id)
        if not 1 <= limit <= 100:
            raise BusinessError("invalid_page", "Run access list requires a bounded page", status=422)
        rows = [
            dict(r)
            for r in self.connection.execute(
                select(s.android_access)
                .where(s.android_access.c.agent_id == agent_id, s.android_access.c.id > after)
                .order_by(s.android_access.c.id)
                .limit(limit)
            ).mappings()
        ]
        for row in rows:
            try:
                self.android_access_authorize(agent_id, row["run_id"], row["id"])
                row["availability"] = "available"
            except BusinessError as error:
                row["availability"] = error.code
            try:
                row.update(self._android_access_record(row))
            except BusinessError:
                row.update(packages=[], kinds=[], availability="android_access_scope_changed")
        return rows

    def android_dispatch(self, agent_id: str, grant_id: str, action_id: str) -> None:
        authority = self.android_authorize(agent_id, grant_id)
        serial = authority["grant"]["device"]["serial"]
        cleanup = self.connection.execute(
            select(s.android_unicode_environments.c.id)
            .where(
                s.android_unicode_environments.c.device_serial == serial,
                s.android_unicode_environments.c.state == "pending",
                s.android_unicode_environments.c.action_id != action_id,
            )
            .limit(1)
        ).first()
        if cleanup:
            raise BusinessError(
                "android_ime_cleanup_pending", "A previous temporary input method selection still requires recovery"
            )
        blocked = self.connection.execute(
            select(s.actions.c.id)
            .where(
                s.actions.c.tool.in_(["android_input", "android_act"]),
                s.actions.c.id != action_id,
                s.actions.c.arguments["target_serial"].as_string() == serial,
                s.actions.c.state.in_(["executing", "unknown"]),
                unresolved_action(),
            )
            .limit(1)
        ).first()
        if blocked:
            raise BusinessError("android_outcome_unresolved", "An earlier device action requires independent review")

    def android_connection(self, agent_id: str, config_id: str) -> dict:
        config = self.configuration_get(agent_id, config_id)
        if config["kind"] != "channel" or not config["enabled"]:
            raise BusinessError("android_configuration_disabled", "An enabled Android channel is required")
        return {"config_id": config_id, "revision_id": config["revision_id"], **connection(config["value"])}

    def android_grant(self, agent_id: str, config_id: str, revision_id: str, device: dict) -> dict:
        config = self.android_connection(agent_id, config_id)
        checked = device_record(device)
        if config["revision_id"] != revision_id or config["serial"] != checked["serial"]:
            raise BusinessError("android_configuration_changed", "Probe configuration changed; observe again")
        row = self._record(
            s.android_grants,
            {
                "config_id": config_id,
                "config_revision_id": revision_id,
                "device": checked,
                "device_sha256": digest(checked),
                "history_epoch": self.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
                "revision": 1,
                "revoked_at": None,
            },
            agent_id,
        )
        self.audit(
            agent_id, "android.device_granted", row["id"], {"config_id": config_id, "device_sha256": digest(checked)}
        )
        return row

    def android_authorize(self, agent_id: str, grant_id: str) -> dict:
        row = self._one(s.android_grants, agent_id, grant_id)
        config = self.android_connection(agent_id, row["config_id"])
        if (
            row["revoked_at"]
            or row["history_epoch"] != self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
            or config["revision_id"] != row["config_revision_id"]
            or digest(device_record(row["device"])) != row["device_sha256"]
        ):
            raise BusinessError("android_grant_revoked", "Device authority was revoked or changed")
        return {"grant": row, "connection": config}

    def android_revoke(self, agent_id: str, grant_id: str, revision: int) -> dict:
        row = self._one(s.android_grants, agent_id, grant_id)
        if type(revision) is not int or revision != row["revision"]:
            raise BusinessError("revision_conflict", "Device grant changed", status=409)
        if not row["revoked_at"]:
            self.connection.execute(
                update(s.android_grants)
                .where(s.android_grants.c.id == grant_id)
                .values(revoked_at=self.clock.now(), revision=revision + 1)
            )
            self.audit(agent_id, "android.device_revoked", grant_id, {"revision": revision + 1})
        return self._one(s.android_grants, agent_id, grant_id)

    def android_grants(self, agent_id: str, after: str, limit: int) -> list[dict]:
        self.settings(agent_id)
        if not 1 <= limit <= 100:
            raise BusinessError("invalid_page", "Device grants require a bounded page", status=422)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.android_grants)
                .where(s.android_grants.c.agent_id == agent_id, s.android_grants.c.id > after)
                .order_by(s.android_grants.c.id)
                .limit(limit)
            ).mappings()
        ]
