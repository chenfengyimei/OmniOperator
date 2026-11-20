"""Memory decay columns and per-agent decay cursor; frozen SQL, idempotent add-column (v0045 precedent)."""

from alembic import op

revision = "v2_0047"
down_revision = "v2_0046"
branch_labels = None
depends_on = None

_ADD = (
    ("importance", "ALTER TABLE memory_entries ADD COLUMN importance INTEGER NOT NULL DEFAULT 50"),
    ("clarity", "ALTER TABLE memory_entries ADD COLUMN clarity VARCHAR(12) NOT NULL DEFAULT 'vivid'"),
    ("recall_count", "ALTER TABLE memory_entries ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0"),
    ("decay_steps", "ALTER TABLE memory_entries ADD COLUMN decay_steps INTEGER NOT NULL DEFAULT 0"),
    ("pinned", "ALTER TABLE memory_entries ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0"),
    ("last_seen_at", "ALTER TABLE memory_entries ADD COLUMN last_seen_at VARCHAR(40)"),
)

_CREATE = (
    """CREATE TABLE memory_decay_state (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
      created_at VARCHAR(40) NOT NULL,
      last_decay_date VARCHAR(10),
      last_decay_at VARCHAR(40),
      decayed INTEGER NOT NULL DEFAULT 0,
      forgotten INTEGER NOT NULL DEFAULT 0,
      UNIQUE(id,agent_id),
      UNIQUE(agent_id),
      CHECK(decayed>=0 AND forgotten>=0 AND (last_decay_date IS NULL OR length(last_decay_date)=10)))""",
    "CREATE INDEX ix_memory_decay_state_agent_id ON memory_decay_state(agent_id)",
    """CREATE TRIGGER memory_decay_identity BEFORE UPDATE ON memory_decay_state WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at
      BEGIN SELECT RAISE(ABORT,'decay state identity is immutable'); END""",
    "CREATE TRIGGER memory_decay_keep BEFORE DELETE ON memory_decay_state BEGIN SELECT RAISE(ABORT,'decay state cannot be deleted'); END",
)


def upgrade() -> None:
    bind = op.get_bind()
    cols = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(memory_entries)")}
    for name, statement in _ADD:
        if name not in cols:
            op.execute(statement)
    tables = {row[0] for row in bind.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    if "memory_decay_state" not in tables:
        for statement in _CREATE:
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
