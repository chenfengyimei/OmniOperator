"""Persistent sequence allocation and scoped cursor identity precede all history deletion."""

import uuid

from alembic import op

revision = "v2_0014"
down_revision = "v2_0013"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE history_identity(slot INTEGER NOT NULL PRIMARY KEY CHECK(slot=1), epoch VARCHAR(36) NOT NULL CHECK(length(epoch)=36))",
    "CREATE TABLE history_sequences(kind VARCHAR(20) NOT NULL PRIMARY KEY CHECK(kind IN ('events','audit')), last_issued BIGINT NOT NULL CHECK(last_issued BETWEEN 0 AND 9223372036854775807))",
    "INSERT INTO history_sequences SELECT 'events',COALESCE(MAX(sequence),0) FROM run_events",
    "INSERT INTO history_sequences SELECT 'audit',COALESCE(MAX(sequence),0) FROM audit",
    """CREATE TABLE history_scopes(
      kind VARCHAR(20) NOT NULL, scope_id VARCHAR(36) NOT NULL,
      agent_id VARCHAR(36) REFERENCES agents(id) ON DELETE RESTRICT,
      instance_id VARCHAR(36) REFERENCES instances(id) ON DELETE RESTRICT,
      trim_revision BIGINT NOT NULL,deleted_count BIGINT NOT NULL,last_trim_at VARCHAR(40),PRIMARY KEY(kind,scope_id),
      CHECK((kind IN ('events','audit') AND agent_id IS NOT NULL AND scope_id=agent_id AND instance_id IS NULL) OR
      (kind='instance_audit' AND instance_id IS NOT NULL AND scope_id=instance_id AND agent_id IS NULL)),
      CHECK(trim_revision>=0 AND deleted_count>=0 AND ((trim_revision=0 AND deleted_count=0 AND last_trim_at IS NULL) OR
      (trim_revision>0 AND deleted_count>0 AND last_trim_at IS NOT NULL))))""",
    "INSERT INTO history_scopes(kind,scope_id,agent_id,trim_revision,deleted_count) SELECT 'events',id,id,0,0 FROM agents",
    "INSERT INTO history_scopes(kind,scope_id,agent_id,trim_revision,deleted_count) SELECT 'audit',id,id,0,0 FROM agents",
    "INSERT INTO history_scopes(kind,scope_id,instance_id,trim_revision,deleted_count) SELECT 'instance_audit',id,id,0,0 FROM instances",
    """CREATE TRIGGER history_new_agent AFTER INSERT ON agents BEGIN
      INSERT INTO history_scopes(kind,scope_id,agent_id,trim_revision,deleted_count) VALUES('events',new.id,new.id,0,0);
      INSERT INTO history_scopes(kind,scope_id,agent_id,trim_revision,deleted_count) VALUES('audit',new.id,new.id,0,0); END""",
    """CREATE TRIGGER history_new_instance AFTER INSERT ON instances BEGIN
      INSERT INTO history_scopes(kind,scope_id,instance_id,trim_revision,deleted_count) VALUES('instance_audit',new.id,new.id,0,0); END""",
    """CREATE TRIGGER history_sequence_monotonic BEFORE UPDATE ON history_sequences WHEN
      new.kind!=old.kind OR new.last_issued<old.last_issued
      BEGIN SELECT RAISE(ABORT,'history allocation cannot move backwards'); END""",
    """CREATE TRIGGER history_sequence_keep BEFORE DELETE ON history_sequences
      BEGIN SELECT RAISE(ABORT,'history allocation cannot be deleted'); END""",
    """CREATE TRIGGER history_scope_monotonic BEFORE UPDATE ON history_scopes WHEN
      new.kind!=old.kind OR new.scope_id!=old.scope_id OR new.agent_id IS NOT old.agent_id OR
      new.instance_id IS NOT old.instance_id OR new.trim_revision<old.trim_revision OR new.deleted_count<old.deleted_count
      BEGIN SELECT RAISE(ABORT,'history scope identity and progress cannot move backwards'); END""",
    """CREATE TRIGGER history_scope_keep BEFORE DELETE ON history_scopes
      BEGIN SELECT RAISE(ABORT,'history scope cannot be deleted'); END""",
    """CREATE TRIGGER history_identity_keep BEFORE DELETE ON history_identity
      BEGIN SELECT RAISE(ABORT,'history identity cannot be deleted'); END""",
    """CREATE TRIGGER history_trim_audit AFTER DELETE ON audit BEGIN
      UPDATE history_scopes SET trim_revision=trim_revision+1,deleted_count=deleted_count+1,
      last_trim_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
      WHERE kind='audit' AND scope_id=old.agent_id; END""",
    """CREATE TRIGGER history_trim_events AFTER DELETE ON run_events BEGIN
      UPDATE history_scopes SET trim_revision=trim_revision+1,deleted_count=deleted_count+1,
      last_trim_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
      WHERE kind='events' AND scope_id=old.agent_id; END""",
    """CREATE TRIGGER history_trim_instance_audit AFTER DELETE ON instance_audit BEGIN
      UPDATE history_scopes SET trim_revision=trim_revision+1,deleted_count=deleted_count+1,
      last_trim_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
      WHERE kind='instance_audit'; END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)
    op.get_bind().exec_driver_sql("INSERT INTO history_identity(slot,epoch) VALUES(1,?)", (str(uuid.uuid4()),))


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
