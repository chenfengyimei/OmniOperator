"""Bounded post-turn review batches and revocable pending proposals; frozen SQL, no current schema imports."""

from alembic import op

revision = "v2_0043"
down_revision = "v2_0042"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE background_review_batches (
      sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      id VARCHAR(36) NOT NULL CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,created_at VARCHAR(40) NOT NULL,
      run_id VARCHAR(36) NOT NULL,request_key VARCHAR(200) NOT NULL,request_sha256 VARCHAR(64) NOT NULL,
      policy VARCHAR(48) NOT NULL,history_epoch VARCHAR(36) NOT NULL,budget_micro INTEGER NOT NULL,
      call_count INTEGER NOT NULL,candidate_count INTEGER NOT NULL,state VARCHAR(20) NOT NULL,
      revision INTEGER NOT NULL,updated_at VARCHAR(40) NOT NULL,prepared_at VARCHAR(40),
      error_code VARCHAR(80) NOT NULL DEFAULT '',
      UNIQUE(id),UNIQUE(id,agent_id),UNIQUE(agent_id,request_key),UNIQUE(agent_id,run_id),
      FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      CHECK(sequence>0 AND length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64 AND
        policy='post-turn-review-v1' AND length(history_epoch)=36 AND length(error_code)<=80),
      CHECK(typeof(budget_micro)='integer' AND budget_micro>=0 AND
        typeof(call_count)='integer' AND call_count BETWEEN 0 AND 8 AND
        typeof(candidate_count)='integer' AND candidate_count BETWEEN 0 AND 16 AND
        typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807),
      CHECK(state IN ('queued','running','ready','complete','cancelled','invalidated','failed') AND
        (state NOT IN ('ready','complete') OR prepared_at IS NOT NULL)))""",
    "CREATE INDEX ix_background_review_batches_agent_id ON background_review_batches(agent_id)",
    "CREATE INDEX ix_background_review_batches_agent_sequence ON background_review_batches(agent_id,sequence)",
    """CREATE TABLE background_review_candidates (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,created_at VARCHAR(40) NOT NULL,
      batch_id VARCHAR(36) NOT NULL,position INTEGER NOT NULL,kind VARCHAR(10) NOT NULL,tool VARCHAR(40) NOT NULL,
      proposal TEXT NOT NULL,state VARCHAR(20) NOT NULL,revision INTEGER NOT NULL,fingerprint VARCHAR(64) NOT NULL,
      updated_at VARCHAR(40) NOT NULL,reviewed_at VARCHAR(40),review_audit_id VARCHAR(36),
      applied_memory_id VARCHAR(36),applied_memory_revision INTEGER,applied_skill_id VARCHAR(36),
      applied_skill_revision_id VARCHAR(36),error_code VARCHAR(80) NOT NULL DEFAULT '',
      UNIQUE(id,agent_id),UNIQUE(batch_id,position),
      FOREIGN KEY(batch_id,agent_id) REFERENCES background_review_batches(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(review_audit_id,agent_id) REFERENCES audit(id,agent_id) ON DELETE RESTRICT,
      CHECK(kind IN ('memory','skill') AND length(tool) BETWEEN 1 AND 40 AND length(fingerprint)=64 AND
        typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND
        typeof(position)='integer' AND position BETWEEN 0 AND 15 AND length(error_code)<=80),
      CHECK((state='pending' AND reviewed_at IS NULL AND review_audit_id IS NULL) OR
        (state IN ('applied','rejected','revoked') AND reviewed_at IS NOT NULL AND review_audit_id IS NOT NULL) OR
        (state IN ('invalidated','cancelled') AND ((reviewed_at IS NULL AND review_audit_id IS NULL) OR
        (reviewed_at IS NOT NULL AND review_audit_id IS NOT NULL)))))""",
    "CREATE INDEX ix_background_review_candidates_agent_id ON background_review_candidates(agent_id)",
    "CREATE INDEX ix_background_review_candidates_batch ON background_review_candidates(agent_id,batch_id)",
    "CREATE INDEX ix_background_review_review_audit ON background_review_candidates(agent_id,review_audit_id)",
    """CREATE TRIGGER background_review_batch_identity BEFORE UPDATE ON background_review_batches WHEN
      new.sequence IS NOT old.sequence OR new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR
      new.created_at IS NOT old.created_at OR new.run_id IS NOT old.run_id OR
      new.request_key IS NOT old.request_key OR new.request_sha256 IS NOT old.request_sha256 OR
      new.policy IS NOT old.policy OR new.history_epoch IS NOT old.history_epoch OR
      new.revision!=old.revision+1 OR
      (old.state!='queued' AND new.call_count<old.call_count) OR
      (old.state='queued' AND new.state NOT IN ('queued','running','ready','complete','cancelled','invalidated','failed')) OR
      (old.state='running' AND new.state NOT IN ('running','ready','complete','cancelled','invalidated','failed')) OR
      (old.state='ready' AND new.state NOT IN ('ready','complete','cancelled','invalidated')) OR
      (old.state='complete' AND new.state NOT IN ('complete','invalidated')) OR
      (old.state='cancelled' AND new.state NOT IN ('cancelled','invalidated')) OR old.state IN ('invalidated','failed')
      BEGIN SELECT RAISE(ABORT,'background review batch identity and progress are immutable'); END""",
    """CREATE TRIGGER background_review_candidate_identity BEFORE UPDATE ON background_review_candidates WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
      new.batch_id IS NOT old.batch_id OR new.position IS NOT old.position OR new.kind IS NOT old.kind OR
      new.tool IS NOT old.tool OR new.proposal IS NOT old.proposal OR new.fingerprint IS NOT old.fingerprint OR
      new.revision!=old.revision+1 OR
      (old.state='pending' AND new.state NOT IN ('applied','rejected','cancelled','invalidated')) OR
      (old.state='applied' AND new.state NOT IN ('revoked','invalidated')) OR
      old.state IN ('rejected','revoked','invalidated','cancelled')
      BEGIN SELECT RAISE(ABORT,'background review candidate identity and decisions are immutable'); END""",
    "CREATE TRIGGER background_review_batch_keep BEFORE DELETE ON background_review_batches BEGIN SELECT RAISE(ABORT,'background review evidence cannot be deleted'); END",
    "CREATE TRIGGER background_review_candidate_keep BEFORE DELETE ON background_review_candidates BEGIN SELECT RAISE(ABORT,'background review evidence cannot be deleted'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
