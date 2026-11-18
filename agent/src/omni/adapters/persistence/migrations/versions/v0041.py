"""Session planning snapshots; frozen SQL and no current application imports."""

from alembic import op

revision = "v2_0041"
down_revision = "v2_0040"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE session_todos (id VARCHAR(36) NOT NULL, agent_id VARCHAR(36) NOT NULL, created_at VARCHAR(40) NOT NULL, session_id VARCHAR(36) NOT NULL, session_epoch INTEGER NOT NULL, revision INTEGER NOT NULL, items JSON NOT NULL, updated_at VARCHAR(40) NOT NULL, CONSTRAINT pk_session_todos PRIMARY KEY(id), CONSTRAINT ck_session_todos_uuid_length CHECK(length(id)=36), CONSTRAINT ck_session_todos_bounds CHECK(typeof(revision)='integer' AND revision>0 AND typeof(session_epoch)='integer' AND session_epoch>0 AND json_valid(items) AND json_type(items)='array' AND json_array_length(items)<=256 AND length(CAST(items AS BLOB))<=65536), CONSTRAINT fk_session_todos_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE RESTRICT, CONSTRAINT fk_session_todos_session_id_sessions FOREIGN KEY(session_id,agent_id) REFERENCES sessions(id,agent_id) ON DELETE RESTRICT, CONSTRAINT uq_session_todos_id_agent_id UNIQUE(id,agent_id), CONSTRAINT uq_session_todos_session_id UNIQUE(session_id))",
    "CREATE INDEX ix_session_todos_agent_id ON session_todos(agent_id)",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
