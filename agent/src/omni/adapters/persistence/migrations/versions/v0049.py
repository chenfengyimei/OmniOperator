"""Scheduled skill bindings and task output references; frozen SQL, idempotent add-column (v0047 precedent)."""

from alembic import op

revision = "v2_0049"
down_revision = "v2_0048"
branch_labels = None
depends_on = None

_COLUMN = (
    "continuity",
    "ALTER TABLE schedule_revisions ADD COLUMN continuity INTEGER NOT NULL DEFAULT 0 CHECK(continuity IN (0,1))",
)

_OBJECTS = (
    (
        "schedule_skills",
        """CREATE TABLE schedule_skills (
          id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
          agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
          created_at VARCHAR(40) NOT NULL,
          schedule_id VARCHAR(36) NOT NULL,
          revision INTEGER NOT NULL,
          position INTEGER NOT NULL,
          skill_id VARCHAR(36) NOT NULL,
          revision_id VARCHAR(36) NOT NULL,
          UNIQUE(id,agent_id),
          UNIQUE(schedule_id,revision,position),
          FOREIGN KEY(schedule_id,revision,agent_id)
            REFERENCES schedule_revisions(schedule_id,revision,agent_id) ON DELETE RESTRICT,
          FOREIGN KEY(skill_id,agent_id) REFERENCES skills(id,agent_id) ON DELETE RESTRICT,
          FOREIGN KEY(revision_id,agent_id) REFERENCES skill_revisions(id,agent_id) ON DELETE RESTRICT,
          CHECK(revision>0 AND position BETWEEN 1 AND 4))""",
    ),
    ("ix_schedule_skills_agent_id", "CREATE INDEX ix_schedule_skills_agent_id ON schedule_skills(agent_id)"),
    (
        "schedule_skill_identity",
        """CREATE TRIGGER schedule_skill_identity BEFORE UPDATE ON schedule_skills WHEN
          new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
          new.schedule_id IS NOT old.schedule_id OR new.revision IS NOT old.revision OR
          new.position IS NOT old.position OR new.skill_id IS NOT old.skill_id OR
          new.revision_id IS NOT old.revision_id
          BEGIN SELECT RAISE(ABORT,'schedule skill binding identity is immutable'); END""",
    ),
    (
        "schedule_skill_keep",
        "CREATE TRIGGER schedule_skill_keep BEFORE DELETE ON schedule_skills "
        "BEGIN SELECT RAISE(ABORT,'schedule skill bindings cannot be deleted'); END",
    ),
    (
        "schedule_context_refs",
        """CREATE TABLE schedule_context_refs (
          id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
          agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
          created_at VARCHAR(40) NOT NULL,
          schedule_id VARCHAR(36) NOT NULL,
          revision INTEGER NOT NULL,
          position INTEGER NOT NULL,
          ref_schedule_id VARCHAR(36) NOT NULL,
          UNIQUE(id,agent_id),
          UNIQUE(schedule_id,revision,position),
          FOREIGN KEY(schedule_id,revision,agent_id)
            REFERENCES schedule_revisions(schedule_id,revision,agent_id) ON DELETE RESTRICT,
          FOREIGN KEY(ref_schedule_id,agent_id) REFERENCES schedules(id,agent_id) ON DELETE RESTRICT,
          CHECK(revision>0 AND position BETWEEN 1 AND 4 AND ref_schedule_id<>schedule_id))""",
    ),
    (
        "ix_schedule_context_refs_agent_id",
        "CREATE INDEX ix_schedule_context_refs_agent_id ON schedule_context_refs(agent_id)",
    ),
    (
        "schedule_context_ref_identity",
        """CREATE TRIGGER schedule_context_ref_identity BEFORE UPDATE ON schedule_context_refs WHEN
          new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
          new.schedule_id IS NOT old.schedule_id OR new.revision IS NOT old.revision OR
          new.position IS NOT old.position OR new.ref_schedule_id IS NOT old.ref_schedule_id
          BEGIN SELECT RAISE(ABORT,'schedule context reference identity is immutable'); END""",
    ),
    (
        "schedule_context_ref_keep",
        "CREATE TRIGGER schedule_context_ref_keep BEFORE DELETE ON schedule_context_refs "
        "BEGIN SELECT RAISE(ABORT,'schedule context references cannot be deleted'); END",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(schedule_revisions)")}
    name, statement = _COLUMN
    if name not in columns:
        op.execute(statement)
    existing = {row[0] for row in bind.exec_driver_sql("SELECT name FROM sqlite_master")}
    for name, statement in _OBJECTS:
        if name not in existing:
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
