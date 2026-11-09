"""Recoverable execution-body pruning; frozen SQL, no live model imports."""

from alembic import op

revision = "v2_0012"
down_revision = "v2_0011"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE retention_batches (
      id VARCHAR(36) PRIMARY KEY CHECK(length(id)=36), created_at VARCHAR(40) NOT NULL,
      instance_id VARCHAR(36) NOT NULL REFERENCES instances(id),
      plan_sha256 VARCHAR(64) NOT NULL, policy VARCHAR(40) NOT NULL, cutoff VARCHAR(40) NOT NULL,
      schema_version VARCHAR(40) NOT NULL, backup_path TEXT NOT NULL, backup_sha256 VARCHAR(64) NOT NULL,
      state VARCHAR(20) NOT NULL, completed_at VARCHAR(40),
      CHECK(length(plan_sha256)=64 AND length(backup_sha256)=64 AND policy='execution-body-v1'),
      CHECK((state='prepared' AND completed_at IS NULL) OR (state IN ('complete','cancelled') AND completed_at IS NOT NULL)))""",
    """CREATE TABLE retention_targets (
      batch_id VARCHAR(36) NOT NULL REFERENCES retention_batches(id), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      run_id VARCHAR(36) NOT NULL, fingerprint VARCHAR(64) NOT NULL, retention_revision INTEGER NOT NULL,
      state VARCHAR(20) NOT NULL, pruned_at VARCHAR(40), PRIMARY KEY(batch_id,run_id),
      FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      CHECK(length(fingerprint)=64 AND retention_revision>0),
      CHECK((state IN ('pending','cancelled') AND pruned_at IS NULL) OR (state='pruned' AND pruned_at IS NOT NULL)))""",
    "CREATE INDEX ix_retention_targets_agent_id ON retention_targets(agent_id)",
    "CREATE UNIQUE INDEX ix_retention_targets_pending_run ON retention_targets(run_id) WHERE state='pending'",
    "ALTER TABLE runs ADD COLUMN body_pruned_at VARCHAR(40)",
    "ALTER TABLE runs ADD COLUMN body_pruned_plan_id VARCHAR(36) REFERENCES retention_batches(id)",
    """ALTER TABLE runs ADD COLUMN body_sha256 VARCHAR(64) CHECK(
      (body_pruned_at IS NULL AND body_pruned_plan_id IS NULL AND body_sha256 IS NULL) OR
      (body_pruned_at IS NOT NULL AND body_pruned_plan_id IS NOT NULL AND body_sha256 IS NOT NULL AND length(body_sha256)=64))""",
    "ALTER TABLE actions ADD COLUMN body_pruned_at VARCHAR(40)",
    """ALTER TABLE actions ADD COLUMN result_sha256 VARCHAR(64) CHECK(
      (body_pruned_at IS NULL AND result_sha256 IS NULL) OR
      (body_pruned_at IS NOT NULL AND result_sha256 IS NOT NULL AND length(result_sha256)=64))""",
    "ALTER TABLE run_events ADD COLUMN body_pruned_at VARCHAR(40)",
    """ALTER TABLE run_events ADD COLUMN data_sha256 VARCHAR(64) CHECK(
      (body_pruned_at IS NULL AND data_sha256 IS NULL) OR
      (body_pruned_at IS NOT NULL AND data_sha256 IS NOT NULL AND length(data_sha256)=64))""",
    """CREATE TRIGGER retention_batch_identity BEFORE UPDATE ON retention_batches WHEN
      new.id!=old.id OR new.created_at!=old.created_at OR new.instance_id!=old.instance_id OR
      new.plan_sha256!=old.plan_sha256 OR new.policy!=old.policy OR new.cutoff!=old.cutoff OR
      new.schema_version!=old.schema_version OR new.backup_path!=old.backup_path OR new.backup_sha256!=old.backup_sha256 OR
      old.state!='prepared' OR new.state NOT IN ('complete','cancelled')
      BEGIN SELECT RAISE(ABORT,'retention batch identity is immutable'); END""",
    """CREATE TRIGGER retention_target_identity BEFORE UPDATE ON retention_targets WHEN
      new.batch_id!=old.batch_id OR new.agent_id!=old.agent_id OR new.run_id!=old.run_id OR
      new.fingerprint!=old.fingerprint OR new.retention_revision!=old.retention_revision OR
      old.state!='pending' OR new.state NOT IN ('pruned','cancelled')
      BEGIN SELECT RAISE(ABORT,'retention target identity is immutable'); END""",
    """CREATE TRIGGER retention_batch_keep BEFORE DELETE ON retention_batches
      BEGIN SELECT RAISE(ABORT,'retention evidence cannot be deleted'); END""",
    """CREATE TRIGGER retention_target_keep BEFORE DELETE ON retention_targets
      BEGIN SELECT RAISE(ABORT,'retention evidence cannot be deleted'); END""",
    """CREATE TRIGGER run_body_mark BEFORE UPDATE OF body_pruned_at,body_pruned_plan_id,body_sha256 ON runs
      WHEN old.body_pruned_at IS NOT NULL
      BEGIN SELECT RAISE(ABORT,'pruned body identity is immutable'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
