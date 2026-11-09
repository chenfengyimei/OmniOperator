"""Shared QQ audience and credential authority for context, model and outbound checks."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ...domain.contracts import BusinessError
from ...domain.credentials import reference_fingerprint
from ...domain.delivery import account_resource
from ...domain.qq import ADAPTER, SEND_COOLDOWN_SECONDS, connection_value
from ...domain.scheduling import instant, timestamp
from . import schema as s

if TYPE_CHECKING:
    from .configuration_repository import ConfigurationRepositories


def qq_send_after(unit: "ConfigurationRepositories", account: str) -> str:
    started = unit.connection.execute(
        select(func.max(s.delivery_attempts.c.started_at))
        .join(
            s.outbox,
            (s.outbox.c.id == s.delivery_attempts.c.delivery_id)
            & (s.outbox.c.agent_id == s.delivery_attempts.c.agent_id),
        )
        .where(s.outbox.c.resource_key == account_resource("qq", account), s.outbox.c.adapter_id == ADAPTER)
    ).scalar_one()
    return timestamp(instant(started) + dt.timedelta(seconds=SEND_COOLDOWN_SECONDS)) if started else ""


def qq_connection_authorized(
    unit: "ConfigurationRepositories", agent_id: str, config_id: str, revision_id: str
) -> dict:
    config = unit.configuration_get(agent_id, config_id)
    if not unit.connection.execute(
        select(s.agents.c.id).where(s.agents.c.id == agent_id, s.agents.c.enabled.is_(True))
    ).first():
        raise BusinessError("agent_disabled", "Agent is disabled")
    if not config["enabled"] or config["revision_id"] != revision_id:
        raise BusinessError("qq_configuration_revoked", "QQ connection configuration changed")
    connection_value(config["value"])
    unit.credential_authorize(config["value"]["credential_ref"])
    reference = config["value"]["credential_ref"]
    credential = (
        unit.connection.execute(
            select(s.credential_references.c.revision, s.credential_references.c.authority_epoch).where(
                s.credential_references.c.reference_sha256 == reference_fingerprint(reference)
            )
        ).first()
        if reference
        else None
    )
    config["transport_revision"] = config["revision_id"] + (
        ":" + ":".join(str(v) for v in credential) if credential else ""
    )
    return config


def qq_run(unit: "ConfigurationRepositories", agent_id: str, run_id: str) -> dict | None:
    row = (
        unit.connection.execute(select(s.qq_runs).where(s.qq_runs.c.agent_id == agent_id, s.qq_runs.c.run_id == run_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def qq_run_authorized(unit: "ConfigurationRepositories", agent_id: str, run_id: str) -> dict | None:
    row = qq_run(unit, agent_id, run_id)
    if not row:
        return None
    binding = unit._one(s.qq_bindings, agent_id, row["binding_id"])
    if not binding["enabled"] or binding["revision"] != row["binding_revision"]:
        raise BusinessError("qq_binding_revoked", "QQ conversation grant changed")
    account = unit.connection.execute(
        select(s.accounts).where(
            s.accounts.c.agent_id == agent_id,
            s.accounts.c.id == row["account_binding_id"],
            s.accounts.c.person_id == row["person_id"],
            s.accounts.c.external_id == row["sender_id"],
            s.accounts.c.channel == "qq",
        )
    ).first()
    if account is None:
        raise BusinessError("qq_sender_revoked", "QQ sender binding was removed")
    person = unit._one(s.people, agent_id, row["person_id"])
    if person["deleted_at"]:
        raise BusinessError("qq_sender_revoked", "QQ sender person was erased")
    config = qq_connection_authorized(unit, agent_id, binding["config_id"], row["config_revision_id"])
    return {
        "config_id": config["id"],
        "config_revision_id": row["config_revision_id"],
        "adapter_id": ADAPTER,
        "account_key": config["value"]["account"],
        "target": binding["scene"] + ":" + binding["peer_id"],
    }
