"""Scoped window capture artifacts and publication constraints."""

from alembic import op

revision = "v2_0022"
down_revision = "v2_0021"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE session_captures (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_session_captures_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsession_id VARCHAR(36) NOT NULL, \n\tobject_id VARCHAR(36) NOT NULL, \n\toperation_id VARCHAR(36) NOT NULL, \n\tcontent_epoch INTEGER NOT NULL, \n\twidth INTEGER NOT NULL, \n\theight INTEGER NOT NULL, \n\tmethod VARCHAR(32) NOT NULL, \n\tCONSTRAINT pk_session_captures PRIMARY KEY (id), \n\tCONSTRAINT fk_session_captures_session_id_sessions FOREIGN KEY(session_id, agent_id) REFERENCES sessions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_session_captures_object_id_objects FOREIGN KEY(object_id, agent_id) REFERENCES objects (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_session_captures_agent_id UNIQUE (agent_id, operation_id), \n\tCONSTRAINT ck_session_captures_identity CHECK (length(operation_id)=36 AND typeof(content_epoch)='integer' AND content_epoch>0), \n\tCONSTRAINT ck_session_captures_dimensions CHECK (typeof(width)='integer' AND typeof(height)='integer' AND width BETWEEN 1 AND 8192 AND height BETWEEN 1 AND 8192 AND width*height<=16000000), \n\tCONSTRAINT ck_session_captures_method CHECK (method='print_window'), \n\tCONSTRAINT uq_session_captures_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_session_captures_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)",
    "CREATE INDEX ix_session_captures_agent_id ON session_captures (agent_id)",
    "CREATE INDEX ix_session_captures_cursor ON session_captures (agent_id, session_id, id)",
    "CREATE INDEX ix_session_captures_object ON session_captures (agent_id, object_id)",
    "CREATE TRIGGER capture_session_live BEFORE INSERT ON session_captures WHEN NOT EXISTS(SELECT 1 FROM sessions WHERE id=new.session_id AND agent_id=new.agent_id AND deleted_at IS NULL AND content_epoch=new.content_epoch) BEGIN SELECT RAISE(ABORT,'capture session changed'); END",
    "CREATE TRIGGER capture_identity BEFORE UPDATE ON session_captures BEGIN SELECT RAISE(ABORT,'capture references are immutable'); END",
    "CREATE TRIGGER capture_session_erased AFTER UPDATE OF deleted_at ON sessions WHEN new.deleted_at IS NOT NULL BEGIN DELETE FROM session_captures WHERE session_id=new.id AND agent_id=new.agent_id; END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
