"""Immutable helper profiles and durable IME restoration obligations; input truth stays in actions."""

from __future__ import annotations

import re
from typing import Any, cast

from sqlalchemy import select, update

from ...application.ports import Record
from ...domain.android import RESTORE_SPEC, identity
from ...domain.android_unicode import fingerprint, ime_component, profile
from ...domain.contracts import BusinessError
from ...domain.credentials import reference_fingerprint
from . import schema as s
from .action_predicates import unresolved_action
from .configuration_repository import ConfigurationRepositories


class AndroidUnicodeRepositories(ConfigurationRepositories):
    def android_unicode_profile(self, agent_id: str, grant_id: str) -> Record:
        self._one(s.android_grants, agent_id, grant_id)
        row = (
            self.connection.execute(
                select(s.android_unicode_profiles)
                .where(
                    s.android_unicode_profiles.c.agent_id == agent_id, s.android_unicode_profiles.c.grant_id == grant_id
                )
                .order_by(s.android_unicode_profiles.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(
            Record,
            dict(row)
            if row
            else {
                "grant_id": grant_id,
                "revision": 0,
                "enabled": False,
                "credential_ref": "",
                "apk_sha256": "",
                "certificate_sha256": "",
            },
        )

    def android_unicode_save(self, agent_id: str, grant_id: str, value: Record, revision: int) -> Record:
        checked = profile(value)
        current = self.android_unicode_profile(agent_id, grant_id)
        if type(revision) is not int or revision != current["revision"]:
            raise BusinessError("revision_conflict", "Unicode helper profile changed", status=409)
        if checked["enabled"]:
            cast(Any, self).android_authorize(agent_id, grant_id)
            self.credential_authorize(cast(str, checked["credential_ref"]))
        row = self._record(
            s.android_unicode_profiles,
            {"grant_id": grant_id, "revision": revision + 1, **checked, "fingerprint": fingerprint(checked)},
            agent_id,
        )
        self.audit(
            agent_id,
            "android.unicode_profile_saved",
            cast(str, row["id"]),
            {"grant_id": grant_id, "revision": revision + 1, "fingerprint": row["fingerprint"]},
        )
        return row

    def android_unicode_authority(self, agent_id: str, grant_id: str) -> Record:
        cast(Any, self).android_authorize(agent_id, grant_id)
        row = self.android_unicode_profile(agent_id, grant_id)
        if not row["enabled"]:
            raise BusinessError("android_unicode_unconfigured", "Explicitly configure the pinned Unicode helper first")
        credential_ref = cast(str, row["credential_ref"])
        self.credential_authorize(credential_ref)
        version = self.connection.execute(
            select(s.credential_references.c.revision, s.credential_references.c.authority_epoch).where(
                s.credential_references.c.reference_sha256 == reference_fingerprint(credential_ref)
            )
        ).one()
        return {"profile": row, "credential_generation": ":".join(map(str, version))}

    def android_unicode_begin(
        self, agent_id: str, run_id: str, action_id: str, arguments: Record, profile_id: str, snapshot: Record
    ) -> Record:
        unit: Any = self
        action = unit.action(agent_id, action_id)
        tool = cast(str, action["tool"])
        unit.approved_dispatch(agent_id, run_id, action_id, arguments, tool)
        if tool not in {"android_input", "android_act"} or arguments.get("kind") != "append_unicode":
            raise BusinessError("android_unicode_invalid", "Restoration intent requires an approved Unicode input")
        if tool == "android_act":
            access = unit.android_access_authorize(agent_id, run_id, cast(str, arguments["access_id"]))
            grant_id = cast(str, cast(Record, access["grant"])["id"])
        else:
            grant_id = cast(str, arguments["grant_id"])
        authority = self.android_unicode_authority(agent_id, grant_id)
        if cast(Record, authority["profile"])["id"] != profile_id:
            raise BusinessError("android_unicode_profile_changed", "Unicode helper profile changed")
        if set(snapshot) != {"prepare_nonce", "original_ime", "original_subtype", "enabled_sha256"}:
            raise BusinessError("android_unicode_invalid", "Exact IME restoration evidence is required")
        identity(snapshot["prepare_nonce"])
        ime_component(snapshot["original_ime"])
        if (
            type(snapshot["original_subtype"]) is not int
            or not -2147483648 <= snapshot["original_subtype"] <= 2147483647
            or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot["enabled_sha256"]))
        ):
            raise BusinessError("android_unicode_invalid", "IME restoration snapshot is invalid")
        prior = (
            self.connection.execute(
                select(s.android_unicode_environments).where(
                    s.android_unicode_environments.c.agent_id == agent_id,
                    s.android_unicode_environments.c.action_id == action_id,
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if any(prior[key] != value for key, value in snapshot.items()):
                raise BusinessError("android_unicode_replay_denied", "An earlier IME intent cannot be replayed")
            return cast(Record, dict(prior))
        root = unit.android_authorize(agent_id, grant_id)
        root_grant = cast(Record, root["grant"])
        row = self._record(
            s.android_unicode_environments,
            {
                "action_id": action_id,
                "grant_id": grant_id,
                "profile_id": profile_id,
                "device_serial": cast(Record, root_grant["device"])["serial"],
                "server_port": cast(Record, root["connection"])["port"],
                **snapshot,
                "state": "pending",
                "revision": 1,
                "result_code": "prepared",
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "android.ime_restoration_prepared",
            cast(str, row["id"]),
            {"action_id": action_id, "snapshot_sha256": fingerprint(snapshot)},
        )
        return row

    def android_unicode_environment(self, agent_id: str, environment_id: str) -> Record:
        row = self._one(s.android_unicode_environments, agent_id, environment_id)
        return {**row, "device": self._one(s.android_grants, agent_id, cast(str, row["grant_id"]))["device"]}

    def android_unicode_environments(self, agent_id: str, after: str, limit: int) -> list[Record]:
        self._configuration_agent(agent_id)
        if after:
            identity(after)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BusinessError("invalid_page", "IME obligations require a bounded page", status=422)
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.android_unicode_environments)
                .where(
                    s.android_unicode_environments.c.agent_id == agent_id, s.android_unicode_environments.c.id > after
                )
                .order_by(s.android_unicode_environments.c.id)
                .limit(limit)
            ).mappings()
        ]

    def android_unicode_observed(self, agent_id: str, environment_id: str, value: Record) -> Record:
        row = self._one(s.android_unicode_environments, agent_id, environment_id)
        if row["state"] != "pending":
            return row
        if set(value) == {"result_code"} and re.fullmatch(r"[a-z_]{1,80}", str(value["result_code"])):
            self.connection.execute(
                update(s.android_unicode_environments)
                .where(s.android_unicode_environments.c.id == environment_id)
                .values(
                    result_code=value["result_code"],
                    observed_at=self.clock.now(),
                    revision=cast(int, row["revision"]) + 1,
                )
            )
            self.audit(
                agent_id, "android.ime_restoration_unknown", environment_id, {"result_code": value["result_code"]}
            )
            return self._one(s.android_unicode_environments, agent_id, environment_id)
        if (
            set(value) != {"current_ime", "current_subtype", "enabled_sha256", "result_code"}
            or type(value["current_subtype"]) is not int
            or not -2147483648 <= value["current_subtype"] <= 2147483647
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["enabled_sha256"]))
        ):
            raise BusinessError("android_unicode_invalid", "Exact environment observation is required")
        ime_component(value["current_ime"])
        if not re.fullmatch(r"[a-z_]{1,80}", str(value["result_code"])):
            raise BusinessError("android_unicode_invalid", "Environment result code is invalid")
        restored = (
            value["current_ime"] == row["original_ime"]
            and value["current_subtype"] == row["original_subtype"]
            and value["enabled_sha256"] == row["enabled_sha256"]
        )
        self.connection.execute(
            update(s.android_unicode_environments)
            .where(s.android_unicode_environments.c.id == environment_id)
            .values(
                current_ime=value["current_ime"],
                current_subtype=value["current_subtype"],
                result_code=value["result_code"],
                observed_at=self.clock.now(),
                revision=cast(int, row["revision"]) + 1,
                state="restored" if restored else "pending",
            )
        )
        self.audit(
            agent_id,
            "android.ime_restoration_observed",
            environment_id,
            {"restored": restored, "observation_sha256": fingerprint(value)},
        )
        return self._one(s.android_unicode_environments, agent_id, environment_id)

    def android_unicode_recovery_dispatch(
        self, agent_id: str, run_id: str, action_id: str, arguments: Record
    ) -> Record:
        unit: Any = self
        unit.owner_dispatch(agent_id, run_id, action_id, arguments, RESTORE_SPEC.name)
        row = self.android_unicode_environment(agent_id, cast(str, arguments["environment_id"]))
        if (
            row["state"] != "pending"
            or row["revision"] != arguments["revision"]
            or row["device_serial"] != arguments["target_serial"]
            or row["original_ime"] != arguments["original_ime"]
        ):
            raise BusinessError("android_ime_recovery_changed", "IME obligation changed; prepare a new recovery")
        prior = self.connection.execute(
            select(s.actions.c.id)
            .where(
                s.actions.c.agent_id == agent_id,
                s.actions.c.tool == RESTORE_SPEC.name,
                s.actions.c.id != action_id,
                s.actions.c.arguments["environment_id"].as_string() == row["id"],
                unresolved_action(),
            )
            .limit(1)
        ).first()
        if prior:
            raise BusinessError("android_ime_recovery_unresolved", "Review an earlier recovery before another dispatch")
        return row
