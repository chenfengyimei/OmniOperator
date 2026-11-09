"""Historical reference-release evidence never points back to live object rows."""

from alembic import op

revision = "v2_0013"
down_revision = "v2_0012"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE reference_release_batches (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36), created_at VARCHAR(40) NOT NULL,
      instance_id VARCHAR(36) NOT NULL REFERENCES instances(id), plan_sha256 VARCHAR(64) NOT NULL,
      policy VARCHAR(48) NOT NULL, schema_version VARCHAR(40) NOT NULL,
      backup_path TEXT NOT NULL, backup_sha256 VARCHAR(64) NOT NULL, state VARCHAR(20) NOT NULL,
      completed_at VARCHAR(40), CHECK(length(plan_sha256)=64 AND length(backup_sha256)=64 AND policy='execution-artifact-reference-v1'),
      CHECK((state='prepared' AND completed_at IS NULL) OR (state IN ('complete','cancelled') AND completed_at IS NOT NULL)))""",
    """CREATE TABLE reference_release_targets (
      batch_id VARCHAR(36) NOT NULL REFERENCES reference_release_batches(id), reference_id VARCHAR(36) NOT NULL,
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT, run_id VARCHAR(36) NOT NULL, action_id VARCHAR(36) NOT NULL,
      object_id VARCHAR(36) NOT NULL, object_revision INTEGER NOT NULL, object_sha256 VARCHAR(64) NOT NULL, object_bytes BIGINT NOT NULL,
      prune_plan_id VARCHAR(36) NOT NULL REFERENCES retention_batches(id), fingerprint VARCHAR(64) NOT NULL,
      state VARCHAR(20) NOT NULL, ended_at VARCHAR(40), PRIMARY KEY(batch_id,reference_id),
      FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(action_id,agent_id) REFERENCES actions(id,agent_id) ON DELETE RESTRICT,
      CHECK(length(reference_id)=36 AND length(object_id)=36 AND object_revision>0 AND length(fingerprint)=64 AND length(object_sha256)=64 AND object_bytes>=0),
      CHECK((state='pending' AND ended_at IS NULL) OR (state IN ('released','cancelled') AND ended_at IS NOT NULL)))""",
    "CREATE INDEX ix_reference_release_targets_agent_id ON reference_release_targets(agent_id)",
    "CREATE UNIQUE INDEX ix_reference_release_pending ON reference_release_targets(reference_id) WHERE state='pending'",
    """CREATE TRIGGER reference_release_batch_identity BEFORE UPDATE ON reference_release_batches WHEN
      new.id!=old.id OR new.created_at!=old.created_at OR new.instance_id!=old.instance_id OR new.plan_sha256!=old.plan_sha256 OR
      new.policy!=old.policy OR new.schema_version!=old.schema_version OR new.backup_path!=old.backup_path OR
      new.backup_sha256!=old.backup_sha256 OR old.state!='prepared' OR new.state NOT IN ('complete','cancelled')
      BEGIN SELECT RAISE(ABORT,'reference release batch identity is immutable'); END""",
    """CREATE TRIGGER reference_release_target_identity BEFORE UPDATE ON reference_release_targets WHEN
      new.batch_id!=old.batch_id OR new.reference_id!=old.reference_id OR new.agent_id!=old.agent_id OR new.run_id!=old.run_id OR
      new.action_id!=old.action_id OR new.object_id!=old.object_id OR new.object_revision!=old.object_revision OR
      new.object_sha256!=old.object_sha256 OR new.object_bytes!=old.object_bytes OR
      new.prune_plan_id!=old.prune_plan_id OR new.fingerprint!=old.fingerprint OR
      old.state!='pending' OR new.state NOT IN ('released','cancelled')
      BEGIN SELECT RAISE(ABORT,'reference release target identity is immutable'); END""",
    """CREATE TRIGGER reference_release_batch_keep BEFORE DELETE ON reference_release_batches
      BEGIN SELECT RAISE(ABORT,'reference release evidence cannot be deleted'); END""",
    """CREATE TRIGGER reference_release_target_keep BEFORE DELETE ON reference_release_targets
      BEGIN SELECT RAISE(ABORT,'reference release evidence cannot be deleted'); END""",
    """CREATE TRIGGER pruned_action_no_new_object BEFORE INSERT ON action_objects WHEN EXISTS(
      SELECT 1 FROM actions WHERE id=new.action_id AND body_pruned_at IS NOT NULL)
      BEGIN SELECT RAISE(ABORT,'pruned actions cannot acquire new artifacts'); END""",
    """CREATE TRIGGER pruned_action_no_changed_object BEFORE UPDATE ON action_objects WHEN EXISTS(
      SELECT 1 FROM actions WHERE id IN (old.action_id,new.action_id) AND body_pruned_at IS NOT NULL)
      BEGIN SELECT RAISE(ABORT,'pruned artifact references cannot change identity'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
