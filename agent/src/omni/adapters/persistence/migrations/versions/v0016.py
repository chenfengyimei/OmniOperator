"""Immutable event-retention plans and deletion evidence; never retain the deleted event rows."""

from alembic import op

revision = "v2_0016"
down_revision = "v2_0015"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE event_retention_batches (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT, created_at VARCHAR(40) NOT NULL,
      instance_id VARCHAR(36) NOT NULL REFERENCES instances(id), plan_sha256 VARCHAR(64) NOT NULL,
      policy VARCHAR(48) NOT NULL, schema_version VARCHAR(40) NOT NULL, as_of VARCHAR(40) NOT NULL,
      cutoff VARCHAR(40) NOT NULL, epoch VARCHAR(36) NOT NULL, trim_revision BIGINT NOT NULL, high_water BIGINT NOT NULL,
      backup_path TEXT NOT NULL, backup_sha256 VARCHAR(64) NOT NULL, state VARCHAR(20) NOT NULL, completed_at VARCHAR(40),
      UNIQUE(id,agent_id),
      CHECK(length(plan_sha256)=64 AND length(backup_sha256)=64 AND length(epoch)=36 AND policy='agent-event-retention-v1'),
      CHECK(typeof(trim_revision)='integer' AND trim_revision BETWEEN 0 AND 9223372036854775807 AND
        typeof(high_water)='integer' AND high_water BETWEEN 0 AND 9223372036854775807),
      CHECK((state='prepared' AND completed_at IS NULL) OR
        (state IN ('complete','cancelled') AND completed_at IS NOT NULL)))""",
    "CREATE INDEX ix_event_retention_batches_agent_id ON event_retention_batches(agent_id)",
    "CREATE UNIQUE INDEX ix_event_retention_prepared_agent ON event_retention_batches(agent_id) WHERE state='prepared'",
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
        'run.failed','run.cancelled','action.prepared','action.completed')),
      CHECK((state='pending' AND ended_at IS NULL) OR
        (state IN ('deleted','cancelled') AND ended_at IS NOT NULL)))""",
    "CREATE INDEX ix_event_retention_targets_agent_id ON event_retention_targets(agent_id)",
    "CREATE UNIQUE INDEX ix_event_retention_pending_sequence ON event_retention_targets(sequence) WHERE state='pending'",
    """CREATE TRIGGER event_retention_batch_identity BEFORE UPDATE ON event_retention_batches WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
      new.instance_id IS NOT old.instance_id OR new.plan_sha256 IS NOT old.plan_sha256 OR new.policy IS NOT old.policy OR
      new.schema_version IS NOT old.schema_version OR new.as_of IS NOT old.as_of OR new.cutoff IS NOT old.cutoff OR
      new.epoch IS NOT old.epoch OR new.trim_revision IS NOT old.trim_revision OR new.high_water IS NOT old.high_water OR
      new.backup_path IS NOT old.backup_path OR new.backup_sha256 IS NOT old.backup_sha256 OR
      old.state!='prepared' OR new.state NOT IN ('complete','cancelled')
      BEGIN SELECT RAISE(ABORT,'event retention batch identity is immutable'); END""",
    """CREATE TRIGGER event_retention_target_identity BEFORE UPDATE ON event_retention_targets WHEN
      new.batch_id IS NOT old.batch_id OR new.sequence IS NOT old.sequence OR new.agent_id IS NOT old.agent_id OR
      new.run_id IS NOT old.run_id OR new.seq IS NOT old.seq OR new.kind IS NOT old.kind OR
      new.run_retention_revision IS NOT old.run_retention_revision OR new.prune_plan_id IS NOT old.prune_plan_id OR
      new.body_sha256 IS NOT old.body_sha256 OR new.fingerprint IS NOT old.fingerprint OR
      old.state!='pending' OR new.state NOT IN ('deleted','cancelled')
      BEGIN SELECT RAISE(ABORT,'event retention target identity is immutable'); END""",
    """CREATE TRIGGER event_retention_batch_keep BEFORE DELETE ON event_retention_batches
      BEGIN SELECT RAISE(ABORT,'event retention evidence cannot be deleted'); END""",
    """CREATE TRIGGER event_retention_target_keep BEFORE DELETE ON event_retention_targets
      BEGIN SELECT RAISE(ABORT,'event retention evidence cannot be deleted'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
