"""V2 relational schema. Repositories never issue DDL; migrations use frozen SQL snapshots."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def ident() -> Column:
    return Column("id", String(36), CheckConstraint("length(id) = 36", name="uuid_length"), primary_key=True)


def scope() -> Column:
    return Column("agent_id", String(36), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True)


def stamp() -> Column:
    return Column("created_at", String(40), nullable=False)


def scoped_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint([column, "agent_id"], [f"{table}.id", f"{table}.agent_id"], ondelete="RESTRICT")


def scoped(name: str, *columns) -> Table:
    return Table(name, metadata, ident(), scope(), stamp(), *columns, UniqueConstraint("id", "agent_id"))


qq_bindings = scoped(
    "qq_bindings",
    Column("config_id", String(36), nullable=False),
    Column("session_id", String(36), nullable=False),
    Column("scene", String(12), nullable=False),
    Column("peer_id", String(19), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("persona", Text, nullable=False),
    Column("cooldown_s", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("last_received_at", String(40)),
    scoped_fk("config_id", "configurations"),
    scoped_fk("session_id", "sessions"),
    UniqueConstraint("agent_id", "config_id", "scene", "peer_id"),
    UniqueConstraint("session_id"),
    CheckConstraint("scene IN ('private','group') AND enabled IN (0,1)", name="scope"),
    CheckConstraint(
        "length(peer_id) BETWEEN 1 AND 19 AND peer_id NOT GLOB '*[^0-9]*' AND substr(peer_id,1,1)!='0' AND CAST(peer_id AS INTEGER)>0",
        name="peer",
    ),
    CheckConstraint("length(persona) BETWEEN 1 AND 8000 AND instr(persona,char(0))=0", name="persona"),
    CheckConstraint(
        "typeof(revision)='integer' AND revision>0 AND typeof(cooldown_s)='integer' AND cooldown_s BETWEEN 1 AND 3600",
        name="revision",
    ),
)
qq_runs = scoped(
    "qq_runs",
    Column("run_id", String(36), nullable=False),
    Column("binding_id", String(36), nullable=False),
    Column("binding_revision", Integer, nullable=False),
    Column("config_revision_id", String(36), nullable=False),
    Column("account_binding_id", String(36), nullable=False),
    Column("person_id", String(36), nullable=False),
    Column("sender_id", String(19), nullable=False),
    Column("event_key", String(64), nullable=False),
    Column("event_fingerprint", String(64), nullable=False),
    Column("persona", Text, nullable=False),
    scoped_fk("run_id", "runs"),
    scoped_fk("binding_id", "qq_bindings"),
    scoped_fk("config_revision_id", "configuration_revisions"),
    scoped_fk("person_id", "people"),
    UniqueConstraint("run_id"),
    UniqueConstraint("agent_id", "event_key"),
    CheckConstraint("binding_revision>0 AND length(event_key)=64 AND length(event_fingerprint)=64", name="identity"),
    CheckConstraint("length(persona) BETWEEN 1 AND 8000 AND length(sender_id) BETWEEN 1 AND 19", name="content"),
)


bilibili_profiles = scoped(
    "bilibili_profiles",
    Column("config_id", String(36), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("headless", Boolean, nullable=False),
    Column("revision", Integer, nullable=False),
    scoped_fk("config_id", "configurations"),
    UniqueConstraint("agent_id", "config_id"),
    CheckConstraint(
        "enabled IN (0,1) AND headless IN (0,1) AND typeof(revision)='integer' AND revision>0", name="revision"
    ),
)
bilibili_profile_revisions = scoped(
    "bilibili_profile_revisions",
    Column("profile_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("headless", Boolean, nullable=False),
    Column("forms", JSON, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    scoped_fk("profile_id", "bilibili_profiles"),
    UniqueConstraint("profile_id", "revision"),
    CheckConstraint(
        "revision>0 AND enabled IN (0,1) AND headless IN (0,1) AND json_valid(forms) AND length(CAST(forms AS BLOB))<=32768 AND length(fingerprint)=64",
        name="payload",
    ),
)
bilibili_publications = scoped(
    "bilibili_publications",
    Column("message_id", String(36), nullable=False),
    Column("config_id", String(36), nullable=False),
    Column("config_revision_id", String(36), nullable=False),
    Column("profile_revision_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("target", Text, nullable=False),
    Column("target_key", String(32), nullable=False),
    Column("mentions", JSON, nullable=False),
    Column("body_hash", String(64), nullable=False),
    Column("key", String(200), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    scoped_fk("message_id", "messages"),
    scoped_fk("config_id", "configurations"),
    scoped_fk("config_revision_id", "configuration_revisions"),
    scoped_fk("profile_revision_id", "bilibili_profile_revisions"),
    UniqueConstraint("agent_id", "key"),
    CheckConstraint(
        "kind IN ('comment','private_message') AND length(target) BETWEEN 1 AND 500 AND length(target_key) BETWEEN 1 AND 32",
        name="target",
    ),
    UniqueConstraint("agent_id", "request_sha256"),
    CheckConstraint(
        "json_valid(mentions) AND json_type(mentions)='array' AND json_array_length(mentions)<=10 AND length(CAST(mentions AS BLOB))<=8192 AND length(body_hash)=64 AND length(request_sha256)=64 AND length(key) BETWEEN 1 AND 200",
        name="payload",
    ),
)
bilibili_deliveries = scoped(
    "bilibili_deliveries",
    Column("publication_id", String(36), nullable=False),
    Column("delivery_id", String(36), nullable=False),
    scoped_fk("publication_id", "bilibili_publications"),
    scoped_fk("delivery_id", "outbox"),
    UniqueConstraint("delivery_id"),
)


principals = Table(
    "principals",
    metadata,
    ident(),
    stamp(),
    Column("role", String(20), nullable=False),
    Column("name", Text, nullable=False),
    UniqueConstraint("role"),
    CheckConstraint("role = 'owner'", name="single_owner"),
)
instances = Table(
    "instances",
    metadata,
    ident(),
    stamp(),
    Column("slot", Integer, nullable=False, unique=True),
    Column("owner_id", String(36), ForeignKey("principals.id"), nullable=False),
    CheckConstraint("slot = 1", name="singleton"),
)
agents = Table(
    "agents",
    metadata,
    ident(),
    stamp(),
    Column("name", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("revision", Integer, nullable=False, default=1),
    CheckConstraint("revision > 0", name="revision"),
)


def preference_columns():
    return (
        Column("persona", Text, nullable=False),
        Column("workspace", Text, nullable=False),
        Column(
            "daily_budget_micro", Integer, CheckConstraint("daily_budget_micro>=0", name="daily_budget"), nullable=False
        ),
        Column(
            "call_reservation_micro",
            Integer,
            CheckConstraint("call_reservation_micro>0", name="call_budget"),
            nullable=False,
        ),
        Column("max_steps", Integer, CheckConstraint("max_steps BETWEEN 1 AND 32", name="step_budget"), nullable=False),
        Column("model_profile_id", String(36), nullable=False),
        Column(
            "model_kind",
            String(20),
            CheckConstraint("model_kind='model'", name="model_kind"),
            nullable=False,
            server_default="model",
        ),
        ForeignKeyConstraint(
            ["model_profile_id", "agent_id", "model_kind"],
            ["configurations.id", "configurations.agent_id", "configurations.kind"],
            ondelete="RESTRICT",
        ),
    )


settings = Table(
    "settings",
    metadata,
    scope(),
    Column("revision", Integer, nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("permission_epoch", Integer, CheckConstraint("permission_epoch>0", name="permission_epoch"), nullable=False),
    *preference_columns(),
    UniqueConstraint("agent_id"),
    CheckConstraint("revision>0", name="revision"),
)
settings_disabled_tools = Table(
    "settings_disabled_tools",
    metadata,
    Column("agent_id", String(36), ForeignKey("settings.agent_id", ondelete="RESTRICT"), primary_key=True),
    Column("tool", String(100), CheckConstraint("length(tool) BETWEEN 1 AND 100", name="tool"), primary_key=True),
)
settings_revisions = scoped(
    "settings_revisions",
    Column("revision", Integer, CheckConstraint("revision>0", name="revision"), nullable=False),
    *preference_columns(),
    Column("permission_epoch", Integer, nullable=False),
    Column("disabled_tools", JSON, nullable=False),
    UniqueConstraint("agent_id", "revision"),
)
configurations = scoped(
    "configurations",
    Column("kind", String(20), nullable=False),
    Column("name", String(100), nullable=False),
    Column("name_key", String(200), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("permission_epoch", Integer, nullable=False),
    Column("deleted_at", String(40)),
    UniqueConstraint("id", "agent_id", "kind"),
    CheckConstraint("kind IN ('model','channel','mcp','preset')", name="kind"),
    CheckConstraint(
        "revision>0 AND permission_epoch>0 AND enabled IN (0,1) AND length(trim(name)) BETWEEN 1 AND 100", name="bounds"
    ),
    ForeignKeyConstraint(
        ["id", "revision", "agent_id"],
        ["configuration_revisions.config_id", "configuration_revisions.revision", "configuration_revisions.agent_id"],
        deferrable=True,
        initially="DEFERRED",
    ),
)
configuration_revisions = scoped(
    "configuration_revisions",
    Column("config_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("revision", Integer, CheckConstraint("revision>0", name="revision"), nullable=False),
    Column("name", String(100), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("fingerprint", String(64), CheckConstraint("length(fingerprint)=64", name="fingerprint"), nullable=False),
    ForeignKeyConstraint(
        ["config_id", "agent_id", "kind"],
        ["configurations.id", "configurations.agent_id", "configurations.kind"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("config_id", "revision", "agent_id"),
    UniqueConstraint("id", "agent_id", "kind"),
)


def option_columns(kind):
    return (
        Column("revision_id", String(36), primary_key=True),
        Column("agent_id", String(36), nullable=False),
        Column("kind", String(20), CheckConstraint(f"kind='{kind}'", name="kind"), nullable=False, server_default=kind),
        ForeignKeyConstraint(
            ["revision_id", "agent_id", "kind"],
            ["configuration_revisions.id", "configuration_revisions.agent_id", "configuration_revisions.kind"],
            ondelete="RESTRICT",
        ),
    )


model_options = Table(
    "model_options",
    metadata,
    *option_columns("model"),
    Column("model", String(200), nullable=False),
    Column("base_url", String(2000), nullable=False),
    Column("credential_ref", String(200), nullable=False),
    Column("context_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("summary_tokens", Integer, nullable=False),
    Column("margin_tokens", Integer, nullable=False),
    Column("counter_mode", String(30), nullable=False),
    Column("counter_ref", String(100), nullable=False),
    CheckConstraint(
        "context_tokens BETWEEN 1024 AND 2000000 AND output_tokens>0 AND summary_tokens>0 AND margin_tokens>=128 AND output_tokens+margin_tokens<context_tokens AND summary_tokens+margin_tokens<context_tokens",
        name="capacity",
    ),
    CheckConstraint("counter_mode IN ('utf8_upper_bound','required') AND length(counter_ref)>0", name="counter"),
)
channel_options = Table(
    "channel_options",
    metadata,
    *option_columns("channel"),
    Column("driver", String(20), nullable=False),
    Column("endpoint", String(2000), nullable=False),
    Column("account", String(200), nullable=False),
    Column("credential_ref", String(200), nullable=False),
    CheckConstraint("driver IN ('qq','bilibili','android')", name="driver"),
)
mcp_options = Table(
    "mcp_options",
    metadata,
    *option_columns("mcp"),
    Column("transport", String(30), nullable=False),
    Column("command", String(4000), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("endpoint", String(2000), nullable=False),
    Column("auth_mode", String(20), nullable=False),
    Column("credential_ref", String(200), nullable=False),
    CheckConstraint("auth_mode IN ('none','bearer','oauth')", name="auth_mode"),
    CheckConstraint(
        "(transport='stdio' AND endpoint='' AND auth_mode='none') OR (transport='streamable_http' AND command='' AND json_array_length(arguments)=0 AND length(endpoint)>0)",
        name="transport",
    ),
)
preset_options = Table(
    "preset_options",
    metadata,
    *option_columns("preset"),
    Column("content", Text, CheckConstraint("length(content)<=50000", name="length"), nullable=False),
)
Index(
    "uq_configuration_name",
    configurations.c.agent_id,
    configurations.c.kind,
    configurations.c.name_key,
    unique=True,
    sqlite_where=configurations.c.deleted_at.is_(None),
)
Index("ix_configuration_cursor", configurations.c.agent_id, configurations.c.kind, configurations.c.id)

people = scoped(
    "people",
    Column("name", Text, nullable=False),
    Column("owner_id", String(36), ForeignKey("principals.id")),
    Column("revision", Integer, nullable=False, default=1),
    Column(
        "deleted_at",
        String(40),
        CheckConstraint(
            "revision > 0 AND (owner_id IS NULL OR deleted_at IS NULL) AND (deleted_at IS NOT NULL OR length(trim(name)) BETWEEN 1 AND 100)",
            name="person_state",
        ),
    ),
)
accounts = scoped(
    "accounts",
    Column("person_id", String(36), nullable=False),
    Column("channel", String(40), nullable=False),
    Column("external_id", Text, nullable=False),
    Column(
        "revision",
        Integer,
        CheckConstraint(
            "revision > 0 AND length(channel) BETWEEN 1 AND 32 AND length(trim(external_id)) BETWEEN 1 AND 200",
            name="account_state",
        ),
        nullable=False,
        server_default="1",
    ),
    Column(
        "verification",
        String(16),
        CheckConstraint("verification IN ('manual','verified')", name="verification"),
        nullable=False,
        server_default="manual",
    ),
    scoped_fk("person_id", "people"),
    UniqueConstraint("agent_id", "channel", "external_id"),
)
sessions = scoped(
    "sessions",
    Column("title", Text, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("next_seq", Integer, nullable=False, default=0),
    Column("deleted_at", String(40)),
    Column(
        "content_epoch",
        Integer,
        CheckConstraint("content_epoch > 0", name="content_epoch"),
        nullable=False,
        server_default="1",
    ),
    Column(
        "context_floor_seq",
        Integer,
        CheckConstraint("context_floor_seq >= 0 AND context_floor_seq <= next_seq", name="context_floor"),
        nullable=False,
        server_default="0",
    ),
)
messages = scoped(
    "messages",
    Column("session_id", String(36), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("run_id", String(36)),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("deleted_at", String(40)),
    scoped_fk("session_id", "sessions"),
    UniqueConstraint("session_id", "seq"),
    CheckConstraint("role IN ('user','assistant','system')", name="role"),
)
objects = scoped(
    "objects",
    Column("sha256", String(64), nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("media_type", String(100), nullable=False),
    Column("pinned", Boolean, nullable=False, default=False),
    Column("expires_at", String(40)),
    Column("revision", Integer, nullable=False, server_default="1"),
    UniqueConstraint("agent_id", "sha256"),
    CheckConstraint("bytes >= 0", name="nonnegative_size"),
    CheckConstraint("revision > 0", name="revision"),
)
message_parts = scoped(
    "message_parts",
    Column("message_id", String(36), nullable=False),
    Column("position", Integer, nullable=False),
    Column("kind", String(30), nullable=False),
    Column("object_id", String(36)),
    scoped_fk("message_id", "messages"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("message_id", "position"),
)
runs = scoped(
    "runs",
    Column("session_id", String(36), nullable=False),
    Column("parent_id", String(36)),
    Column("input_message_id", String(36), nullable=False),
    Column("state", String(30), nullable=False),
    Column("config_revision", Integer, nullable=False),
    Column("last_seq", Integer, nullable=False, default=0),
    Column("error_code", String(100), nullable=False, default=""),
    Column("ended_at", String(40)),
    scoped_fk("session_id", "sessions"),
    scoped_fk("parent_id", "runs"),
    scoped_fk("input_message_id", "messages"),
    CheckConstraint(
        "state IN ('queued','running','waiting_approval','cancelling','succeeded','failed','cancelled','interrupted')",
        name="state",
    ),
)
run_events = Table(
    "run_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    scope(),
    Column("run_id", String(36), nullable=False),
    Column("seq", Integer, nullable=False),
    stamp(),
    Column("kind", String(80), nullable=False),
    Column("data", JSON, nullable=False),
    scoped_fk("run_id", "runs"),
    UniqueConstraint("run_id", "seq"),
)
context_snapshots = scoped(
    "context_snapshots",
    Column("ordinal", Integer, CheckConstraint("ordinal>0", name="ordinal"), nullable=False, server_default="1"),
    Column("run_id", String(36), nullable=False),
    Column(
        "session_epoch",
        Integer,
        CheckConstraint("session_epoch>=0", name="session_epoch"),
        nullable=False,
        server_default="0",
    ),
    Column(
        "mode",
        String(20),
        CheckConstraint(
            "mode IN ('complete','summary','truncated','redacted','blocked','preparing','legacy')", name="mode"
        ),
        nullable=False,
        server_default="legacy",
    ),
    Column(
        "omitted_count",
        Integer,
        CheckConstraint("omitted_count >= 0", name="omitted_count"),
        nullable=False,
        server_default="0",
    ),
    Column("reason", String(80), nullable=False, server_default=""),
    Column(
        "input_chars",
        Integer,
        CheckConstraint("input_chars >= 0", name="input_chars"),
        nullable=False,
        server_default="0",
    ),
    scoped_fk("run_id", "runs"),
)
summaries = scoped(
    "summaries",
    Column("session_id", String(36), nullable=False),
    Column("first_seq", Integer, nullable=False),
    Column("last_seq", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("session_epoch", Integer, nullable=False, server_default="1"),
    Column("parent_id", String(36)),
    Column("run_id", String(36)),
    Column("model_call_id", String(36)),
    Column("state", String(20), nullable=False),
    Column(
        "source_hash",
        String(64),
        CheckConstraint("length(source_hash) IN (0,64)", name="summary_source_hash"),
        nullable=False,
    ),
    Column("covered_count", Integer, nullable=False),
    Column("error_code", String(80), nullable=False, server_default=""),
    scoped_fk("session_id", "sessions"),
    scoped_fk("run_id", "runs"),
    scoped_fk("model_call_id", "model_calls"),
    ForeignKeyConstraint(
        ["parent_id", "session_id", "agent_id"],
        ["summaries.id", "summaries.session_id", "summaries.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("id", "session_id", "agent_id"),
    CheckConstraint("first_seq <= last_seq", name="valid_interval"),
    CheckConstraint(
        "revision > 0 AND session_epoch > 0 AND covered_count >= 0 AND length(content)<=4000", name="bounds"
    ),
    CheckConstraint("state IN ('building','ready','failed','unknown','invalidated')", name="state"),
    CheckConstraint(
        "state!='ready' OR (model_call_id IS NOT NULL AND run_id IS NOT NULL AND length(content)>0 AND length(source_hash)=64)",
        name="published",
    ),
)
summary_messages = scoped(
    "summary_messages",
    Column("summary_id", String(36), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("session_id", String(36), nullable=False),
    ForeignKeyConstraint(
        ["summary_id", "session_id", "agent_id"],
        ["summaries.id", "summaries.session_id", "summaries.agent_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["message_id", "session_id", "agent_id"],
        ["messages.id", "messages.session_id", "messages.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("summary_id", "message_id"),
)
context_messages = scoped(
    "context_messages",
    Column("snapshot_id", String(36), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("position", Integer, CheckConstraint("position>=0", name="position"), nullable=False),
    scoped_fk("snapshot_id", "context_snapshots"),
    scoped_fk("message_id", "messages"),
    UniqueConstraint("snapshot_id", "message_id"),
    UniqueConstraint("snapshot_id", "position"),
)
context_summaries = scoped(
    "context_summaries",
    Column("snapshot_id", String(36), nullable=False),
    Column("summary_id", String(36), nullable=False),
    scoped_fk("snapshot_id", "context_snapshots"),
    scoped_fk("summary_id", "summaries"),
    UniqueConstraint("snapshot_id"),
)
work_items = scoped(
    "work_items",
    Column("run_id", String(36)),
    Column("kind", String(40), nullable=False),
    Column("state", String(20), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("claimed_by", String(36)),
    Column("claim_token", String(36)),
    Column("lease_fence", Integer),
    Column("queue_sequence", Integer, nullable=False, unique=True),
    CheckConstraint("queue_sequence>0 AND attempt>=0 AND (kind!='turn' OR run_id IS NOT NULL)", name="queue"),
    CheckConstraint(
        "kind!='turn' OR state NOT IN ('claimed','running') OR (claimed_by IS NOT NULL AND claim_token IS NOT NULL AND length(claim_token)=36 AND lease_fence IS NOT NULL AND lease_fence>0)",
        name="claim",
    ),
    Column("attempt", Integer, nullable=False, default=0),
    scoped_fk("run_id", "runs"),
    UniqueConstraint("run_id", "kind"),
    CheckConstraint("state IN ('pending','claimed','running','done','failed','cancelled','unknown')", name="state"),
)
inbox = scoped(
    "inbox",
    Column("channel", String(40), nullable=False),
    Column("key", String(200), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("run_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    UniqueConstraint("agent_id", "channel", "key"),
)
actions = scoped(
    "actions",
    Column("run_id", String(36), nullable=False),
    Column("tool", String(100), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("arguments_sha256", String(64), nullable=False),
    Column("writes", Boolean, nullable=False),
    Column("state", String(30), nullable=False),
    Column("effect", String(20), nullable=False, default="none"),
    Column("policy_revision", Integer, nullable=False),
    Column("result", JSON),
    Column("ended_at", String(40)),
    scoped_fk("run_id", "runs"),
    CheckConstraint(
        "state IN ('pending','approved','denied','executing','ok','error','cancelled','unknown')", name="state"
    ),
    CheckConstraint("effect IN ('none','applied','unknown')", name="effect"),
)
approvals = scoped(
    "approvals",
    Column("action_id", String(36), nullable=False, unique=True),
    Column("state", String(20), nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("resolved_at", String(40)),
    scoped_fk("action_id", "actions"),
    CheckConstraint("state IN ('pending','approved','denied','expired','cancelled')", name="state"),
)
action_objects = scoped(
    "action_objects",
    Column("action_id", String(36), nullable=False),
    Column("object_id", String(36), nullable=False),
    scoped_fk("action_id", "actions"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("action_id", "object_id"),
)
audit = Table(
    "audit",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    scope(),
    stamp(),
    Column("id", String(36), nullable=False, unique=True),
    Column("kind", String(80), nullable=False),
    Column("operation_id", String(36)),
    Column("data", JSON, nullable=False),
)
instance_audit = Table(
    "instance_audit",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("id", String(36), nullable=False, unique=True),
    stamp(),
    Column("owner_id", String(36), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False),
    Column("operation_id", String(36), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("outcome", String(20), nullable=False),
    Column("data", JSON, nullable=False),
    UniqueConstraint("operation_id", "kind"),
    CheckConstraint("length(id)=36 AND length(operation_id)=36", name="identities"),
    CheckConstraint(
        "(kind='credential.requested' AND outcome='requested') OR (kind='credential.resolved' AND outcome IN ('applied','not_applied','unknown')) OR (kind='credential.registered' AND outcome='registered') OR (kind='credential.reviewed' AND outcome IN ('allowed','revoked')) OR (kind='credential.recovered' AND outcome IN ('not_applied','unknown')) OR (kind='credential.restored' AND outcome='unknown')",
        name="event",
    ),
    sqlite_autoincrement=True,
)
outbox = scoped(
    "outbox",
    Column("run_id", String(36), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("channel", String(40), nullable=False),
    Column("target", String(512), nullable=False),
    Column("config_id", String(36)),
    Column("config_revision_id", String(36)),
    Column("config_kind", String(20), nullable=False, server_default="channel"),
    Column("adapter_id", String(100)),
    Column("account_key", String(200)),
    Column("resource_key", String(64)),
    Column("session_epoch", Integer),
    Column("body_hash", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("policy", String(50), nullable=False),
    Column("key", String(200), nullable=False),
    Column("parent_id", String(36)),
    Column("queue_sequence", Integer, nullable=False, unique=True),
    Column("state", String(24), nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("attempt_seq", Integer, nullable=False, server_default="0"),
    Column("outcome_revision", Integer, nullable=False, server_default="1"),
    Column("latest_attempt_id", String(36)),
    Column("next_attempt_at", String(40)),
    Column("approval_until", String(40)),
    Column("error_code", String(80), nullable=False, server_default=""),
    Column("review_epoch", Integer, nullable=False, server_default="1"),
    Column("reviewed_epoch", Integer, nullable=False, server_default="0"),
    Column("last_review_id", String(36)),
    Column("resolution", String(24)),
    Column("archived_at", String(40)),
    Column("legacy", Boolean, nullable=False, server_default="0"),
    Column("legacy_state", String(20)),
    scoped_fk("run_id", "runs"),
    scoped_fk("message_id", "messages"),
    scoped_fk("parent_id", "outbox"),
    ForeignKeyConstraint(
        ["config_id", "agent_id", "config_kind"],
        ["configurations.id", "configurations.agent_id", "configurations.kind"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["config_revision_id", "config_id", "agent_id"],
        ["configuration_revisions.id", "configuration_revisions.config_id", "configuration_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["latest_attempt_id", "id", "agent_id"],
        ["delivery_attempts.id", "delivery_attempts.delivery_id", "delivery_attempts.agent_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["last_review_id", "id", "agent_id"],
        ["delivery_reviews.id", "delivery_reviews.delivery_id", "delivery_reviews.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("agent_id", "key"),
    CheckConstraint(
        "state IN ('awaiting_approval','approved','claimed','sending','retry_wait','sent','unknown','cancelled','rejected','blocked')",
        name="state",
    ),
    CheckConstraint(
        "revision>0 AND outcome_revision>0 AND queue_sequence>0 AND attempt_seq BETWEEN 0 AND 3 AND review_epoch>0 AND reviewed_epoch BETWEEN 0 AND review_epoch AND length(body_hash)=64 AND length(fingerprint)=64 AND config_kind='channel' AND length(trim(target)) BETWEEN 1 AND 512 AND legacy IN (0,1)",
        name="bounds",
    ),
    CheckConstraint(
        "legacy=1 OR (config_id IS NOT NULL AND config_revision_id IS NOT NULL AND adapter_id IS NOT NULL AND account_key IS NOT NULL AND resource_key IS NOT NULL AND session_epoch>0 AND policy='delivery-policy-v1')",
        name="authority",
    ),
    CheckConstraint(
        "resolution IS NULL OR resolution IN ('confirmed_sent','confirmed_not_sent','allow_future')", name="resolution"
    ),
)
Index(
    "uq_configuration_revision_owner",
    configuration_revisions.c.id,
    configuration_revisions.c.config_id,
    configuration_revisions.c.agent_id,
    unique=True,
)
delivery_approvals = scoped(
    "delivery_approvals",
    Column("delivery_id", String(36), nullable=False, unique=True),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("approved_at", String(40)),
    Column("revoked_at", String(40)),
    scoped_fk("delivery_id", "outbox"),
    CheckConstraint(
        "revision>0 AND length(fingerprint)=64 AND state IN ('pending','approved','denied','revoked')", name="state"
    ),
)
delivery_attempts = scoped(
    "delivery_attempts",
    Column("delivery_id", String(36), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("token", String(36), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("started_at", String(40)),
    Column("ended_at", String(40)),
    Column("error_code", String(80), nullable=False, server_default=""),
    scoped_fk("delivery_id", "outbox"),
    UniqueConstraint("delivery_id", "seq"),
    UniqueConstraint("id", "delivery_id", "agent_id"),
    CheckConstraint(
        "seq BETWEEN 1 AND 3 AND fence>0 AND length(boot_id)=36 AND length(token)=36 AND state IN ('claimed','sending','sent','not_sent','unknown','cancelled')",
        name="state",
    ),
)
delivery_results = scoped(
    "delivery_results",
    Column("delivery_id", String(36), nullable=False),
    Column("attempt_id", String(36), nullable=False),
    Column("observation_key", String(100), nullable=False),
    Column("status", String(20), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("receipt_id", String(200), nullable=False),
    Column("error_code", String(80), nullable=False),
    Column("retryable", Boolean, nullable=False),
    Column("late", Boolean, nullable=False),
    ForeignKeyConstraint(
        ["attempt_id", "delivery_id", "agent_id"],
        ["delivery_attempts.id", "delivery_attempts.delivery_id", "delivery_attempts.agent_id"],
        ondelete="RESTRICT",
    ),
    scoped_fk("delivery_id", "outbox"),
    UniqueConstraint("attempt_id", "observation_key"),
    CheckConstraint(
        "status IN ('sent','not_sent','unknown') AND length(fingerprint)=64 AND (status!='sent' OR length(receipt_id)>0) AND (status='sent' OR receipt_id='') AND (retryable=0 OR status='not_sent') AND retryable IN (0,1) AND late IN (0,1)",
        name="result",
    ),
)
delivery_reviews = scoped(
    "delivery_reviews",
    Column("delivery_id", String(36), nullable=False),
    Column("attempt_id", String(36)),
    Column("review_epoch", Integer, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("decision", String(24), nullable=False),
    Column("evidence", String(2000), nullable=False),
    scoped_fk("delivery_id", "outbox"),
    ForeignKeyConstraint(
        ["attempt_id", "delivery_id", "agent_id"],
        ["delivery_attempts.id", "delivery_attempts.delivery_id", "delivery_attempts.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("id", "delivery_id", "agent_id"),
    CheckConstraint(
        "review_epoch>0 AND length(fingerprint)=64 AND length(trim(evidence)) BETWEEN 1 AND 2000 AND decision IN ('confirmed_sent','confirmed_not_sent','allow_future')",
        name="review",
    ),
)
dispatch_resources = Table(
    "dispatch_resources",
    metadata,
    Column("resource_key", String(64), primary_key=True),
    scope(),
    Column("attempt_id", String(36), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("token", String(36), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("state", String(20), nullable=False),
    scoped_fk("attempt_id", "delivery_attempts"),
    CheckConstraint(
        "fence>0 AND length(resource_key)=64 AND length(boot_id)=36 AND length(token)=36 AND state IN ('held','released')",
        name="bounds",
    ),
)
Index("ix_delivery_pending", outbox.c.state, outbox.c.queue_sequence)
Index("ix_delivery_account", outbox.c.resource_key, outbox.c.queue_sequence)
Index("ix_delivery_cursor", outbox.c.agent_id, outbox.c.queue_sequence)
Index(
    "ix_delivery_attempt_cursor", delivery_attempts.c.agent_id, delivery_attempts.c.delivery_id, delivery_attempts.c.seq
)
budget_accounts = Table(
    "budget_accounts",
    metadata,
    scope(),
    Column("day", String(10), nullable=False),
    Column("currency", String(3), nullable=False, default="USD"),
    Column("held_micro", Integer, nullable=False, default=0),
    Column("spent_micro", Integer, nullable=False, default=0),
    UniqueConstraint("agent_id", "day", "currency"),
    CheckConstraint("held_micro >= 0 AND spent_micro >= 0", name="nonnegative"),
)
model_calls = scoped(
    "model_calls",
    Column("run_id", String(36), nullable=False),
    Column("model", Text, nullable=False),
    Column("day", String(10), nullable=False),
    Column("state", String(20), nullable=False),
    Column("reserved_micro", Integer, nullable=False),
    Column("cost_micro", Integer),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("ended_at", String(40)),
    Column(
        "purpose",
        String(20),
        CheckConstraint("purpose IN ('turn','summary')", name="purpose"),
        nullable=False,
        server_default="turn",
    ),
    scoped_fk("run_id", "runs"),
    CheckConstraint("reserved_micro >= 0", name="nonnegative_reservation"),
    CheckConstraint("cost_micro IS NULL OR cost_micro >= 0", name="nonnegative_cost"),
    CheckConstraint("state IN ('reserved','settled','unknown','not_sent')", name="state"),
)
memory_entries = scoped(
    "memory_entries",
    Column("kind", String(30), nullable=False),
    Column("content", Text, nullable=False),
    Column("person_id", String(36)),
    Column("session_id", String(36)),
    Column("revision", Integer, nullable=False, default=1),
    Column("deleted_at", String(40)),
    Column(
        "content_hash",
        String(64),
        CheckConstraint(
            "length(content_hash) IN (0,64) AND revision > 0 AND (kind NOT IN ('profile','resident') OR (person_id IS NOT NULL AND session_id IS NULL AND length(content)<=8000))",
            name="document_scope",
        ),
        nullable=False,
        server_default="",
    ),
    scoped_fk("person_id", "people"),
    scoped_fk("session_id", "sessions"),
    CheckConstraint("kind IN ('fact','profile','resident','journal','note')", name="kind"),
)
source_anchors = scoped(
    "source_anchors",
    Column("memory_id", String(36), nullable=False),
    Column("memory_revision_id", String(36), nullable=False),
    Column(
        "source_hash",
        String(64),
        CheckConstraint("length(source_hash) IN (0,64)", name="source_hash"),
        nullable=False,
        server_default="",
    ),
    Column("message_id", String(36)),
    Column("action_id", String(36)),
    Column("object_id", String(36)),
    Column("manual_audit_id", String(36)),
    Column("document_revision_id", String(36)),
    Column("source_start", Integer),
    Column("source_end", Integer),
    scoped_fk("document_revision_id", "document_revisions"),
    Column("quote", Text, nullable=False),
    CheckConstraint(
        "(document_revision_id IS NULL AND source_start IS NULL AND source_end IS NULL) OR (document_revision_id IS NOT NULL AND source_start IS NOT NULL AND source_end IS NOT NULL AND source_start>=0 AND source_end>source_start AND length(quote)=source_end-source_start)",
        name="document_range",
    ),
    scoped_fk("memory_id", "memory_entries"),
    scoped_fk("message_id", "messages"),
    scoped_fk("action_id", "actions"),
    scoped_fk("object_id", "objects"),
    CheckConstraint(
        "(message_id IS NOT NULL) + (action_id IS NOT NULL) + (object_id IS NOT NULL) + (manual_audit_id IS NOT NULL) + (document_revision_id IS NOT NULL) = 1",
        name="exactly_one_source",
    ),
)
memory_revisions = scoped(
    "memory_revisions",
    Column("memory_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("content", Text, nullable=False),
    scoped_fk("memory_id", "memory_entries"),
    UniqueConstraint("memory_id", "revision"),
)
tombstones = scoped(
    "tombstones",
    Column("memory_id", String(36), nullable=False),
    Column("reason", Text, nullable=False),
    Column(
        "content_hash",
        String(64),
        CheckConstraint("length(content_hash) IN (0,64)", name="content_hash"),
        nullable=False,
        server_default="",
    ),
    scoped_fk("memory_id", "memory_entries"),
    UniqueConstraint("memory_id"),
)
tombstone_sources = scoped(
    "tombstone_sources",
    Column("tombstone_id", String(36), nullable=False),
    Column("source_hash", String(64), CheckConstraint("length(source_hash) = 64", name="source_hash"), nullable=False),
    scoped_fk("tombstone_id", "tombstones"),
    UniqueConstraint("tombstone_id", "source_hash"),
)
context_memories = scoped(
    "context_memories",
    Column("snapshot_id", String(36), nullable=False),
    Column("memory_id", String(36), nullable=False),
    Column("memory_revision_id", String(36)),
    Column("state", String(24), nullable=False),
    scoped_fk("snapshot_id", "context_snapshots"),
    scoped_fk("memory_id", "memory_entries"),
    ForeignKeyConstraint(
        ["memory_revision_id", "memory_id", "agent_id"],
        ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("snapshot_id", "memory_id"),
    CheckConstraint(
        "(state='captured' AND memory_revision_id IS NOT NULL) OR (state='legacy_unresolved' AND memory_revision_id IS NULL)",
        name="resolution",
    ),
)
schedules = scoped(
    "schedules",
    Column("name", String(100), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("deleted_at", String(40)),
    CheckConstraint("revision>0 AND enabled IN (0,1) AND length(trim(name)) BETWEEN 1 AND 100", name="bounds"),
    ForeignKeyConstraint(
        ["id", "revision", "agent_id"],
        ["schedule_revisions.schedule_id", "schedule_revisions.revision", "schedule_revisions.agent_id"],
        deferrable=True,
        initially="DEFERRED",
    ),
)
schedule_revisions = scoped(
    "schedule_revisions",
    Column("schedule_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("name", String(100), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("kind", String(20), nullable=False),
    Column("timezone", String(100), nullable=False),
    Column("tzdb_version", String(100), nullable=False),
    Column("expression", String(100), nullable=False),
    Column("interval_seconds", Integer),
    Column("start_at", String(40)),
    Column("session_id", String(36), nullable=False),
    Column("prompt", Text, nullable=False),
    Column("misfire", String(20), nullable=False),
    scoped_fk("schedule_id", "schedules"),
    scoped_fk("session_id", "sessions"),
    UniqueConstraint("schedule_id", "revision", "agent_id"),
    UniqueConstraint("id", "schedule_id", "agent_id"),
    CheckConstraint(
        "revision>0 AND length(prompt) BETWEEN 1 AND 20000 AND misfire='latest' AND length(tzdb_version)>0",
        name="bounds",
    ),
    CheckConstraint(
        "(kind='cron' AND interval_seconds IS NULL AND start_at IS NULL) OR (kind='interval' AND expression='' AND interval_seconds BETWEEN 60 AND 31536000 AND start_at IS NOT NULL) OR (kind='once' AND expression='' AND interval_seconds IS NULL AND start_at IS NOT NULL)",
        name="kind",
    ),
)
schedule_progress = Table(
    "schedule_progress",
    metadata,
    Column("schedule_id", String(36), primary_key=True),
    scope(),
    Column("revision_id", String(36), nullable=False),
    Column("next_at", String(40)),
    Column("last_scheduled_at", String(40)),
    Column("sequence", Integer, nullable=False),
    Column("reviewed_sequence", Integer, nullable=False),
    Column("review_epoch", Integer, nullable=False, server_default="1"),
    Column("revision", Integer, nullable=False),
    Column("error_code", String(100), nullable=False),
    Column("updated_at", String(40), nullable=False),
    scoped_fk("schedule_id", "schedules"),
    ForeignKeyConstraint(
        ["revision_id", "schedule_id", "agent_id"],
        ["schedule_revisions.id", "schedule_revisions.schedule_id", "schedule_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "revision>0 AND sequence>=0 AND reviewed_sequence>=0 AND reviewed_sequence<=sequence AND review_epoch>0",
        name="bounds",
    ),
)
occurrences = scoped(
    "occurrences",
    Column("schedule_id", String(36), nullable=False),
    Column("revision_id", String(36), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("scheduled_at", String(40), nullable=False),
    Column("local_time", String(48), nullable=False),
    Column("coalesced_from", String(40)),
    Column("state", String(20), nullable=False),
    Column("error_code", String(100), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("run_id", String(36), unique=True),
    scoped_fk("schedule_id", "schedules"),
    scoped_fk("run_id", "runs"),
    ForeignKeyConstraint(
        ["revision_id", "schedule_id", "agent_id"],
        ["schedule_revisions.id", "schedule_revisions.schedule_id", "schedule_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("schedule_id", "scheduled_at"),
    UniqueConstraint("schedule_id", "sequence"),
    CheckConstraint(
        "sequence>0 AND state IN ('pending','enqueued','blocked','cancelled','legacy') AND (state!='enqueued' OR run_id IS NOT NULL)",
        name="state",
    ),
)
schedule_reviews = scoped(
    "schedule_reviews",
    Column("occurrence_id", String(36), nullable=False),
    Column("model_call_id", String(36)),
    Column("action_id", String(36)),
    Column("delivery_id", String(36)),
    Column("delivery_revision", Integer),
    Column("state", String(20), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("review_epoch", Integer, nullable=False),
    scoped_fk("occurrence_id", "occurrences"),
    scoped_fk("model_call_id", "model_calls"),
    scoped_fk("action_id", "actions"),
    scoped_fk("delivery_id", "outbox"),
    CheckConstraint(
        "(model_call_id IS NOT NULL)+(action_id IS NOT NULL)+(delivery_id IS NOT NULL)=1 AND length(fingerprint)=64 AND review_epoch>0 AND state IN ('reserved','unknown','executing','sending')",
        name="intent",
    ),
    UniqueConstraint("occurrence_id", "model_call_id", "state", "review_epoch"),
    UniqueConstraint("occurrence_id", "action_id", "state", "review_epoch"),
    UniqueConstraint("occurrence_id", "delivery_id", "state", "review_epoch", "delivery_revision"),
)
leases = Table(
    "leases",
    metadata,
    Column("agent_id", String(36), ForeignKey("agents.id", ondelete="RESTRICT"), primary_key=True),
    Column("resource", String(200), primary_key=True),
    Column("token", String(36), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("state", String(20), nullable=False),
    Column("run_id", String(36)),
    scoped_fk("run_id", "runs"),
    CheckConstraint(
        "fence>0 AND length(token)=36 AND length(boot_id)=36 AND length(resource) BETWEEN 1 AND 200 AND state IN ('held','released')",
        name="bounds",
    ),
)
Index("ix_schedule_due", schedule_progress.c.next_at, schedule_progress.c.schedule_id)
Index("ix_schedule_cursor", schedules.c.agent_id, schedules.c.id)
Index("ix_occurrence_cursor", occurrences.c.agent_id, occurrences.c.schedule_id, occurrences.c.sequence)
Index("ix_lease_owner", leases.c.boot_id, leases.c.state, leases.c.expires_at)
Index("ix_schedule_call_state", model_calls.c.run_id, model_calls.c.state)
Index("ix_schedule_action_state", actions.c.run_id, actions.c.state)
Index("ix_schedule_delivery_state", outbox.c.run_id, outbox.c.state)
Index("ix_run_session_state", runs.c.session_id, runs.c.state)

extensions = Table(
    "extensions",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("id", String(36), CheckConstraint("length(id)=36", name="uuid_length"), nullable=False),
    scope(),
    stamp(),
    Column("name", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("authority_epoch", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False, default=False),
    Column("state", String(20), nullable=False),
    Column("current_revision_id", String(36), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("error_code", String(80), nullable=False, server_default=""),
    UniqueConstraint("id"),
    UniqueConstraint("id", "agent_id"),
    UniqueConstraint("agent_id", "name"),
    ForeignKeyConstraint(
        ["current_revision_id", "id", "agent_id"],
        ["extension_revisions.id", "extension_revisions.extension_id", "extension_revisions.agent_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint(
        "sequence>0 AND length(name) BETWEEN 1 AND 32 AND name GLOB '[a-z]*' AND name NOT GLOB '*[^a-z0-9_]*' AND length(error_code)<=80",
        name="identity",
    ),
    CheckConstraint(
        "typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND typeof(authority_epoch)='integer' AND authority_epoch BETWEEN 1 AND 9223372036854775807",
        name="revision",
    ),
    CheckConstraint(
        "enabled IN (0,1) AND ((enabled=1 AND state='enabled') OR (enabled=0 AND state IN ('disabled','drifted','missing')))",
        name="state",
    ),
    sqlite_autoincrement=True,
)
Index("ix_extensions_agent_sequence", extensions.c.agent_id, extensions.c.sequence)
grants = scoped(
    "grants",
    Column("extension_id", String(36), nullable=False),
    Column("extension_revision_id", String(36), nullable=False),
    Column("tool_id", String(36), nullable=False),
    Column("authority_epoch", Integer, nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("revoked_at", String(40)),
    Column("review_audit_id", String(36), nullable=False),
    ForeignKeyConstraint(
        ["tool_id", "extension_revision_id", "extension_id", "agent_id"],
        [
            "extension_tools.id",
            "extension_tools.extension_revision_id",
            "extension_tools.extension_id",
            "extension_tools.agent_id",
        ],
        ondelete="RESTRICT",
    ),
    scoped_fk("review_audit_id", "audit"),
    UniqueConstraint("extension_id", "authority_epoch", "tool_id"),
    UniqueConstraint("id", "tool_id", "authority_epoch", "agent_id"),
    CheckConstraint(
        "typeof(authority_epoch)='integer' AND authority_epoch BETWEEN 1 AND 9223372036854775807 AND length(history_epoch)=36",
        name="authority",
    ),
)
Index("ix_grants_review_audit", grants.c.agent_id, grants.c.review_audit_id)
skill_revisions = scoped(
    "skill_revisions",
    Column("name", String(100), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("object_id", String(36), nullable=False),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("agent_id", "name", "revision"),
)
auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    stamp(),
    Column("expires_at", String(40), nullable=False),
    Column("revoked_at", String(40)),
)
vault = Table(
    "vault",
    metadata,
    Column("reference", String(200), primary_key=True),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("revision", Integer, nullable=False),
)
Index("ix_events_run_cursor", run_events.c.agent_id, run_events.c.run_id, run_events.c.seq)
Index("ix_runs_session", runs.c.agent_id, runs.c.session_id, runs.c.created_at)
Index("ix_work_pending", work_items.c.state, work_items.c.created_at)
Index("ix_memory_live", memory_entries.c.agent_id, memory_entries.c.kind, memory_entries.c.deleted_at)
Index("ix_objects_agent_cursor", objects.c.agent_id, objects.c.id)
Index("ix_message_parts_object", message_parts.c.agent_id, message_parts.c.object_id)
Index("ix_source_anchors_object", source_anchors.c.agent_id, source_anchors.c.object_id)
Index("ix_skill_revisions_object", skill_revisions.c.agent_id, skill_revisions.c.object_id)
Index("ix_action_objects_object", action_objects.c.agent_id, action_objects.c.object_id)
Index("uq_people_owner", people.c.agent_id, people.c.owner_id, unique=True)
Index("ix_people_cursor", people.c.agent_id, people.c.deleted_at, people.c.id)
Index(
    "uq_memory_document",
    memory_entries.c.agent_id,
    memory_entries.c.person_id,
    memory_entries.c.kind,
    unique=True,
    sqlite_where=(memory_entries.c.deleted_at.is_(None) & memory_entries.c.kind.in_(["profile", "resident"])),
)
Index("ix_memory_person", memory_entries.c.agent_id, memory_entries.c.person_id, memory_entries.c.deleted_at)
Index(
    "uq_memory_revision_identity",
    memory_revisions.c.id,
    memory_revisions.c.memory_id,
    memory_revisions.c.agent_id,
    unique=True,
)
Index("ix_tombstones_content", tombstones.c.agent_id, tombstones.c.content_hash)
Index("ix_tombstone_sources_hash", tombstone_sources.c.agent_id, tombstone_sources.c.source_hash)
Index("ix_context_memories_snapshot", context_memories.c.agent_id, context_memories.c.snapshot_id)
messages.append_constraint(scoped_fk("run_id", "runs"))
messages.append_constraint(UniqueConstraint("id", "session_id", "agent_id"))
Index(
    "ix_summaries_ready",
    summaries.c.agent_id,
    summaries.c.session_id,
    summaries.c.session_epoch,
    summaries.c.state,
    summaries.c.last_seq,
)
Index("ix_summary_messages_source", summary_messages.c.agent_id, summary_messages.c.message_id)
Index("ix_context_messages_snapshot", context_messages.c.agent_id, context_messages.c.snapshot_id)
audit.append_constraint(UniqueConstraint("id", "agent_id"))
source_anchors.append_constraint(ForeignKeyConstraint(["manual_audit_id", "agent_id"], ["audit.id", "audit.agent_id"]))
source_anchors.append_constraint(
    ForeignKeyConstraint(
        ["memory_revision_id", "memory_id", "agent_id"],
        ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.agent_id"],
        name="fk_source_anchors_revision",
        ondelete="RESTRICT",
    )
)


source_documents = scoped(
    "source_documents",
    Column("title", String(200), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("deleted_at", String(40)),
    CheckConstraint("revision>0 AND length(title) BETWEEN 1 AND 200", name="bounds"),
    ForeignKeyConstraint(
        ["id", "revision", "agent_id"],
        ["document_revisions.document_id", "document_revisions.revision", "document_revisions.agent_id"],
        deferrable=True,
        initially="DEFERRED",
    ),
)
document_revisions = scoped(
    "document_revisions",
    Column("document_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("title", String(200), nullable=False),
    Column("object_id", String(36)),
    Column("sha256", String(64), nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("chars", Integer, nullable=False),
    Column("authored_audit_id", String(36), nullable=False),
    Column("redacted_at", String(40)),
    Column("text_index_version", String(100), nullable=False),
    Column("active_vector_build_id", String(36)),
    scoped_fk("document_id", "source_documents"),
    scoped_fk("object_id", "objects"),
    ForeignKeyConstraint(["authored_audit_id", "agent_id"], ["audit.id", "audit.agent_id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["active_vector_build_id", "id", "agent_id"],
        ["document_index_builds.id", "document_index_builds.revision_id", "document_index_builds.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("document_id", "revision", "agent_id"),
    UniqueConstraint("id", "document_id", "agent_id"),
    CheckConstraint(
        "revision>0 AND length(sha256)=64 AND bytes BETWEEN 1 AND 262144 AND chars BETWEEN 1 AND 100000 AND ((redacted_at IS NULL AND object_id IS NOT NULL) OR (redacted_at IS NOT NULL AND object_id IS NULL))",
        name="bounds",
    ),
)
document_chunks = scoped(
    "document_chunks",
    Column("revision_id", String(36), nullable=False),
    Column("document_id", String(36), nullable=False),
    Column("position", Integer, nullable=False),
    Column("start", Integer, nullable=False),
    Column("end", Integer, nullable=False),
    Column("content", Text, nullable=False),
    ForeignKeyConstraint(
        ["revision_id", "document_id", "agent_id"],
        ["document_revisions.id", "document_revisions.document_id", "document_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("revision_id", "position"),
    CheckConstraint(
        "position BETWEEN 0 AND 99 AND start>=0 AND end>start AND end<=100000 AND length(content)=end-start AND length(content)<=1000",
        name="bounds",
    ),
)
document_index_builds = scoped(
    "document_index_builds",
    Column("revision_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("version", String(100), nullable=False),
    Column("dimension", Integer),
    Column("state", String(20), nullable=False),
    Column("error_code", String(80), nullable=False),
    Column("completed_at", String(40)),
    scoped_fk("revision_id", "document_revisions"),
    UniqueConstraint("id", "revision_id", "agent_id"),
    CheckConstraint("state IN ('building','ready','failed','invalidated')", name="state"),
    CheckConstraint(
        "length(version) BETWEEN 1 AND 100 AND ((kind='full_text' AND dimension IS NULL) OR (kind='vector' AND dimension IS NOT NULL AND dimension BETWEEN 1 AND 512))",
        name="kind",
    ),
)
document_vectors = Table(
    "document_vectors",
    metadata,
    Column("build_id", String(36), primary_key=True),
    Column("chunk_id", String(36), primary_key=True),
    scope(),
    Column("embedding", JSON, nullable=False),
    ForeignKeyConstraint(
        ["build_id", "agent_id"], ["document_index_builds.id", "document_index_builds.agent_id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(
        ["chunk_id", "agent_id"], ["document_chunks.id", "document_chunks.agent_id"], ondelete="RESTRICT"
    ),
    CheckConstraint("json_array_length(embedding) BETWEEN 1 AND 512", name="dimension"),
)
context_documents = scoped(
    "context_documents",
    Column("snapshot_id", String(36), nullable=False),
    Column("document_revision_id", String(36), nullable=False),
    Column("action_id", String(36), nullable=False),
    Column("source_start", Integer, nullable=False),
    Column("source_end", Integer, nullable=False),
    CheckConstraint("source_start>=0 AND source_end>source_start AND source_end<=100000", name="range"),
    scoped_fk("snapshot_id", "context_snapshots"),
    scoped_fk("document_revision_id", "document_revisions"),
    scoped_fk("action_id", "actions"),
    UniqueConstraint("action_id", "document_revision_id", "source_start", "source_end"),
)
Index("ix_documents_cursor", source_documents.c.agent_id, source_documents.c.id)
Index(
    "ix_document_chunks_revision", document_chunks.c.agent_id, document_chunks.c.revision_id, document_chunks.c.position
)
Index("ix_document_reads_revision", context_documents.c.agent_id, context_documents.c.document_revision_id)
Index("ix_source_anchors_document", source_anchors.c.agent_id, source_anchors.c.document_revision_id)
Index("ix_document_revisions_object", document_revisions.c.agent_id, document_revisions.c.object_id)

document_erasure_jobs = scoped(
    "document_erasure_jobs",
    Column("document_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("error_code", String(80), nullable=False),
    Column("updated_at", String(40), nullable=False),
    scoped_fk("document_id", "source_documents"),
    UniqueConstraint("document_id", "revision"),
    CheckConstraint("revision>0 AND attempt>0 AND state IN ('pending','done','failed')", name="bounds"),
)

Index("uq_context_snapshots_ordinal", context_snapshots.c.run_id, context_snapshots.c.ordinal, unique=True)

runs.append_column(Column("settings_revision_id", String(36)))
runs.append_column(
    Column(
        "execution_kind",
        String(12),
        CheckConstraint("execution_kind IN ('model','owner')", name="execution_kind"),
        nullable=False,
        server_default="model",
    )
)
runs.append_column(Column("model_revision_id", String(36)))
runs.append_column(Column("model_permission_epoch", Integer))
runs.append_constraint(ForeignKeyConstraint(["settings_revision_id"], ["settings_revisions.id"], ondelete="RESTRICT"))
runs.append_constraint(ForeignKeyConstraint(["model_revision_id"], ["configuration_revisions.id"], ondelete="RESTRICT"))
model_calls.append_column(
    Column(
        "input_token_estimate",
        Integer,
        CheckConstraint("input_token_estimate IS NULL OR input_token_estimate>=0", name="token_estimate"),
    )
)
model_calls.append_column(Column("counting_method", String(20), nullable=False, server_default="unmeasured"))
model_calls.append_column(Column("counter_id", String(100), nullable=False, server_default=""))
model_calls.append_column(Column("output_token_limit", Integer))
model_calls.append_column(Column("capacity_tokens", Integer))
context_snapshots.append_column(Column("input_token_estimate", Integer))
context_snapshots.append_column(Column("counting_method", String(20), nullable=False, server_default="unmeasured"))
context_snapshots.append_column(Column("counter_id", String(100), nullable=False, server_default=""))
context_snapshots.append_column(Column("output_token_limit", Integer))

runs.append_column(Column("policy_epoch", Integer))

settings.append_constraint(
    ForeignKeyConstraint(
        ["agent_id", "revision"],
        ["settings_revisions.agent_id", "settings_revisions.revision"],
        deferrable=True,
        initially="DEFERRED",
    )
)

instances.append_column(
    Column(
        "next_work_sequence",
        Integer,
        CheckConstraint("next_work_sequence>=0", name="work_sequence"),
        nullable=False,
        server_default="0",
    )
)

credential_references = Table(
    "credential_references",
    metadata,
    ident(),
    stamp(),
    Column("owner_id", String(36), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False),
    Column("reference", String(200), unique=True),
    Column("reference_sha256", String(64), nullable=False, unique=True),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("authority_epoch", Integer, nullable=False, server_default="1"),
    Column("current_operation_id", String(36)),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("id", "owner_id"),
    CheckConstraint(
        "state IN ('ready','changing','unknown','revoked') AND revision>0 AND authority_epoch>0 AND length(reference_sha256)=64",
        name="state",
    ),
    CheckConstraint("reference IS NULL OR length(reference) BETWEEN 1 AND 200", name="reference"),
    CheckConstraint("state!='ready' OR reference IS NOT NULL", name="ready_reference"),
    CheckConstraint("state!='changing' OR current_operation_id IS NOT NULL", name="changing_operation"),
)
credential_operations = Table(
    "credential_operations",
    metadata,
    ident(),
    stamp(),
    Column("owner_id", String(36), nullable=False),
    Column("credential_id", String(36), nullable=False),
    Column("operation", String(20), nullable=False),
    Column("state", String(20), nullable=False),
    Column("prior_state", String(20), nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("authority_epoch", Integer, nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("execution_token", String(36), nullable=False),
    Column("started_at", String(40)),
    Column("ended_at", String(40)),
    Column("error_code", String(100), nullable=False, server_default=""),
    Column("legacy", Boolean, nullable=False, server_default="0"),
    UniqueConstraint("id", "credential_id", "owner_id"),
    ForeignKeyConstraint(
        ["credential_id", "owner_id"],
        ["credential_references.id", "credential_references.owner_id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "operation IN ('set','delete','legacy') AND state IN ('prepared','running','applied','not_applied','unknown') AND prior_state IN ('ready','revoked','unknown') AND revision>0 AND authority_epoch>0 AND length(boot_id)=36 AND length(execution_token)=36 AND legacy IN (0,1)",
        name="state",
    ),
    CheckConstraint("legacy=0 OR state IN ('applied','not_applied','unknown')", name="legacy_terminal"),
)
credential_references.append_constraint(
    ForeignKeyConstraint(
        ["current_operation_id", "id", "owner_id"],
        ["credential_operations.id", "credential_operations.credential_id", "credential_operations.owner_id"],
        ondelete="RESTRICT",
    )
)
credential_reviews = Table(
    "credential_reviews",
    metadata,
    ident(),
    stamp(),
    Column("owner_id", String(36), nullable=False),
    Column("credential_id", String(36), nullable=False),
    Column("operation_id", String(36)),
    Column("reference_revision", Integer, nullable=False),
    Column("authority_epoch", Integer, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("policy", String(40), nullable=False),
    Column("decision", String(20), nullable=False),
    Column("evidence", Text, nullable=False),
    UniqueConstraint("credential_id", "authority_epoch", "reference_revision"),
    ForeignKeyConstraint(
        ["credential_id", "owner_id"],
        ["credential_references.id", "credential_references.owner_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["operation_id", "credential_id", "owner_id"],
        ["credential_operations.id", "credential_operations.credential_id", "credential_operations.owner_id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "reference_revision>0 AND authority_epoch>0 AND length(fingerprint)=64 AND policy='credential-authority-v1' AND decision IN ('allow_current','revoke') AND length(trim(evidence)) BETWEEN 1 AND 2000",
        name="review",
    ),
)
Index("ix_credential_references_state", credential_references.c.state)
Index("ix_credential_operations_state", credential_operations.c.state, credential_operations.c.boot_id)

runs.append_column(Column("retention_pinned", Boolean, nullable=False, server_default="0"))
runs.append_column(Column("retention_revision", Integer, nullable=False, server_default="1"))
runs.append_constraint(CheckConstraint("retention_pinned IN (0,1) AND retention_revision>0", name="retention"))
Index("ix_runs_retention", runs.c.ended_at, runs.c.id)

retention_batches = Table(
    "retention_batches",
    metadata,
    ident(),
    stamp(),
    Column("instance_id", String(36), ForeignKey("instances.id"), nullable=False),
    Column("plan_sha256", String(64), nullable=False),
    Column("policy", String(40), nullable=False),
    Column("cutoff", String(40), nullable=False),
    Column("schema_version", String(40), nullable=False),
    Column("backup_path", Text, nullable=False),
    Column("backup_sha256", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("completed_at", String(40)),
    CheckConstraint(
        "length(plan_sha256)=64 AND length(backup_sha256)=64 AND policy='execution-body-v1'", name="identity"
    ),
    CheckConstraint(
        "(state='prepared' AND completed_at IS NULL) OR (state IN ('complete','cancelled') AND completed_at IS NOT NULL)",
        name="state",
    ),
)
retention_targets = Table(
    "retention_targets",
    metadata,
    Column("batch_id", String(36), ForeignKey("retention_batches.id"), primary_key=True),
    scope(),
    Column("run_id", String(36), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("retention_revision", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("pruned_at", String(40)),
    scoped_fk("run_id", "runs"),
    CheckConstraint("length(fingerprint)=64 AND retention_revision>0", name="identity"),
    CheckConstraint(
        "(state IN ('pending','cancelled') AND pruned_at IS NULL) OR (state='pruned' AND pruned_at IS NOT NULL)",
        name="state",
    ),
)
Index(
    "ix_retention_targets_pending_run",
    retention_targets.c.run_id,
    unique=True,
    sqlite_where=retention_targets.c.state == "pending",
)
runs.append_column(Column("body_pruned_at", String(40)))
runs.append_column(Column("body_pruned_plan_id", String(36), ForeignKey("retention_batches.id")))
runs.append_column(Column("body_sha256", String(64)))
runs.append_constraint(
    CheckConstraint(
        "(body_pruned_at IS NULL AND body_pruned_plan_id IS NULL AND body_sha256 IS NULL) OR (body_pruned_at IS NOT NULL AND body_pruned_plan_id IS NOT NULL AND body_sha256 IS NOT NULL AND length(body_sha256)=64)",
        name="body_pruned",
    )
)
for table, digest in ((actions, "result_sha256"), (run_events, "data_sha256")):
    table.append_column(Column("body_pruned_at", String(40)))
    table.append_column(Column(digest, String(64)))
    table.append_constraint(
        CheckConstraint(
            f"(body_pruned_at IS NULL AND {digest} IS NULL) OR (body_pruned_at IS NOT NULL AND {digest} IS NOT NULL AND length({digest})=64)",
            name="body_pruned",
        )
    )

reference_release_batches = Table(
    "reference_release_batches",
    metadata,
    ident(),
    stamp(),
    Column("instance_id", String(36), ForeignKey("instances.id"), nullable=False),
    Column("plan_sha256", String(64), nullable=False),
    Column("policy", String(48), nullable=False),
    Column("schema_version", String(40), nullable=False),
    Column("backup_path", Text, nullable=False),
    Column("backup_sha256", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("completed_at", String(40)),
    CheckConstraint(
        "length(plan_sha256)=64 AND length(backup_sha256)=64 AND policy='execution-artifact-reference-v1'",
        name="identity",
    ),
    CheckConstraint(
        "(state='prepared' AND completed_at IS NULL) OR (state IN ('complete','cancelled') AND completed_at IS NOT NULL)",
        name="state",
    ),
)
reference_release_targets = Table(
    "reference_release_targets",
    metadata,
    Column("batch_id", String(36), ForeignKey("reference_release_batches.id"), primary_key=True),
    Column("reference_id", String(36), primary_key=True),
    scope(),
    Column("run_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    Column("action_id", String(36), nullable=False),
    scoped_fk("action_id", "actions"),
    # Historical identities deliberately do not keep objects/action_objects alive.
    Column("object_id", String(36), nullable=False),
    Column("object_revision", Integer, nullable=False),
    Column("object_sha256", String(64), nullable=False),
    Column("object_bytes", BigInteger, nullable=False),
    Column("prune_plan_id", String(36), ForeignKey("retention_batches.id"), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("ended_at", String(40)),
    CheckConstraint(
        "length(reference_id)=36 AND length(object_id)=36 AND object_revision>0 AND length(fingerprint)=64 "
        "AND length(object_sha256)=64 AND object_bytes>=0",
        name="identity",
    ),
    CheckConstraint(
        "(state='pending' AND ended_at IS NULL) OR (state IN ('released','cancelled') AND ended_at IS NOT NULL)",
        name="state",
    ),
)
Index(
    "ix_reference_release_pending",
    reference_release_targets.c.reference_id,
    unique=True,
    sqlite_where=reference_release_targets.c.state == "pending",
)

history_identity = Table(
    "history_identity",
    metadata,
    Column("slot", Integer, primary_key=True, nullable=False),
    Column("epoch", String(36), nullable=False),
    CheckConstraint("slot=1 AND length(epoch)=36", name="identity"),
)
history_sequences = Table(
    "history_sequences",
    metadata,
    Column("kind", String(20), primary_key=True, nullable=False),
    Column("last_issued", BigInteger, nullable=False),
    CheckConstraint("kind IN ('events','audit') AND last_issued BETWEEN 0 AND 9223372036854775807", name="sequence"),
)
history_scopes = Table(
    "history_scopes",
    metadata,
    Column("kind", String(20), primary_key=True, nullable=False),
    Column("scope_id", String(36), primary_key=True, nullable=False),
    Column("agent_id", String(36), ForeignKey("agents.id", ondelete="RESTRICT")),
    Column("instance_id", String(36), ForeignKey("instances.id", ondelete="RESTRICT")),
    Column("trim_revision", BigInteger, nullable=False),
    Column("deleted_count", BigInteger, nullable=False),
    Column("last_trim_at", String(40)),
    CheckConstraint(
        "(kind IN ('events','audit') AND agent_id IS NOT NULL AND scope_id=agent_id AND instance_id IS NULL) OR "
        "(kind='instance_audit' AND instance_id IS NOT NULL AND scope_id=instance_id AND agent_id IS NULL)",
        name="scope",
    ),
    CheckConstraint(
        "trim_revision>=0 AND deleted_count>=0 AND "
        "((trim_revision=0 AND deleted_count=0 AND last_trim_at IS NULL) OR "
        "(trim_revision>0 AND deleted_count>0 AND last_trim_at IS NOT NULL))",
        name="progress",
    ),
)


def history_retention_batches(name: str, policy: str) -> Table:
    table = scoped(
        name,
        Column("instance_id", String(36), ForeignKey("instances.id"), nullable=False),
        Column("plan_sha256", String(64), nullable=False),
        Column("policy", String(48), nullable=False),
        Column("schema_version", String(40), nullable=False),
        Column("as_of", String(40), nullable=False),
        Column("cutoff", String(40), nullable=False),
        Column("epoch", String(36), nullable=False),
        Column("trim_revision", BigInteger, nullable=False),
        Column("high_water", BigInteger, nullable=False),
        Column("backup_path", Text, nullable=False),
        Column("backup_sha256", String(64), nullable=False),
        Column("state", String(20), nullable=False),
        Column("completed_at", String(40)),
        CheckConstraint(
            f"length(plan_sha256)=64 AND length(backup_sha256)=64 AND length(epoch)=36 AND policy='{policy}'",
            name="identity",
        ),
        CheckConstraint(
            "typeof(trim_revision)='integer' AND trim_revision BETWEEN 0 AND 9223372036854775807 "
            "AND typeof(high_water)='integer' AND high_water BETWEEN 0 AND 9223372036854775807",
            name="bounds",
        ),
        CheckConstraint(
            "(state='prepared' AND completed_at IS NULL) OR "
            "(state IN ('complete','cancelled') AND completed_at IS NOT NULL)",
            name="state",
        ),
    )
    Index(
        f"ix_{name.removesuffix('_batches')}_prepared_agent",
        table.c.agent_id,
        unique=True,
        sqlite_where=table.c.state == "prepared",
    )
    return table


audit_retention_batches = history_retention_batches("audit_retention_batches", "agent-audit-retention-v1")
audit_retention_targets = Table(
    "audit_retention_targets",
    metadata,
    Column("batch_id", String(36), primary_key=True),
    # Historical audit and subject identities never retain rows selected for deletion.
    Column("audit_id", String(36), primary_key=True),
    scope(),
    Column("sequence", BigInteger, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("operation_id", String(36), nullable=False),
    Column("subject_kind", String(20), nullable=False),
    Column("run_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    Column("run_retention_revision", BigInteger, nullable=False),
    Column("prune_plan_id", String(36), ForeignKey("retention_batches.id"), nullable=False),
    Column("body_sha256", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("ended_at", String(40)),
    ForeignKeyConstraint(
        ["batch_id", "agent_id"],
        ["audit_retention_batches.id", "audit_retention_batches.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("batch_id", "sequence"),
    CheckConstraint(
        "length(audit_id)=36 AND length(operation_id)=36 AND length(body_sha256)=64 AND length(fingerprint)=64",
        name="identity",
    ),
    CheckConstraint(
        "typeof(sequence)='integer' AND sequence BETWEEN 1 AND 9223372036854775807 "
        "AND typeof(run_retention_revision)='integer' AND run_retention_revision BETWEEN 1 AND 9223372036854775807",
        name="bounds",
    ),
    CheckConstraint(
        "(subject_kind='run' AND kind='run.completed' AND operation_id=run_id) OR "
        "(subject_kind='action' AND kind IN ('action.ok','action.error','action.denied','action.cancelled'))",
        name="subject",
    ),
    CheckConstraint(
        "(state='pending' AND ended_at IS NULL) OR (state IN ('deleted','cancelled') AND ended_at IS NOT NULL)",
        name="state",
    ),
)
Index(
    "ix_audit_retention_pending_audit",
    audit_retention_targets.c.audit_id,
    unique=True,
    sqlite_where=audit_retention_targets.c.state == "pending",
)

event_retention_batches = history_retention_batches("event_retention_batches", "agent-event-retention-v1")
event_retention_targets = Table(
    "event_retention_targets",
    metadata,
    Column("batch_id", String(36), primary_key=True),
    # Historical global and per-run sequence identities do not retain event rows.
    Column("sequence", BigInteger, primary_key=True),
    scope(),
    Column("run_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    Column("seq", BigInteger, nullable=False),
    Column("kind", String(80), nullable=False),
    Column("run_retention_revision", BigInteger, nullable=False),
    Column("prune_plan_id", String(36), ForeignKey("retention_batches.id"), nullable=False),
    Column("body_sha256", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("ended_at", String(40)),
    ForeignKeyConstraint(
        ["batch_id", "agent_id"],
        ["event_retention_batches.id", "event_retention_batches.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("batch_id", "run_id", "seq"),
    CheckConstraint("length(body_sha256)=64 AND length(fingerprint)=64", name="identity"),
    CheckConstraint(
        "typeof(sequence)='integer' AND sequence BETWEEN 1 AND 9223372036854775807 "
        "AND typeof(seq)='integer' AND seq BETWEEN 1 AND 9223372036854775807 "
        "AND typeof(run_retention_revision)='integer' AND run_retention_revision BETWEEN 1 AND 9223372036854775807",
        name="bounds",
    ),
    CheckConstraint(
        "kind IN ('run.queued','run.running','run.waiting_approval','run.cancelling','run.succeeded',"
        "'run.failed','run.cancelled','action.prepared','action.completed')",
        name="kind",
    ),
    CheckConstraint(
        "(state='pending' AND ended_at IS NULL) OR (state IN ('deleted','cancelled') AND ended_at IS NOT NULL)",
        name="state",
    ),
)
Index(
    "ix_event_retention_pending_sequence",
    event_retention_targets.c.sequence,
    unique=True,
    sqlite_where=event_retention_targets.c.state == "pending",
)

maintenance_jobs = Table(
    "maintenance_jobs",
    metadata,
    Column("sequence", Integer, primary_key=True),
    Column("id", String(36), CheckConstraint("length(id)=36", name="uuid_length"), nullable=False),
    scope(),
    stamp(),
    Column("kind", String(40), nullable=False),
    Column("document_id", String(36), nullable=False),
    scoped_fk("document_id", "source_documents"),
    Column("revision_id", String(36), nullable=False),
    scoped_fk("revision_id", "document_revisions"),
    Column("source_revision", Integer, nullable=False),
    Column("erasure_job_id", String(36)),
    scoped_fk("erasure_job_id", "document_erasure_jobs"),
    Column("request_key", String(200), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("index_version", String(80), nullable=False),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("cancel_requested_at", String(40)),
    Column("updated_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("error_code", String(80), nullable=False, server_default=""),
    Column("result_build_id", String(36)),
    scoped_fk("result_build_id", "document_index_builds"),
    UniqueConstraint("id", name="uq_maintenance_jobs_id"),
    UniqueConstraint("id", "agent_id", name="uq_maintenance_jobs_scope"),
    UniqueConstraint("agent_id", "request_key"),
    CheckConstraint(
        "sequence>0 AND length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64",
        name="identity",
    ),
    CheckConstraint(
        "typeof(source_revision)='integer' AND source_revision BETWEEN 1 AND 9223372036854775807 "
        "AND typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 "
        "AND typeof(attempt_count)='integer' AND attempt_count BETWEEN 0 AND 9223372036854775807",
        name="bounds",
    ),
    CheckConstraint(
        "(kind='document_index_rebuild' AND erasure_job_id IS NULL AND length(index_version) BETWEEN 1 AND 80) OR "
        "(kind='document_erasure_resume' AND erasure_job_id IS NOT NULL AND index_version='')",
        name="kind",
    ),
    CheckConstraint(
        "(state IN ('queued','claimed','running','cancelling') AND finished_at IS NULL) OR "
        "(state IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)",
        name="state",
    ),
    sqlite_autoincrement=True,
)
Index("ix_maintenance_jobs_agent_sequence", maintenance_jobs.c.agent_id, maintenance_jobs.c.sequence)
Index(
    "ix_maintenance_jobs_active_document",
    maintenance_jobs.c.agent_id,
    maintenance_jobs.c.document_id,
    unique=True,
    sqlite_where=maintenance_jobs.c.state.in_(["claimed", "running", "cancelling"]),
)
maintenance_attempts = scoped(
    "maintenance_attempts",
    Column("job_id", String(36), nullable=False),
    scoped_fk("job_id", "maintenance_jobs"),
    Column("attempt_no", Integer, nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("token", String(36), nullable=False, unique=True),
    Column("fence", Integer, nullable=False),
    Column("lease_until", String(40), nullable=False),
    Column("state", String(20), nullable=False),
    Column("started_at", String(40)),
    Column("ended_at", String(40)),
    Column("error_code", String(80), nullable=False, server_default=""),
    Column("build_id", String(36)),
    scoped_fk("build_id", "document_index_builds"),
    UniqueConstraint("job_id", "attempt_no"),
    CheckConstraint("length(boot_id)=36 AND length(token)=36", name="identity"),
    CheckConstraint(
        "typeof(attempt_no)='integer' AND attempt_no BETWEEN 1 AND 9223372036854775807 "
        "AND typeof(fence)='integer' AND fence BETWEEN 1 AND 9223372036854775807",
        name="bounds",
    ),
    CheckConstraint(
        "(state='claimed' AND started_at IS NULL AND ended_at IS NULL) OR "
        "(state='running' AND started_at IS NOT NULL AND ended_at IS NULL) OR "
        "(state='succeeded' AND started_at IS NOT NULL AND ended_at IS NOT NULL) OR "
        "(state IN ('failed','cancelled','interrupted') AND ended_at IS NOT NULL)",
        name="state",
    ),
)
Index(
    "ix_maintenance_attempts_active_job",
    maintenance_attempts.c.job_id,
    unique=True,
    sqlite_where=maintenance_attempts.c.state.in_(["claimed", "running"]),
)

memory_organization_batches = Table(
    "memory_organization_batches",
    metadata,
    Column("sequence", Integer, primary_key=True),
    Column("id", String(36), CheckConstraint("length(id)=36", name="uuid_length"), nullable=False),
    scope(),
    stamp(),
    Column("request_key", String(200), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("policy", String(48), nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("budget_micro", Integer, nullable=False),
    Column("source_count", Integer, nullable=False),
    Column("candidate_count", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("prepared_at", String(40)),
    Column("error_code", String(80), nullable=False, server_default=""),
    UniqueConstraint("id"),
    UniqueConstraint("id", "agent_id"),
    UniqueConstraint("agent_id", "request_key"),
    CheckConstraint(
        "sequence>0 AND length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64 AND "
        "policy='exact-fact-duplicates-v1' AND length(history_epoch)=36 AND length(error_code)<=80",
        name="identity",
    ),
    CheckConstraint(
        "typeof(budget_micro)='integer' AND budget_micro=0 AND "
        "typeof(source_count)='integer' AND source_count BETWEEN 2 AND 32 AND "
        "typeof(candidate_count)='integer' AND candidate_count BETWEEN 0 AND 31 AND candidate_count<source_count AND "
        "typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807",
        name="bounds",
    ),
    CheckConstraint(
        "state IN ('queued','ready','complete','cancelled','invalidated','failed') AND "
        "(state NOT IN ('ready','complete') OR prepared_at IS NOT NULL)",
        name="state",
    ),
    sqlite_autoincrement=True,
)
Index(
    "ix_memory_organization_batches_agent_sequence",
    memory_organization_batches.c.agent_id,
    memory_organization_batches.c.sequence,
)

memory_organization_sources = scoped(
    "memory_organization_sources",
    Column("batch_id", String(36), nullable=False),
    scoped_fk("batch_id", "memory_organization_batches"),
    Column("memory_id", String(36), nullable=False),
    scoped_fk("memory_id", "memory_entries"),
    Column("memory_revision_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("raw_sha256", String(64), nullable=False),
    Column("evidence_sha256", String(64), nullable=False),
    Column("person_id", String(36)),
    scoped_fk("person_id", "people"),
    Column("session_id", String(36)),
    scoped_fk("session_id", "sessions"),
    ForeignKeyConstraint(
        ["memory_revision_id", "memory_id", "agent_id"],
        ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("batch_id", "memory_id"),
    UniqueConstraint("batch_id", "position"),
    UniqueConstraint("id", "batch_id", "agent_id"),
    CheckConstraint(
        "typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND "
        "typeof(position)='integer' AND position BETWEEN 0 AND 31 AND length(raw_sha256)=64 AND length(evidence_sha256)=64",
        name="bounds",
    ),
)
Index(
    "ix_memory_organization_sources_memory",
    memory_organization_sources.c.agent_id,
    memory_organization_sources.c.memory_id,
)

memory_organization_candidates = scoped(
    "memory_organization_candidates",
    Column("batch_id", String(36), nullable=False),
    scoped_fk("batch_id", "memory_organization_batches"),
    Column("keep_source_id", String(36), nullable=False),
    Column("duplicate_source_id", String(36), nullable=False),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("reviewed_at", String(40)),
    Column("review_audit_id", String(36)),
    scoped_fk("review_audit_id", "audit"),
    Column("error_code", String(80), nullable=False, server_default=""),
    ForeignKeyConstraint(
        ["keep_source_id", "batch_id", "agent_id"],
        [
            "memory_organization_sources.id",
            "memory_organization_sources.batch_id",
            "memory_organization_sources.agent_id",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["duplicate_source_id", "batch_id", "agent_id"],
        [
            "memory_organization_sources.id",
            "memory_organization_sources.batch_id",
            "memory_organization_sources.agent_id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("batch_id", "duplicate_source_id"),
    CheckConstraint(
        "keep_source_id!=duplicate_source_id AND typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 "
        "AND length(fingerprint)=64 AND length(error_code)<=80",
        name="identity",
    ),
    CheckConstraint(
        "(state='pending' AND reviewed_at IS NULL AND review_audit_id IS NULL) OR "
        "(state IN ('applied','rejected','revoked') AND reviewed_at IS NOT NULL AND review_audit_id IS NOT NULL) OR "
        "(state IN ('invalidated','cancelled') AND ((reviewed_at IS NULL AND review_audit_id IS NULL) OR "
        "(reviewed_at IS NOT NULL AND review_audit_id IS NOT NULL)))",
        name="state",
    ),
)
for source in (memory_organization_candidates.c.keep_source_id, memory_organization_candidates.c.duplicate_source_id):
    Index(
        "ix_memory_organization_applied_" + source.name.removesuffix("_source_id"),
        memory_organization_candidates.c.agent_id,
        source,
        unique=False,
        sqlite_where=memory_organization_candidates.c.state == "applied",
    )
Index(
    "ix_memory_organization_review_audit",
    memory_organization_candidates.c.agent_id,
    memory_organization_candidates.c.review_audit_id,
)


extension_revisions = scoped(
    "extension_revisions",
    Column("extension_id", String(36), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("version", String(64), nullable=False),
    Column("protocol", String(40), nullable=False),
    Column("object_id", String(36), nullable=False),
    Column("package_sha256", String(64), nullable=False),
    Column("catalog_sha256", String(64), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("files", JSON, nullable=False),
    Column("entry", String(200), nullable=False),
    Column("file_count", Integer, nullable=False),
    Column("total_bytes", Integer, nullable=False),
    scoped_fk("extension_id", "extensions"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("extension_id", "ordinal"),
    UniqueConstraint("id", "extension_id", "agent_id"),
    CheckConstraint(
        "typeof(ordinal)='integer' AND ordinal BETWEEN 1 AND 9223372036854775807 AND length(version) BETWEEN 1 AND 64 AND protocol='omni-python-extension-ipc-1' AND length(entry) BETWEEN 1 AND 200",
        name="identity",
    ),
    CheckConstraint(
        "length(package_sha256)=64 AND package_sha256 NOT GLOB '*[^0-9a-f]*' AND length(catalog_sha256)=64 AND catalog_sha256 NOT GLOB '*[^0-9a-f]*'",
        name="fingerprints",
    ),
    CheckConstraint(
        "typeof(file_count)='integer' AND file_count BETWEEN 1 AND 64 AND typeof(total_bytes)='integer' AND total_bytes BETWEEN 0 AND 8388608",
        name="bounds",
    ),
    CheckConstraint(
        "json_valid(manifest) AND json_type(manifest)='object' AND json_valid(files) AND json_type(files)='array' AND json_array_length(files)=file_count",
        name="metadata",
    ),
)
Index("ix_extension_revisions_object", extension_revisions.c.agent_id, extension_revisions.c.object_id)
extension_tools = scoped(
    "extension_tools",
    Column("extension_id", String(36), nullable=False),
    Column("extension_revision_id", String(36), nullable=False),
    Column("name", String(100), nullable=False),
    Column("local_name", String(32), nullable=False),
    Column("description", String(512), nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("timeout_s", Integer, nullable=False),
    Column("writes", Boolean, nullable=False),
    Column("spec_sha256", String(64), nullable=False),
    ForeignKeyConstraint(
        ["extension_revision_id", "extension_id", "agent_id"],
        ["extension_revisions.id", "extension_revisions.extension_id", "extension_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("extension_revision_id", "name"),
    UniqueConstraint("extension_revision_id", "local_name"),
    UniqueConstraint("id", "extension_revision_id", "extension_id", "agent_id"),
    CheckConstraint(
        "length(name) BETWEEN 1 AND 64 AND length(local_name) BETWEEN 1 AND 32 AND local_name GLOB '[a-z]*' AND local_name NOT GLOB '*[^a-z0-9_]*' AND length(description) BETWEEN 1 AND 512",
        name="identity",
    ),
    CheckConstraint(
        "typeof(timeout_s)='integer' AND timeout_s BETWEEN 1 AND 300 AND writes=1 AND length(spec_sha256)=64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'",
        name="behavior",
    ),
    CheckConstraint(
        "json_valid(parameters) AND json_type(parameters)='object'",
        name="parameters",
    ),
)
run_extension_tools = scoped(
    "run_extension_tools",
    Column("run_id", String(36), nullable=False),
    Column("extension_id", String(36), nullable=False),
    Column("extension_revision_id", String(36), nullable=False),
    Column("tool_id", String(36), nullable=False),
    Column("grant_id", String(36), nullable=False),
    Column("authority_epoch", Integer, nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("name", String(100), nullable=False),
    scoped_fk("run_id", "runs"),
    ForeignKeyConstraint(
        ["tool_id", "extension_revision_id", "extension_id", "agent_id"],
        [
            "extension_tools.id",
            "extension_tools.extension_revision_id",
            "extension_tools.extension_id",
            "extension_tools.agent_id",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["grant_id", "tool_id", "authority_epoch", "agent_id"],
        ["grants.id", "grants.tool_id", "grants.authority_epoch", "grants.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("run_id", "name"),
    UniqueConstraint("id", "run_id", "agent_id"),
    CheckConstraint(
        "typeof(authority_epoch)='integer' AND authority_epoch BETWEEN 1 AND 9223372036854775807 AND length(history_epoch)=36 AND length(name) BETWEEN 1 AND 64",
        name="identity",
    ),
)
action_extension_bindings = scoped(
    "action_extension_bindings",
    Column("action_id", String(36), nullable=False),
    Column("run_tool_id", String(36), nullable=False),
    scoped_fk("action_id", "actions"),
    scoped_fk("run_tool_id", "run_extension_tools"),
    UniqueConstraint("action_id"),
)
extension_install_requests = scoped(
    "extension_install_requests",
    Column("request_key", String(200), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("extension_id", String(36), nullable=False),
    Column("extension_revision_id", String(36), nullable=False),
    ForeignKeyConstraint(
        ["extension_revision_id", "extension_id", "agent_id"],
        ["extension_revisions.id", "extension_revisions.extension_id", "extension_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("agent_id", "request_key"),
    CheckConstraint(
        "length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'",
        name="identity",
    ),
)
runs.append_column(
    Column(
        "extension_capture_epoch",
        String(36),
        CheckConstraint(
            "extension_capture_epoch IS NULL OR length(extension_capture_epoch)=36", name="extension_capture"
        ),
    )
)

# Unknown execution outcomes and owner observations are distinct truths. Only
# the current observation head changes; ledger identities never rewrite an action.
Index("uq_actions_run_identity", actions.c.id, actions.c.run_id, actions.c.agent_id, unique=True)
action_review_heads = Table(
    "action_review_heads",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("id", String(36), CheckConstraint("length(id)=36", name="uuid_length"), nullable=False),
    scope(),
    stamp(),
    Column("action_id", String(36), nullable=False),
    Column("run_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("policy", String(40), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("arguments_sha256", String(64), nullable=False),
    Column("result_sha256", String(64), nullable=False),
    Column("session_epoch", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("resolution", String(20)),
    Column("last_review_id", String(36)),
    Column("updated_at", String(40), nullable=False),
    Column("error_code", String(80), nullable=False, server_default=""),
    UniqueConstraint("id"),
    UniqueConstraint("id", "agent_id"),
    UniqueConstraint("action_id"),
    UniqueConstraint("id", "action_id", "run_id", "agent_id"),
    ForeignKeyConstraint(
        ["action_id", "run_id", "agent_id"], ["actions.id", "actions.run_id", "actions.agent_id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(
        ["last_review_id", "id", "agent_id"],
        ["action_reviews.id", "action_reviews.head_id", "action_reviews.agent_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint(
        "sequence>0 AND typeof(revision)='integer' AND revision BETWEEN 0 AND 9223372036854775807 AND typeof(session_epoch)='integer' AND session_epoch BETWEEN 1 AND 9223372036854775807",
        name="bounds",
    ),
    CheckConstraint(
        "policy='action-review-1' AND length(history_epoch)=36 AND length(error_code)<=80 AND length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*' AND length(arguments_sha256)=64 AND arguments_sha256 NOT GLOB '*[^0-9a-f]*' AND length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'",
        name="identity",
    ),
    CheckConstraint(
        "(state IN ('pending','invalidated') AND resolution IS NULL AND last_review_id IS NULL) OR (state='reviewed' AND resolution IN ('applied','not_applied','still_unknown') AND last_review_id IS NOT NULL)",
        name="resolution",
    ),
    sqlite_autoincrement=True,
)
Index("ix_action_review_heads_agent_sequence", action_review_heads.c.agent_id, action_review_heads.c.sequence)
Index("ix_action_review_heads_run", action_review_heads.c.agent_id, action_review_heads.c.run_id)
action_reviews = scoped(
    "action_reviews",
    Column("head_id", String(36), nullable=False),
    Column("action_id", String(36), nullable=False),
    Column("run_id", String(36), nullable=False),
    Column("head_revision", Integer, nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("policy", String(40), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("proof", JSON, nullable=False),
    Column("decision", String(20), nullable=False),
    Column("evidence_sha256", String(64), nullable=False),
    Column("review_audit_id", String(36), nullable=False),
    ForeignKeyConstraint(
        ["head_id", "action_id", "run_id", "agent_id"],
        [
            "action_review_heads.id",
            "action_review_heads.action_id",
            "action_review_heads.run_id",
            "action_review_heads.agent_id",
        ],
        ondelete="RESTRICT",
    ),
    scoped_fk("review_audit_id", "audit"),
    UniqueConstraint("head_id", "head_revision"),
    UniqueConstraint("id", "head_id", "agent_id"),
    CheckConstraint(
        "typeof(head_revision)='integer' AND head_revision BETWEEN 0 AND 9223372036854775807 AND policy='action-review-1' AND length(history_epoch)=36 AND decision IN ('applied','not_applied','still_unknown')",
        name="decision",
    ),
    CheckConstraint(
        "length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*' AND length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*' AND length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'",
        name="fingerprints",
    ),
    CheckConstraint("json_valid(proof) AND json_type(proof)='object'", name="proof"),
)
Index("ix_action_reviews_action", action_reviews.c.agent_id, action_reviews.c.action_id, action_reviews.c.head_revision)
Index("ix_action_reviews_review_audit", action_reviews.c.agent_id, action_reviews.c.review_audit_id)
action_review_evidence = scoped(
    "action_review_evidence",
    Column("review_id", String(36), nullable=False),
    Column("body", Text, nullable=False),
    Column("body_sha256", String(64), nullable=False),
    Column("erased_at", String(40)),
    scoped_fk("review_id", "action_reviews"),
    UniqueConstraint("review_id"),
    CheckConstraint(
        "length(body_sha256)=64 AND body_sha256 NOT GLOB '*[^0-9a-f]*' AND ((erased_at IS NULL AND length(body) BETWEEN 1 AND 2000) OR (erased_at IS NOT NULL AND body=''))",
        name="body",
    ),
)

terminal_sessions = Table(
    "terminal_sessions",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("id", String(36), nullable=False),
    scope(),
    stamp(),
    Column("boot_id", String(36), nullable=False),
    Column("shell", String(32), nullable=False),
    Column("executable", Text, nullable=False),
    Column("cwd", Text, nullable=False),
    Column("workspace_sha256", String(64), nullable=False),
    Column("permission_epoch", Integer, nullable=False),
    Column("cols", Integer, nullable=False),
    Column("rows", Integer, nullable=False),
    Column("state", String(16), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("pid", Integer),
    Column("process_created", Float),
    Column("cleanup_pending", Boolean, nullable=False),
    Column("error_code", String(80), nullable=False),
    Column("exit_code", Integer),
    Column("ended_at", String(40)),
    UniqueConstraint("id"),
    UniqueConstraint("id", "agent_id"),
    CheckConstraint("length(id)=36 AND length(boot_id)=36 AND sequence>0", name="identity"),
    CheckConstraint(
        "shell IN ('powershell','pwsh','cmd','bash','zsh','sh') AND length(executable) BETWEEN 1 AND 4000 AND length(cwd) BETWEEN 1 AND 4000",
        name="profile",
    ),
    CheckConstraint("length(workspace_sha256)=64 AND workspace_sha256 NOT GLOB '*[^0-9a-f]*'", name="workspace"),
    CheckConstraint(
        "typeof(permission_epoch)='integer' AND permission_epoch>0 AND typeof(revision)='integer' AND revision>0",
        name="revision",
    ),
    CheckConstraint(
        "typeof(cols)='integer' AND cols BETWEEN 20 AND 500 AND typeof(rows)='integer' AND rows BETWEEN 5 AND 200",
        name="geometry",
    ),
    CheckConstraint(
        "(pid IS NULL AND process_created IS NULL) OR (typeof(pid)='integer' AND pid>0 AND process_created>0)",
        name="process",
    ),
    CheckConstraint(
        "length(error_code)<=80 AND cleanup_pending IN (0,1) AND state IN ('starting','running','closing','exited','unknown')",
        name="state",
    ),
    CheckConstraint(
        "(state='exited' AND cleanup_pending=0 AND ended_at IS NOT NULL) OR (state!='exited' AND ended_at IS NULL)",
        name="exit",
    ),
    CheckConstraint("state!='running' OR (pid IS NOT NULL AND cleanup_pending=0)", name="running"),
    CheckConstraint("state NOT IN ('starting','closing','unknown') OR cleanup_pending=1", name="cleanup"),
    sqlite_autoincrement=True,
)
Index("ix_terminal_sessions_agent_sequence", terminal_sessions.c.agent_id, terminal_sessions.c.sequence)
Index("ix_terminal_sessions_state", terminal_sessions.c.boot_id, terminal_sessions.c.state)

terminal_operations = scoped(
    "terminal_operations",
    Column("session_id", String(36), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("state", String(16), nullable=False),
    Column("intent_audit_id", String(36), nullable=False),
    Column("error_code", String(80), nullable=False),
    Column("ended_at", String(40)),
    scoped_fk("session_id", "terminal_sessions"),
    scoped_fk("intent_audit_id", "audit"),
    CheckConstraint("length(boot_id)=36 AND kind IN ('open','input','resize','close')", name="identity"),
    CheckConstraint("length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'", name="fingerprint"),
    CheckConstraint(
        "typeof(bytes)='integer' AND ((kind='input' AND bytes BETWEEN 1 AND 16384) OR (kind!='input' AND bytes=0))",
        name="bytes",
    ),
    CheckConstraint("state IN ('prepared','accepted','not_sent','unknown') AND length(error_code)<=80", name="state"),
    CheckConstraint(
        "(state='prepared' AND ended_at IS NULL) OR (state!='prepared' AND ended_at IS NOT NULL)", name="settled"
    ),
)
Index(
    "ix_terminal_operations_session",
    terminal_operations.c.agent_id,
    terminal_operations.c.session_id,
    terminal_operations.c.created_at,
)
Index("ix_terminal_operations_audit", terminal_operations.c.agent_id, terminal_operations.c.intent_audit_id)
Index(
    "ix_terminal_operations_review_cursor",
    terminal_operations.c.agent_id,
    terminal_operations.c.state,
    terminal_operations.c.id,
)
Index(
    "uq_terminal_operations_review_scope",
    terminal_operations.c.id,
    terminal_operations.c.session_id,
    terminal_operations.c.agent_id,
    unique=True,
)

terminal_reviews = scoped(
    "terminal_reviews",
    Column("session_id", String(36), nullable=False),
    Column("operation_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("policy", String(32), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("proof", JSON, nullable=False),
    Column("decision", String(20), nullable=False),
    Column("evidence_sha256", String(64), nullable=False),
    Column("evidence", Text),
    Column("evidence_erased_at", String(40)),
    Column("audit_id", String(36), nullable=False),
    scoped_fk("session_id", "terminal_sessions"),
    ForeignKeyConstraint(
        ["operation_id", "session_id", "agent_id"],
        ["terminal_operations.id", "terminal_operations.session_id", "terminal_operations.agent_id"],
        ondelete="RESTRICT",
    ),
    scoped_fk("audit_id", "audit"),
    UniqueConstraint("agent_id", "operation_id", "revision"),
    CheckConstraint(
        "typeof(revision)='integer' AND revision BETWEEN 1 AND 100 AND length(history_epoch)=36 AND policy='terminal-review-1'",
        name="identity",
    ),
    CheckConstraint("decision IN ('applied','not_applied','still_unknown')", name="decision"),
    CheckConstraint(
        "length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*' AND length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*' AND length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'",
        name="fingerprints",
    ),
    CheckConstraint(
        "(evidence IS NOT NULL AND length(evidence) BETWEEN 1 AND 2000 AND length(CAST(evidence AS BLOB))<=8000 AND instr(evidence,char(0))=0 AND evidence_erased_at IS NULL) OR (evidence IS NULL AND evidence_erased_at IS NOT NULL AND length(evidence_erased_at) BETWEEN 1 AND 40)",
        name="evidence",
    ),
    CheckConstraint(
        "json_valid(proof) AND length(CAST(proof AS BLOB))<=16384 AND COALESCE(json_type(proof,'$.schema')='integer' AND json_extract(proof,'$.schema')=1,0)",
        name="proof",
    ),
)
Index("ix_terminal_reviews_audit", terminal_reviews.c.agent_id, terminal_reviews.c.audit_id)

session_captures = scoped(
    "session_captures",
    Column("session_id", String(36), nullable=False),
    Column("object_id", String(36), nullable=False),
    Column("operation_id", String(36), nullable=False),
    Column("content_epoch", Integer, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("method", String(32), nullable=False),
    scoped_fk("session_id", "sessions"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("agent_id", "operation_id"),
    CheckConstraint("length(operation_id)=36 AND typeof(content_epoch)='integer' AND content_epoch>0", name="identity"),
    CheckConstraint(
        "typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 1 AND 8192 AND height BETWEEN 1 AND 8192 AND width*height<=16000000",
        name="dimensions",
    ),
    CheckConstraint(
        "method IN ('print_window','screen_capture_kit','x11_composite','pipewire_window','adb_screencap')",
        name="method",
    ),
)
Index("ix_session_captures_cursor", session_captures.c.agent_id, session_captures.c.session_id, session_captures.c.id)
Index("ix_session_captures_object", session_captures.c.agent_id, session_captures.c.object_id)

android_grants = scoped(
    "android_grants",
    Column("config_id", String(36), nullable=False),
    Column("config_revision_id", String(36), nullable=False),
    Column("device", JSON, nullable=False),
    Column("device_sha256", String(64), nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("revoked_at", String(40)),
    Column("revision", Integer, nullable=False),
    scoped_fk("config_id", "configurations"),
    scoped_fk("config_revision_id", "configuration_revisions"),
    CheckConstraint(
        "json_valid(device) AND json_type(device)='object' AND length(CAST(device AS BLOB))<=2048 AND length(device_sha256)=64",
        name="device",
    ),
    CheckConstraint("typeof(revision)='integer' AND revision>0", name="revision"),
    CheckConstraint("length(history_epoch)=36", name="history_epoch"),
)

android_access = scoped(
    "android_access",
    Column("grant_id", String(36), nullable=False),
    Column("run_id", String(36), nullable=False),
    Column("source_action_id", String(36), nullable=False),
    Column("session_epoch", Integer, nullable=False),
    Column("policy_revision", Integer, nullable=False),
    Column("scope_sha256", String(64), nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("revoked_at", String(40)),
    Column("revision", Integer, nullable=False),
    scoped_fk("grant_id", "android_grants"),
    scoped_fk("run_id", "runs"),
    scoped_fk("source_action_id", "actions"),
    UniqueConstraint("source_action_id"),
    CheckConstraint(
        "session_epoch>0 AND policy_revision>0 AND revision>0 AND length(expires_at) BETWEEN 20 AND 40", name="identity"
    ),
    CheckConstraint("length(scope_sha256)=64", name="scope"),
)

android_access_packages = scoped(
    "android_access_packages",
    Column("access_id", String(36), nullable=False),
    Column("package", String(200), nullable=False),
    scoped_fk("access_id", "android_access"),
    UniqueConstraint("access_id", "package"),
    CheckConstraint(
        "length(package) BETWEEN 3 AND 200 AND instr(package,'.')>1 AND package NOT GLOB '*[^A-Za-z0-9_.]*'",
        name="package",
    ),
)
android_access_kinds = scoped(
    "android_access_kinds",
    Column("access_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    scoped_fk("access_id", "android_access"),
    UniqueConstraint("access_id", "kind"),
    CheckConstraint(
        "kind IN ('tap','toggle','append_ascii','append_unicode','long_press','swipe','key','launch')", name="kind"
    ),
)

android_unicode_profiles = scoped(
    "android_unicode_profiles",
    Column("grant_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("credential_ref", String(200), nullable=False),
    Column("apk_sha256", String(64), nullable=False),
    Column("certificate_sha256", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    scoped_fk("grant_id", "android_grants"),
    UniqueConstraint("grant_id", "revision"),
    CheckConstraint(
        "revision>0 AND enabled IN (0,1) AND length(apk_sha256) IN (0,64) AND length(certificate_sha256) IN (0,64) AND length(fingerprint)=64 AND (enabled=0 OR (length(credential_ref)>0 AND length(apk_sha256)=64 AND length(certificate_sha256)=64))",
        name="profile",
    ),
)
android_unicode_environments = scoped(
    "android_unicode_environments",
    Column("action_id", String(36), nullable=False),
    Column("grant_id", String(36), nullable=False),
    Column("profile_id", String(36), nullable=False),
    Column("device_serial", String(128), nullable=False),
    Column("server_port", Integer, nullable=False),
    Column("prepare_nonce", String(36), nullable=False),
    Column("original_ime", String(400), nullable=False),
    Column("original_subtype", Integer, nullable=False),
    Column("enabled_sha256", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("current_ime", String(400)),
    Column("current_subtype", Integer),
    Column("result_code", String(80), nullable=False),
    Column("observed_at", String(40)),
    scoped_fk("action_id", "actions"),
    scoped_fk("grant_id", "android_grants"),
    scoped_fk("profile_id", "android_unicode_profiles"),
    UniqueConstraint("action_id"),
    CheckConstraint(
        "state IN ('pending','restored','accepted') AND revision>0 AND server_port BETWEEN 1 AND 65535 AND length(prepare_nonce)=36 AND length(original_ime) BETWEEN 3 AND 400 AND length(enabled_sha256)=64",
        name="environment",
    ),
)
Index(
    "ix_android_unicode_environments_device",
    android_unicode_environments.c.device_serial,
    android_unicode_environments.c.state,
)

owner_commands = scoped(
    "owner_commands",
    Column("run_id", String(36), nullable=False),
    Column("request_id", String(36), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("session_epoch", Integer, nullable=False),
    Column("tool", String(100), nullable=False),
    Column("arguments", JSON(none_as_null=True)),
    Column("action_id", String(36)),
    scoped_fk("run_id", "runs"),
    scoped_fk("action_id", "actions"),
    UniqueConstraint("agent_id", "request_id"),
    UniqueConstraint("run_id"),
    UniqueConstraint("action_id"),
    CheckConstraint("length(request_id)=36 AND length(boot_id)=36 AND length(request_sha256)=64", name="identity"),
    CheckConstraint(
        "session_epoch>0 AND tool IN ('desktop_semantic','android_input','android_restore_ime')", name="command"
    ),
    CheckConstraint("action_id IS NULL OR arguments IS NULL", name="single_body"),
)

image_objects = scoped(
    "image_objects",
    Column("object_id", String(36), nullable=False),
    Column("media_type", String(30), nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    ForeignKeyConstraint(["object_id", "agent_id"], ["objects.id", "objects.agent_id"], ondelete="CASCADE"),
    UniqueConstraint("object_id"),
    CheckConstraint(
        "media_type IN ('image/png','image/jpeg','image/webp') AND typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 1 AND 8192 AND height BETWEEN 1 AND 8192 AND width*height<=16000000",
        name="image",
    ),
)
image_objects.info["object_metadata"] = True
model_image_policies = scoped(
    "model_image_policies",
    Column("revision_id", String(36), nullable=False),
    Column("protocol", String(30), nullable=False),
    Column("per_call_limit", Integer, nullable=False),
    Column("run_budget", Integer, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    scoped_fk("revision_id", "configuration_revisions"),
    UniqueConstraint("revision_id"),
    CheckConstraint(
        "protocol IN ('disabled','openai_low') AND typeof(per_call_limit)='integer' AND typeof(run_budget)='integer' AND per_call_limit BETWEEN 1 AND 4 AND run_budget BETWEEN per_call_limit AND 64 AND length(fingerprint)=64",
        name="policy",
    ),
)
model_call_images = scoped(
    "model_call_images",
    Column("call_id", String(36), nullable=False),
    Column("snapshot_id", String(36), nullable=False),
    Column("policy_id", String(36), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("part_id", String(36), nullable=False),
    Column("object_id", String(36)),
    Column("sha256", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("erased_at", String(40)),
    scoped_fk("call_id", "model_calls"),
    scoped_fk("snapshot_id", "context_snapshots"),
    scoped_fk("policy_id", "model_image_policies"),
    scoped_fk("message_id", "messages"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("call_id", "part_id"),
    CheckConstraint(
        "state IN ('prepared','dispatched','attached','not_sent','unknown') AND length(part_id)=36 AND length(sha256)=64 AND (erased_at IS NULL OR object_id IS NULL)",
        name="image",
    ),
)
Index("ix_model_call_images_call", model_call_images.c.agent_id, model_call_images.c.call_id)

media_sources = scoped(
    "media_sources",
    Column("kind", String(20), nullable=False),
    Column("bvid", String(12), nullable=False),
    Column("aid", String(19), nullable=False),
    Column("cid", String(19), nullable=False),
    Column("title", String(300), nullable=False),
    Column("duration_s", Integer, nullable=False),
    Column("config_id", String(36), nullable=False),
    Column("config_revision_id", String(36), nullable=False),
    scoped_fk("config_id", "configurations"),
    scoped_fk("config_revision_id", "configuration_revisions"),
    UniqueConstraint("agent_id", "bvid"),
    CheckConstraint(
        "kind='bilibili_video' AND length(bvid)=12 AND substr(bvid,1,2)='BV' AND substr(bvid,3) NOT GLOB '*[^0-9A-Za-z]*' AND length(aid) BETWEEN 1 AND 19 AND length(cid) BETWEEN 1 AND 19 AND typeof(duration_s)='integer' AND duration_s BETWEEN 1 AND 7200 AND length(title)<=300",
        name="source",
    ),
)
media_jobs = scoped(
    "media_jobs",
    Column("source_id", String(36), nullable=False),
    Column("operation", String(12), nullable=False),
    Column("state", String(12), nullable=False),
    Column("params", JSON(none_as_null=True)),
    Column("error_code", String(80), nullable=False),
    Column("finished_at", String(40)),
    scoped_fk("source_id", "media_sources"),
    CheckConstraint(
        "operation IN ('subtitle','transcribe','frames') AND state IN ('queued','running','done','failed','cancelled','unknown') AND length(error_code)<=80 AND (state IN ('queued','running') OR finished_at IS NOT NULL)",
        name="job",
    ),
)
Index("ix_media_jobs_source", media_jobs.c.agent_id, media_jobs.c.source_id)
media_transcripts = scoped(
    "media_transcripts",
    Column("source_id", String(36), nullable=False),
    Column("job_id", String(36), nullable=False),
    Column("kind", String(12), nullable=False),
    Column("language", String(16), nullable=False),
    Column("object_id", String(36), nullable=False),
    Column("bytes", Integer, nullable=False),
    scoped_fk("source_id", "media_sources"),
    scoped_fk("job_id", "media_jobs"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("job_id"),
    CheckConstraint(
        "kind IN ('subtitle','asr') AND length(language)<=16 AND typeof(bytes)='integer' AND bytes BETWEEN 1 AND 4194304",
        name="transcript",
    ),
)
media_frames = scoped(
    "media_frames",
    Column("source_id", String(36), nullable=False),
    Column("job_id", String(36), nullable=False),
    Column("object_id", String(36), nullable=False),
    Column("frame_count", Integer, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("timestamps", JSON(none_as_null=True)),
    scoped_fk("source_id", "media_sources"),
    scoped_fk("job_id", "media_jobs"),
    scoped_fk("object_id", "objects"),
    UniqueConstraint("job_id"),
    CheckConstraint(
        "typeof(frame_count)='integer' AND frame_count BETWEEN 1 AND 12 AND typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 1 AND 8192 AND height BETWEEN 1 AND 8192 AND width*height<=16000000",
        name="frames",
    ),
)

model_route_policies = scoped(
    "model_route_policies",
    Column("revision", Integer, nullable=False),
    Column("default_mode", String(16), nullable=False),
    Column("allow_escalation", Boolean, nullable=False),
    *(Column(f"{mode}_model_id", String(36)) for mode in ("chat", "retrieval", "agent")),
    *(Column(f"{mode}_reservation_micro", BigInteger) for mode in ("chat", "retrieval", "agent")),
    *(scoped_fk(f"{mode}_model_id", "configurations") for mode in ("chat", "retrieval", "agent")),
    UniqueConstraint("agent_id", "revision"),
    CheckConstraint(
        "typeof(revision)='integer' AND revision>0 AND default_mode IN ('auto','chat','retrieval','agent') AND allow_escalation IN (0,1)",
        name="policy",
    ),
    *(
        CheckConstraint(
            f"{mode}_reservation_micro IS NULL OR (typeof({mode}_reservation_micro)='integer' AND {mode}_reservation_micro BETWEEN 1 AND 1000000000000)",
            name=f"{mode}_amount",
        )
        for mode in ("chat", "retrieval", "agent")
    ),
)
run_routes = scoped(
    "run_routes",
    Column("run_id", String(36), nullable=False),
    Column("policy_id", String(36)),
    Column("requested_mode", String(16), nullable=False),
    Column("initial_mode", String(16), nullable=False),
    Column("source", String(16), nullable=False),
    Column("reason", String(80), nullable=False),
    Column("allow_escalation", Boolean, nullable=False),
    Column("reservation_micro", BigInteger, nullable=False),
    scoped_fk("run_id", "runs"),
    scoped_fk("policy_id", "model_route_policies"),
    UniqueConstraint("run_id"),
    CheckConstraint(
        "requested_mode IN ('default','auto','chat','retrieval','agent') AND initial_mode IN ('chat','retrieval','agent') AND source IN ('manual','default','rule','fallback','channel','baseline') AND allow_escalation IN (0,1) AND typeof(reservation_micro)='integer' AND reservation_micro BETWEEN 1 AND 1000000000000",
        name="route",
    ),
)
route_changes = scoped(
    "route_changes",
    Column("run_id", String(36), nullable=False),
    Column("route_id", String(36), nullable=False),
    Column("source_snapshot_id", String(36), nullable=False),
    Column("from_mode", String(16), nullable=False),
    Column("to_mode", String(16), nullable=False),
    scoped_fk("run_id", "runs"),
    scoped_fk("route_id", "run_routes"),
    scoped_fk("source_snapshot_id", "model_call_routes"),
    UniqueConstraint("run_id"),
    CheckConstraint(
        "(from_mode='chat' AND to_mode IN ('retrieval','agent')) OR (from_mode='retrieval' AND to_mode='agent')",
        name="upward",
    ),
)
model_call_routes = scoped(
    "model_call_routes",
    Column("call_id", String(36), nullable=False),
    Column("route_id", String(36), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("mode", String(16), nullable=False),
    Column("tool_count", Integer, nullable=False),
    Column("full_tool_count", Integer, nullable=False),
    Column("tool_bytes", Integer, nullable=False),
    Column("full_tool_bytes", Integer, nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    scoped_fk("call_id", "model_calls"),
    scoped_fk("route_id", "run_routes"),
    UniqueConstraint("call_id"),
    UniqueConstraint("route_id", "ordinal"),
    CheckConstraint("typeof(ordinal)='integer' AND ordinal BETWEEN 1 AND 64", name="ordinal"),
    CheckConstraint(
        "mode IN ('chat','retrieval','agent') AND tool_count BETWEEN 0 AND 513 AND full_tool_count BETWEEN 0 AND 512 AND tool_bytes>=0 AND full_tool_bytes>=0 AND length(manifest_sha256)=64",
        name="snapshot",
    ),
)
model_call_route_tools = scoped(
    "model_call_route_tools",
    Column("snapshot_id", String(36), nullable=False),
    Column("position", Integer, nullable=False),
    Column("name", String(100), nullable=False),
    Column("spec_sha256", String(64), nullable=False),
    scoped_fk("snapshot_id", "model_call_routes"),
    UniqueConstraint("snapshot_id", "position"),
    UniqueConstraint("snapshot_id", "name"),
    CheckConstraint(
        "typeof(position)='integer' AND position BETWEEN 0 AND 512 AND length(name) BETWEEN 1 AND 100 AND length(spec_sha256)=64",
        name="tool",
    ),
)

memory_embeddings = scoped(
    "memory_embeddings",
    Column("memory_id", String(36), nullable=False),
    scoped_fk("memory_id", "memory_entries"),
    Column("memory_revision_id", String(36), nullable=False),
    scoped_fk("memory_revision_id", "memory_revisions"),
    Column("revision", Integer, nullable=False),
    Column("version", String(100), nullable=False),
    Column("dimension", Integer, nullable=False),
    Column("raw_sha256", String(64), nullable=False),
    Column("evidence_sha256", String(64), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("state", String(20), nullable=False),
    Column("embedding", JSON(none_as_null=True)),
    Column("finished_at", String(40)),
    Column("error_code", String(80), nullable=False),
    CheckConstraint(
        "revision>0 AND dimension BETWEEN 1 AND 512 AND length(version) BETWEEN 1 AND 100 AND length(raw_sha256)=64 AND length(evidence_sha256)=64 AND length(boot_id)=36",
        name="identity",
    ),
    CheckConstraint(
        "(state='building' AND embedding IS NULL AND finished_at IS NULL) OR (state='ready' AND embedding IS NOT NULL AND finished_at IS NOT NULL) OR (state IN ('failed','invalidated') AND embedding IS NULL AND finished_at IS NOT NULL)",
        name="state",
    ),
)
Index(
    "ix_memory_embeddings_active",
    memory_embeddings.c.memory_revision_id,
    memory_embeddings.c.version,
    unique=True,
    sqlite_where=memory_embeddings.c.state.in_(["building", "ready"]),
)

memory_semantic_batches = scoped(
    "memory_semantic_batches",
    Column("request_key", String(200), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("policy", String(48), nullable=False),
    Column("history_epoch", String(36), nullable=False),
    Column("version", String(100), nullable=False),
    Column("dimension", Integer, nullable=False),
    Column("source_count", Integer, nullable=False),
    Column("candidate_count", Integer, nullable=False),
    UniqueConstraint("agent_id", "request_key"),
    CheckConstraint(
        "length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64 AND policy='local-related-facts-v1' AND length(history_epoch)=36 AND length(version) BETWEEN 1 AND 100 AND dimension BETWEEN 1 AND 512 AND source_count BETWEEN 2 AND 32 AND candidate_count BETWEEN 0 AND 31",
        name="manifest",
    ),
)
memory_semantic_sources = scoped(
    "memory_semantic_sources",
    Column("batch_id", String(36), nullable=False),
    scoped_fk("batch_id", "memory_semantic_batches"),
    Column("memory_id", String(36), nullable=False),
    scoped_fk("memory_id", "memory_entries"),
    Column("memory_revision_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("raw_sha256", String(64), nullable=False),
    Column("evidence_sha256", String(64), nullable=False),
    Column("person_id", String(36)),
    scoped_fk("person_id", "people"),
    Column("session_id", String(36)),
    scoped_fk("session_id", "sessions"),
    ForeignKeyConstraint(
        ["memory_revision_id", "memory_id", "agent_id"],
        ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("batch_id", "memory_id"),
    UniqueConstraint("batch_id", "position"),
    UniqueConstraint("id", "batch_id", "agent_id"),
    CheckConstraint(
        "revision>0 AND position BETWEEN 0 AND 31 AND length(raw_sha256)=64 AND length(evidence_sha256)=64",
        name="manifest",
    ),
)
memory_semantic_links = scoped(
    "memory_semantic_links",
    Column("batch_id", String(36), nullable=False),
    scoped_fk("batch_id", "memory_semantic_batches"),
    Column("left_source_id", String(36), nullable=False),
    Column("right_source_id", String(36), nullable=False),
    Column("similarity_micro", Integer, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("review_audit_id", String(36)),
    Column("undo_audit_id", String(36)),
    scoped_fk("review_audit_id", "audit"),
    scoped_fk("undo_audit_id", "audit"),
    Column("updated_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["left_source_id", "batch_id", "agent_id"],
        ["memory_semantic_sources.id", "memory_semantic_sources.batch_id", "memory_semantic_sources.agent_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["right_source_id", "batch_id", "agent_id"],
        ["memory_semantic_sources.id", "memory_semantic_sources.batch_id", "memory_semantic_sources.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("batch_id", "left_source_id", "right_source_id"),
    CheckConstraint(
        "left_source_id!=right_source_id AND similarity_micro BETWEEN 850000 AND 1000000 AND length(fingerprint)=64 AND revision>0",
        name="identity",
    ),
    CheckConstraint(
        "(state='pending' AND review_audit_id IS NULL AND undo_audit_id IS NULL) OR (state IN ('accepted','rejected') AND review_audit_id IS NOT NULL AND undo_audit_id IS NULL) OR (state='revoked' AND review_audit_id IS NOT NULL AND undo_audit_id IS NOT NULL) OR state='invalidated'",
        name="state",
    ),
)

for semantic_table, integer_columns in (
    (memory_embeddings, ("revision", "dimension")),
    (memory_semantic_batches, ("dimension", "source_count", "candidate_count")),
    (memory_semantic_sources, ("revision", "position")),
    (memory_semantic_links, ("similarity_micro", "revision")),
):
    semantic_table.append_constraint(
        CheckConstraint(" AND ".join(f"typeof({name})='integer'" for name in integer_columns), name="integers")
    )

runs.append_column(Column("skill_capture_epoch", String(36)))
skills = scoped(
    "skills",
    Column("name", String(40), nullable=False),
    Column("current_revision_id", String(36), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("authority_epoch", Integer, nullable=False),
    Column("state", String(12), nullable=False),
    Column("review_audit_id", String(36)),
    scoped_fk("review_audit_id", "audit"),
    UniqueConstraint("agent_id", "name"),
    ForeignKeyConstraint(
        ["current_revision_id", "id", "agent_id"],
        ["skill_manifests.revision_id", "skill_manifests.skill_id", "skill_manifests.agent_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint(
        "length(name) BETWEEN 3 AND 40 AND typeof(revision)='integer' AND revision>0 AND typeof(authority_epoch)='integer' AND authority_epoch>0 AND state IN ('disabled','enabled','archived')",
        name="head",
    ),
)
skill_manifests = Table(
    "skill_manifests",
    metadata,
    Column("revision_id", String(36), primary_key=True),
    scope(),
    scoped_fk("revision_id", "skill_revisions"),
    Column("skill_id", String(36), nullable=False),
    scoped_fk("skill_id", "skills"),
    Column("description", String(120), nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("windows", Boolean, nullable=False),
    Column("macos", Boolean, nullable=False),
    Column("linux", Boolean, nullable=False),
    UniqueConstraint("revision_id", "skill_id", "agent_id"),
    UniqueConstraint("revision_id", "agent_id"),
    CheckConstraint(
        "length(description) BETWEEN 1 AND 120 AND length(manifest_sha256)=64 AND windows IN (0,1) AND macos IN (0,1) AND linux IN (0,1) AND windows+macos+linux>0",
        name="manifest",
    ),
)
skill_files = scoped(
    "skill_files",
    Column("revision_id", String(36), nullable=False),
    scoped_fk("revision_id", "skill_revisions"),
    Column("path", String(180), nullable=False),
    Column("object_id", String(36), nullable=False),
    scoped_fk("object_id", "objects"),
    Column("sha256", String(64), nullable=False),
    Column("bytes", Integer, nullable=False),
    UniqueConstraint("revision_id", "path"),
    ForeignKeyConstraint(
        ["revision_id", "agent_id"], ["skill_manifests.revision_id", "skill_manifests.agent_id"], ondelete="RESTRICT"
    ),
    CheckConstraint(
        "length(path) BETWEEN 1 AND 180 AND length(sha256)=64 AND typeof(bytes)='integer' AND bytes BETWEEN 1 AND 131072",
        name="file",
    ),
)
run_skill_catalog = scoped(
    "run_skill_catalog",
    Column("run_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    Column("skill_id", String(36), nullable=False),
    scoped_fk("skill_id", "skills"),
    Column("revision_id", String(36), nullable=False),
    Column("authority_epoch", Integer, nullable=False),
    Column("review_audit_id", String(36), nullable=False),
    scoped_fk("review_audit_id", "audit"),
    Column("position", Integer, nullable=False),
    ForeignKeyConstraint(
        ["revision_id", "skill_id", "agent_id"],
        ["skill_manifests.revision_id", "skill_manifests.skill_id", "skill_manifests.agent_id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("run_id", "skill_id"),
    UniqueConstraint("run_id", "position"),
    UniqueConstraint("id", "run_id", "skill_id", "agent_id"),
    CheckConstraint(
        "typeof(authority_epoch)='integer' AND authority_epoch>0 AND typeof(position)='integer' AND position BETWEEN 0 AND 31",
        name="capture",
    ),
)
run_skill_activations = scoped(
    "run_skill_activations",
    Column("catalog_id", String(36), nullable=False),
    Column("run_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    Column("skill_id", String(36), nullable=False),
    scoped_fk("skill_id", "skills"),
    Column("position", Integer, nullable=False),
    Column("platform", String(12), nullable=False),
    Column("source", String(12), nullable=False),
    Column("action_id", String(36)),
    scoped_fk("action_id", "actions"),
    ForeignKeyConstraint(
        ["catalog_id", "run_id", "skill_id", "agent_id"],
        [
            "run_skill_catalog.id",
            "run_skill_catalog.run_id",
            "run_skill_catalog.skill_id",
            "run_skill_catalog.agent_id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("run_id", "skill_id"),
    UniqueConstraint("run_id", "position"),
    CheckConstraint(
        "typeof(position)='integer' AND position BETWEEN 0 AND 3 AND platform IN ('windows','macos','linux') AND ((source='owner' AND action_id IS NULL) OR (source='model' AND action_id IS NOT NULL))",
        name="activation",
    ),
)

# Compute once after every object-owning table is declared; live queries and
# offline foreign-key inventory must recognize exactly the same owners.
OBJECT_REFERENCES = tuple(
    (table, foreign_key.parent)
    for table in metadata.tables.values()
    for foreign_key in table.foreign_keys
    if foreign_key.target_fullname == "objects.id" and not table.info.get("object_metadata")
)
AUDIT_REFERENCES = tuple(
    (table, foreign_key.parent)
    for table in metadata.tables.values()
    for foreign_key in table.foreign_keys
    if foreign_key.target_fullname == "audit.id"
)
