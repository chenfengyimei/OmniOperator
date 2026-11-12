"""People, revision-bound evidence and memory context; frozen schema evolution."""

import hashlib
import json
import unicodedata
import uuid

from alembic import op

revision = "v2_0003"
down_revision = "v2_0002"
branch_labels = None
depends_on = None


def _hash(value: str) -> str:
    return hashlib.sha256(
        b"content\0" + " ".join(unicodedata.normalize("NFKC", value).split()).casefold().encode()
    ).hexdigest()


def _source_hash(kind: str, identity: str, quote: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", quote).split()).casefold()
    return hashlib.sha256(b"source\0" + (kind + ":" + identity + ":" + normalized).encode()).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    op.execute(
        "ALTER TABLE people ADD COLUMN deleted_at VARCHAR(40) CONSTRAINT ck_people_person_state CHECK (revision > 0 AND (owner_id IS NULL OR deleted_at IS NULL) AND (deleted_at IS NOT NULL OR length(trim(name)) BETWEEN 1 AND 100))"
    )
    op.execute(
        "ALTER TABLE accounts ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CONSTRAINT ck_accounts_account_state CHECK (revision > 0 AND length(channel) BETWEEN 1 AND 32 AND length(trim(external_id)) BETWEEN 1 AND 200)"
    )
    op.execute(
        "ALTER TABLE accounts ADD COLUMN verification VARCHAR(16) NOT NULL DEFAULT 'manual' CONSTRAINT ck_accounts_verification CHECK (verification IN ('manual','verified'))"
    )
    op.execute(
        "ALTER TABLE memory_entries ADD COLUMN content_hash VARCHAR(64) NOT NULL DEFAULT '' CONSTRAINT ck_memory_entries_document_scope CHECK (length(content_hash) IN (0,64) AND revision > 0 AND (kind NOT IN ('profile','resident') OR (person_id IS NOT NULL AND session_id IS NULL AND length(content)<=8000)))"
    )
    op.execute(
        "ALTER TABLE tombstones ADD COLUMN content_hash VARCHAR(64) NOT NULL DEFAULT '' CONSTRAINT ck_tombstones_content_hash CHECK (length(content_hash) IN (0,64))"
    )
    op.execute("ALTER TABLE source_anchors ADD COLUMN memory_revision_id VARCHAR(36)")
    op.execute(
        "ALTER TABLE source_anchors ADD COLUMN source_hash VARCHAR(64) NOT NULL DEFAULT '' CONSTRAINT ck_source_anchors_source_hash CHECK (length(source_hash) IN (0,64))"
    )
    for memory_id, content in connection.exec_driver_sql("SELECT id,content FROM memory_entries"):
        if content:
            connection.exec_driver_sql(
                "UPDATE memory_entries SET content_hash=? WHERE id=?", (_hash(content), memory_id)
            )
    connection.exec_driver_sql(
        "UPDATE tombstones SET content_hash=(SELECT content_hash FROM memory_entries WHERE id=tombstones.memory_id)"
    )
    connection.exec_driver_sql(
        "DELETE FROM source_anchors WHERE memory_id IN (SELECT id FROM memory_entries WHERE deleted_at IS NOT NULL)"
    )
    for row in connection.exec_driver_sql("SELECT * FROM source_anchors").mappings():
        source = next(
            (kind, row[column])
            for kind, column in (
                ("message", "message_id"),
                ("action", "action_id"),
                ("object", "object_id"),
                ("manual", "manual_audit_id"),
            )
            if row[column]
        )
        revision_id = connection.exec_driver_sql(
            "SELECT id FROM memory_revisions WHERE memory_id=? ORDER BY revision LIMIT 1", (row["memory_id"],)
        ).scalar_one()
        connection.exec_driver_sql(
            "UPDATE source_anchors SET memory_revision_id=?,source_hash=? WHERE id=?",
            (revision_id, _source_hash(source[0], source[1], row["quote"]), row["id"]),
        )
    op.execute("CREATE UNIQUE INDEX uq_memory_revision_identity ON memory_revisions(id,memory_id,agent_id)")
    with op.batch_alter_table("source_anchors") as batch:
        batch.drop_constraint("fk_source_anchors_manual_audit_id_audit", type_="foreignkey")
        batch.create_foreign_key(
            "fk_source_anchors_manual_audit_id_audit", "audit", ["manual_audit_id", "agent_id"], ["id", "agent_id"]
        )
        batch.alter_column("memory_revision_id", nullable=False)
        batch.create_foreign_key(
            "fk_source_anchors_revision",
            "memory_revisions",
            ["memory_revision_id", "memory_id", "agent_id"],
            ["id", "memory_id", "agent_id"],
            ondelete="RESTRICT",
        )
    old_context: list[tuple[str, object, object, object, object]] = []
    for row in connection.exec_driver_sql("SELECT id,agent_id,created_at,memory_ids FROM context_snapshots").mappings():
        for memory_id in json.loads(row["memory_ids"]):
            old_context.append((str(uuid.uuid4()), row["agent_id"], row["created_at"], row["id"], memory_id))
    with op.batch_alter_table("context_snapshots") as batch:
        batch.drop_column("memory_ids")
    for statement in NEW_TABLES:
        op.execute(statement)
    # Earlier snapshots recorded IDs but no revision. Preserve that uncertainty
    # rather than claiming the current revision was what an older model saw.
    for row in old_context:
        connection.exec_driver_sql(
            "INSERT INTO context_memories(id,agent_id,created_at,snapshot_id,memory_id,state) VALUES(?,?,?,?,?,'legacy_unresolved')",
            row,
        )
    for statement in INDEXES:
        op.execute(statement)
    for agent_id, created_at, owner_id, name in connection.exec_driver_sql(
        "SELECT a.id,a.created_at,p.id,p.name FROM agents a JOIN principals p ON p.role='owner'"
    ):
        exists = connection.exec_driver_sql(
            "SELECT id FROM people WHERE agent_id=? AND owner_id=?", (agent_id, owner_id)
        ).first()
        if not exists:
            connection.exec_driver_sql(
                "INSERT INTO people(id,agent_id,created_at,name,owner_id,revision) VALUES(?,?,?,?,?,1)",
                (str(uuid.uuid4()), agent_id, created_at, name, owner_id),
            )
    op.execute(
        "CREATE TRIGGER people_owner_identity BEFORE UPDATE OF owner_id,agent_id ON people WHEN new.owner_id IS NOT old.owner_id OR new.agent_id!=old.agent_id BEGIN SELECT RAISE(ABORT,'person identity is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER people_owner_delete BEFORE DELETE ON people WHEN old.owner_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'owner person cannot be deleted'); END"
    )
    for table in ("accounts", "memory_entries"):
        for event in ("INSERT", "UPDATE"):
            op.execute(
                f"CREATE TRIGGER {table}_live_person_{event.lower()} BEFORE {event} ON {table} WHEN new.person_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM people WHERE id=new.person_id AND agent_id=new.agent_id AND deleted_at IS NULL) BEGIN SELECT RAISE(ABORT,'person is not live in this Agent'); END"
            )


NEW_TABLES = (
    "\nCREATE TABLE context_memories (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_context_memories_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tmemory_id VARCHAR(36) NOT NULL, \n\tmemory_revision_id VARCHAR(36), \n\tstate VARCHAR(24) NOT NULL, \n\tCONSTRAINT pk_context_memories PRIMARY KEY (id), \n\tCONSTRAINT fk_context_memories_snapshot_id_context_snapshots FOREIGN KEY(snapshot_id, agent_id) REFERENCES context_snapshots (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_context_memories_memory_id_memory_entries FOREIGN KEY(memory_id, agent_id) REFERENCES memory_entries (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_context_memories_memory_revision_id_memory_revisions FOREIGN KEY(memory_revision_id, memory_id, agent_id) REFERENCES memory_revisions (id, memory_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_context_memories_snapshot_id UNIQUE (snapshot_id, memory_id), \n\tCONSTRAINT ck_context_memories_resolution CHECK ((state='captured' AND memory_revision_id IS NOT NULL) OR (state='legacy_unresolved' AND memory_revision_id IS NULL)), \n\tCONSTRAINT uq_context_memories_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_context_memories_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE tombstone_sources (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_tombstone_sources_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\ttombstone_id VARCHAR(36) NOT NULL, \n\tsource_hash VARCHAR(64) NOT NULL CONSTRAINT ck_tombstone_sources_source_hash CHECK (length(source_hash) = 64), \n\tCONSTRAINT pk_tombstone_sources PRIMARY KEY (id), \n\tCONSTRAINT fk_tombstone_sources_tombstone_id_tombstones FOREIGN KEY(tombstone_id, agent_id) REFERENCES tombstones (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_tombstone_sources_tombstone_id UNIQUE (tombstone_id, source_hash), \n\tCONSTRAINT uq_tombstone_sources_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_tombstone_sources_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
)
INDEXES = (
    "CREATE INDEX ix_context_memories_agent_id ON context_memories (agent_id)",
    "CREATE INDEX ix_context_memories_snapshot ON context_memories (agent_id, snapshot_id)",
    "CREATE INDEX ix_tombstone_sources_agent_id ON tombstone_sources (agent_id)",
    "CREATE INDEX ix_tombstone_sources_hash ON tombstone_sources (agent_id, source_hash)",
    "CREATE INDEX ix_people_cursor ON people (agent_id, deleted_at, id)",
    "CREATE UNIQUE INDEX uq_people_owner ON people (agent_id, owner_id)",
    "CREATE INDEX ix_memory_person ON memory_entries (agent_id, person_id, deleted_at)",
    "CREATE UNIQUE INDEX uq_memory_document ON memory_entries (agent_id, person_id, kind) WHERE deleted_at IS NULL AND kind IN ('profile', 'resident')",
    "CREATE INDEX ix_tombstones_content ON tombstones (agent_id, content_hash)",
)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
