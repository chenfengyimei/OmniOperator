"""Align event_retention_targets kind whitelist with domain KINDS (action.executing/model.started/model.completed); frozen DDL, transactional rebuild preserving every evidence row, index and trigger."""

from alembic import op

revision = "v2_0048"
down_revision = "v2_0047"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE event_retention_targets RENAME TO _v48_event_retention_targets",
    """CREATE TABLE event_retention_targets (
      batch_id VARCHAR(36) NOT NULL, sequence BIGINT NOT NULL,
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
      run_id VARCHAR(36) NOT NULL, seq BIGINT NOT NULL, kind VARCHAR(80) NOT NULL,
      run_retention_revision BIGINT NOT NULL, prune_plan_id VARCHAR(36) NOT NULL REFERENCES retention_batches(id),
      body_sha256 VARCHAR(64) NOT NULL, fingerprint VARCHAR(64) NOT NULL, state VARCHAR(20) NOT NULL, ended_at VARCHAR(40),
      PRIMARY KEY(batch_id,sequence), UNIQUE(batch_id,run_id,seq),
      FOREIGN KEY(batch_id,agent_id) REFERENCES event_retention_batches(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      CHECK(length(body_sha256)=64 AND length(fingerprint)=64),
      CHECK(typeof(sequence)='integer' AND sequence BETWEEN 1 AND 9223372036854775807 AND
        typeof(seq)='integer' AND seq BETWEEN 1 AND 9223372036854775807 AND
        typeof(run_retention_revision)='integer' AND run_retention_revision BETWEEN 1 AND 9223372036854775807),
      CHECK(kind IN ('run.queued','run.running','run.waiting_approval','run.cancelling','run.succeeded',
        'run.failed','run.cancelled','action.prepared','action.executing','action.completed',
        'model.started','model.completed')),
      CHECK((state='pending' AND ended_at IS NULL) OR
        (state IN ('deleted','cancelled') AND ended_at IS NOT NULL)))""",
    "INSERT INTO event_retention_targets(batch_id,sequence,agent_id,run_id,seq,kind,run_retention_revision,"
    "prune_plan_id,body_sha256,fingerprint,state,ended_at) "
    "SELECT batch_id,sequence,agent_id,run_id,seq,kind,run_retention_revision,"
    "prune_plan_id,body_sha256,fingerprint,state,ended_at FROM _v48_event_retention_targets",
    "DROP TABLE _v48_event_retention_targets",
    "CREATE INDEX ix_event_retention_targets_agent_id ON event_retention_targets(agent_id)",
    "CREATE UNIQUE INDEX ix_event_retention_pending_sequence ON event_retention_targets(sequence) WHERE state='pending'",
    """CREATE TRIGGER event_retention_target_identity BEFORE UPDATE ON event_retention_targets WHEN
      new.batch_id IS NOT old.batch_id OR new.sequence IS NOT old.sequence OR new.agent_id IS NOT old.agent_id OR
      new.run_id IS NOT old.run_id OR new.seq IS NOT old.seq OR new.kind IS NOT old.kind OR
      new.run_retention_revision IS NOT old.run_retention_revision OR new.prune_plan_id IS NOT old.prune_plan_id OR
      new.body_sha256 IS NOT old.body_sha256 OR new.fingerprint IS NOT old.fingerprint OR
      old.state!='pending' OR new.state NOT IN ('deleted','cancelled')
      BEGIN SELECT RAISE(ABORT,'event retention target identity is immutable'); END""",
    """CREATE TRIGGER event_retention_target_keep BEFORE DELETE ON event_retention_targets
      BEGIN SELECT RAISE(ABORT,'event retention evidence cannot be deleted'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
