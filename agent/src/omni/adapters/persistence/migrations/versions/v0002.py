"""Object pin revisions and action artifact references; frozen SQL, no current schema imports."""

from alembic import op

revision = "v2_0002"
down_revision = "v2_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE objects ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CONSTRAINT ck_objects_revision CHECK (revision > 0)"
    )
    op.execute("""CREATE TABLE action_objects (
        id VARCHAR(36) NOT NULL CONSTRAINT ck_action_objects_uuid_length CHECK(length(id) = 36),
        agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
        created_at VARCHAR(40) NOT NULL,
        action_id VARCHAR(36) NOT NULL,
        object_id VARCHAR(36) NOT NULL,
        CONSTRAINT pk_action_objects PRIMARY KEY(id),
        CONSTRAINT uq_action_objects_id UNIQUE(id,agent_id),
        CONSTRAINT uq_action_objects_action_id UNIQUE(action_id,object_id),
        FOREIGN KEY(action_id,agent_id) REFERENCES actions(id,agent_id) ON DELETE RESTRICT,
        FOREIGN KEY(object_id,agent_id) REFERENCES objects(id,agent_id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_action_objects_agent_id ON action_objects(agent_id)")
    op.execute("CREATE INDEX ix_objects_agent_cursor ON objects(agent_id,id)")
    op.execute("CREATE INDEX ix_message_parts_object ON message_parts(agent_id,object_id)")
    op.execute("CREATE INDEX ix_source_anchors_object ON source_anchors(agent_id,object_id)")
    op.execute("CREATE INDEX ix_skill_revisions_object ON skill_revisions(agent_id,object_id)")
    op.execute("CREATE INDEX ix_action_objects_object ON action_objects(agent_id,object_id)")


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
