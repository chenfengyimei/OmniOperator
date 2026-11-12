"""One Bilibili authority implementation shared by owner reads, previews and final dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from ...domain.bilibili import connection, mentions_value
from ...domain.contracts import BusinessError
from ...domain.credentials import reference_fingerprint
from ...domain.delivery import body_hash
from . import schema as s

if TYPE_CHECKING:
    from .configuration_repository import ConfigurationRepositories


def read_authorized(
    unit: "ConfigurationRepositories", agent_id: str, config_id: str, revision_id: str = ""
) -> dict[str, Any]:
    config = unit.configuration_get(agent_id, config_id)
    connection(config["value"])
    if not config["enabled"] or (revision_id and config["revision_id"] != revision_id):
        raise BusinessError("bilibili_configuration_revoked", "Bilibili configuration changed or was disabled")
    if not unit.connection.execute(
        select(s.agents.c.id).where(s.agents.c.id == agent_id, s.agents.c.enabled.is_(True))
    ).first():
        raise BusinessError("agent_disabled", "Agent is disabled")
    reference = config["value"]["credential_ref"]
    unit.credential_authorize(reference)
    version = unit.connection.execute(
        select(s.credential_references.c.revision, s.credential_references.c.authority_epoch).where(
            s.credential_references.c.reference_sha256 == reference_fingerprint(reference)
        )
    ).one()
    return {**config, "authority": config["revision_id"] + ":" + ":".join(str(v) for v in version)}


def publication_for_delivery(
    unit: "ConfigurationRepositories", agent_id: str, message_id: str, config_id: str, target: str, revision_id: str
) -> dict[str, Any]:
    if not target.startswith("bilibili:"):
        raise BusinessError("bilibili_publication_required", "Prepare a publication before requesting delivery")
    row = unit.one(s.bilibili_publications, agent_id, target[len("bilibili:") :])
    if row["message_id"] != message_id or row["config_id"] != config_id or row["config_revision_id"] != revision_id:
        raise BusinessError("bilibili_publication_changed", "Publication source or sender changed")
    read_authorized(unit, agent_id, config_id, revision_id)
    revision = unit.one(s.bilibili_profile_revisions, agent_id, cast(str, row["profile_revision_id"]))
    profile = unit.one(s.bilibili_profiles, agent_id, cast(str, revision["profile_id"]))
    if not profile["enabled"] or profile["config_id"] != config_id or profile["revision"] != revision["revision"]:
        raise BusinessError("bilibili_profile_revoked", "Reviewed form profile changed or was disabled")
    message = unit.one(s.messages, agent_id, message_id)
    content = cast(str, message["content"])
    if message["deleted_at"] or body_hash(content) != row["body_hash"]:
        raise BusinessError("bilibili_source_invalid", "Publication source was erased or changed")
    mentions = mentions_value(
        content, [{**m, "name": content[m["start"] + 1 : m["end"]]} for m in cast(list[dict[str, Any]], row["mentions"])]
    )
    return {
        **row,
        "mentions": mentions,
        "form": cast(dict[str, Any], revision["forms"])[cast(str, row["kind"])],
        "profile_fingerprint": revision["fingerprint"],
        "headless": revision["headless"],
    }


def publication_preview(unit: "ConfigurationRepositories", agent_id: str, target: str) -> dict[str, Any] | None:
    if not target.startswith("bilibili:"):
        return None
    row = unit.one(s.bilibili_publications, agent_id, target[len("bilibili:") :])
    return {k: row[k] for k in ("id", "kind", "target", "target_key", "mentions", "body_hash", "profile_revision_id")}
