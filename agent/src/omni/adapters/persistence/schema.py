"""V2 relational schema. Repositories never issue DDL; migrations consume this metadata."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
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
settings = Table(
    "settings",
    metadata,
    scope(),
    Column("revision", Integer, nullable=False),
    Column("value", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("agent_id"),
    CheckConstraint("revision > 0", name="revision"),
)
people = scoped(
    "people",
    Column("name", Text, nullable=False),
    Column("owner_id", String(36), ForeignKey("principals.id")),
    Column("revision", Integer, nullable=False, default=1),
)
accounts = scoped(
    "accounts",
    Column("person_id", String(36), nullable=False),
    Column("channel", String(40), nullable=False),
    Column("external_id", Text, nullable=False),
    scoped_fk("person_id", "people"),
    UniqueConstraint("agent_id", "channel", "external_id"),
)
sessions = scoped(
    "sessions",
    Column("title", Text, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("next_seq", Integer, nullable=False, default=0),
    Column("deleted_at", String(40)),
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
    UniqueConstraint("agent_id", "sha256"),
    CheckConstraint("bytes >= 0", name="nonnegative_size"),
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
    Column("config_snapshot", JSON, nullable=False),
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
    Column("run_id", String(36), nullable=False),
    Column("message_ids", JSON, nullable=False),
    Column("memory_ids", JSON, nullable=False),
    scoped_fk("run_id", "runs"),
)
summaries = scoped(
    "summaries",
    Column("session_id", String(36), nullable=False),
    Column("first_seq", Integer, nullable=False),
    Column("last_seq", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    scoped_fk("session_id", "sessions"),
    CheckConstraint("first_seq <= last_seq", name="valid_interval"),
)
work_items = scoped(
    "work_items",
    Column("run_id", String(36)),
    Column("kind", String(40), nullable=False),
    Column("state", String(20), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("claimed_by", String(36)),
    Column("attempt", Integer, nullable=False, default=0),
    scoped_fk("run_id", "runs"),
    UniqueConstraint("run_id", "kind"),
    CheckConstraint("state IN ('pending','running','done','failed','cancelled','unknown')", name="state"),
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
outbox = scoped(
    "outbox",
    Column("run_id", String(36), nullable=False),
    Column("channel", String(40), nullable=False),
    Column("target", Text, nullable=False),
    Column("state", String(20), nullable=False),
    Column("token", String(36)),
    Column("message_id", String(36), nullable=False),
    scoped_fk("run_id", "runs"),
    scoped_fk("message_id", "messages"),
    UniqueConstraint("run_id", "channel", "target"),
    CheckConstraint("state IN ('pending','sending','sent','unknown','dead')", name="state"),
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
    scoped_fk("person_id", "people"),
    scoped_fk("session_id", "sessions"),
    CheckConstraint("kind IN ('fact','profile','resident','journal','note')", name="kind"),
)
source_anchors = scoped(
    "source_anchors",
    Column("memory_id", String(36), nullable=False),
    Column("message_id", String(36)),
    Column("action_id", String(36)),
    Column("object_id", String(36)),
    Column("manual_audit_id", String(36), ForeignKey("audit.id")),
    Column("quote", Text, nullable=False),
    scoped_fk("memory_id", "memory_entries"),
    scoped_fk("message_id", "messages"),
    scoped_fk("action_id", "actions"),
    scoped_fk("object_id", "objects"),
    CheckConstraint(
        "(message_id IS NOT NULL) + (action_id IS NOT NULL) + (object_id IS NOT NULL) + (manual_audit_id IS NOT NULL) = 1",
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
    scoped_fk("memory_id", "memory_entries"),
    UniqueConstraint("memory_id"),
)
schedules = scoped(
    "schedules",
    Column("name", Text, nullable=False),
    Column("prompt", Text, nullable=False),
    Column("timezone", String(100), nullable=False),
    Column("expression", String(100), nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("next_at", String(40), nullable=False),
)
occurrences = scoped(
    "occurrences",
    Column("schedule_id", String(36), nullable=False),
    Column("scheduled_at", String(40), nullable=False),
    Column("run_id", String(36)),
    scoped_fk("schedule_id", "schedules"),
    scoped_fk("run_id", "runs"),
    UniqueConstraint("schedule_id", "scheduled_at"),
)
leases = Table(
    "leases",
    metadata,
    scope(),
    Column("resource", String(200), nullable=False),
    Column("token", String(36), nullable=False),
    Column("boot_id", String(36), nullable=False),
    Column("expires_at", String(40), nullable=False),
    UniqueConstraint("agent_id", "resource"),
)
extensions = scoped(
    "extensions",
    Column("kind", String(30), nullable=False),
    Column("name", String(100), nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("manifest", JSON, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("enabled", Boolean, nullable=False, default=False),
    UniqueConstraint("agent_id", "kind", "name"),
)
grants = scoped(
    "grants",
    Column("extension_id", String(36), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("tool", String(100), nullable=False),
    Column("revoked_at", String(40)),
    scoped_fk("extension_id", "extensions"),
    UniqueConstraint("extension_id", "fingerprint", "tool"),
)
presets = scoped(
    "presets",
    Column("name", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
)
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
messages.append_constraint(scoped_fk("run_id", "runs"))
audit.append_constraint(UniqueConstraint("id", "agent_id"))
source_anchors.append_constraint(ForeignKeyConstraint(["manual_audit_id", "agent_id"], ["audit.id", "audit.agent_id"]))
