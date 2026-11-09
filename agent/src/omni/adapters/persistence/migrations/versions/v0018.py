"""Exact fact-duplicate organization stores immutable identities and revocable review relationships, never text."""

from alembic import op

revision = "v2_0018"
down_revision = "v2_0017"
branch_labels = None
depends_on = None


def _invalidate(predicate: str) -> str:
    return f"""
      UPDATE memory_organization_batches SET state='invalidated',revision=revision+1,
        updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),error_code='memory_organization_source_changed'
      WHERE state IN ('queued','ready','complete','cancelled') AND id IN (
        SELECT s.batch_id FROM memory_organization_sources s WHERE {predicate});
      UPDATE memory_organization_candidates SET state='invalidated',revision=revision+1,
        updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),error_code='memory_organization_source_changed'
      WHERE state IN ('pending','applied') AND batch_id IN (
        SELECT s.batch_id FROM memory_organization_sources s WHERE {predicate});
    """


SQL = (
    """CREATE TABLE memory_organization_batches (
      sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      id VARCHAR(36) NOT NULL CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,created_at VARCHAR(40) NOT NULL,
      request_key VARCHAR(200) NOT NULL,request_sha256 VARCHAR(64) NOT NULL,policy VARCHAR(48) NOT NULL,
      history_epoch VARCHAR(36) NOT NULL,budget_micro INTEGER NOT NULL,source_count INTEGER NOT NULL,
      candidate_count INTEGER NOT NULL,state VARCHAR(20) NOT NULL,revision INTEGER NOT NULL,
      updated_at VARCHAR(40) NOT NULL,prepared_at VARCHAR(40),error_code VARCHAR(80) NOT NULL DEFAULT '',
      UNIQUE(id),UNIQUE(id,agent_id),UNIQUE(agent_id,request_key),
      CHECK(sequence>0 AND length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64 AND
        policy='exact-fact-duplicates-v1' AND length(history_epoch)=36 AND length(error_code)<=80),
      CHECK(typeof(budget_micro)='integer' AND budget_micro=0 AND
        typeof(source_count)='integer' AND source_count BETWEEN 2 AND 32 AND
        typeof(candidate_count)='integer' AND candidate_count BETWEEN 0 AND 31 AND candidate_count<source_count AND
        typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807),
      CHECK(state IN ('queued','ready','complete','cancelled','invalidated','failed') AND
        (state NOT IN ('ready','complete') OR prepared_at IS NOT NULL)))""",
    "CREATE INDEX ix_memory_organization_batches_agent_id ON memory_organization_batches(agent_id)",
    "CREATE INDEX ix_memory_organization_batches_agent_sequence ON memory_organization_batches(agent_id,sequence)",
    """CREATE TABLE memory_organization_sources (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,created_at VARCHAR(40) NOT NULL,
      batch_id VARCHAR(36) NOT NULL,memory_id VARCHAR(36) NOT NULL,memory_revision_id VARCHAR(36) NOT NULL,
      revision INTEGER NOT NULL,position INTEGER NOT NULL,raw_sha256 VARCHAR(64) NOT NULL,evidence_sha256 VARCHAR(64) NOT NULL,
      person_id VARCHAR(36),session_id VARCHAR(36),
      UNIQUE(id,agent_id),UNIQUE(batch_id,memory_id),UNIQUE(batch_id,position),UNIQUE(id,batch_id,agent_id),
      FOREIGN KEY(batch_id,agent_id) REFERENCES memory_organization_batches(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(memory_id,agent_id) REFERENCES memory_entries(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(memory_revision_id,memory_id,agent_id) REFERENCES memory_revisions(id,memory_id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(person_id,agent_id) REFERENCES people(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(session_id,agent_id) REFERENCES sessions(id,agent_id) ON DELETE RESTRICT,
      CHECK(typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND
        typeof(position)='integer' AND position BETWEEN 0 AND 31 AND length(raw_sha256)=64 AND length(evidence_sha256)=64))""",
    "CREATE INDEX ix_memory_organization_sources_agent_id ON memory_organization_sources(agent_id)",
    "CREATE INDEX ix_memory_organization_sources_memory ON memory_organization_sources(agent_id,memory_id)",
    """CREATE TABLE memory_organization_candidates (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,created_at VARCHAR(40) NOT NULL,
      batch_id VARCHAR(36) NOT NULL,keep_source_id VARCHAR(36) NOT NULL,duplicate_source_id VARCHAR(36) NOT NULL,
      state VARCHAR(20) NOT NULL,revision INTEGER NOT NULL,fingerprint VARCHAR(64) NOT NULL,
      updated_at VARCHAR(40) NOT NULL,reviewed_at VARCHAR(40),review_audit_id VARCHAR(36),error_code VARCHAR(80) NOT NULL DEFAULT '',
      UNIQUE(id,agent_id),UNIQUE(batch_id,duplicate_source_id),
      FOREIGN KEY(batch_id,agent_id) REFERENCES memory_organization_batches(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(keep_source_id,batch_id,agent_id) REFERENCES memory_organization_sources(id,batch_id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(duplicate_source_id,batch_id,agent_id) REFERENCES memory_organization_sources(id,batch_id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(review_audit_id,agent_id) REFERENCES audit(id,agent_id) ON DELETE RESTRICT,
      CHECK(keep_source_id!=duplicate_source_id AND typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807
        AND length(fingerprint)=64 AND length(error_code)<=80),
      CHECK((state='pending' AND reviewed_at IS NULL AND review_audit_id IS NULL) OR
        (state IN ('applied','rejected','revoked') AND reviewed_at IS NOT NULL AND review_audit_id IS NOT NULL) OR
        (state IN ('invalidated','cancelled') AND ((reviewed_at IS NULL AND review_audit_id IS NULL) OR
        (reviewed_at IS NOT NULL AND review_audit_id IS NOT NULL)))))""",
    "CREATE INDEX ix_memory_organization_candidates_agent_id ON memory_organization_candidates(agent_id)",
    "CREATE INDEX ix_memory_organization_applied_keep ON memory_organization_candidates(agent_id,keep_source_id) WHERE state='applied'",
    "CREATE INDEX ix_memory_organization_applied_duplicate ON memory_organization_candidates(agent_id,duplicate_source_id) WHERE state='applied'",
    """CREATE TRIGGER memory_organization_batch_identity BEFORE UPDATE ON memory_organization_batches WHEN
      new.sequence IS NOT old.sequence OR new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR
      new.created_at IS NOT old.created_at OR new.request_key IS NOT old.request_key OR
      new.request_sha256 IS NOT old.request_sha256 OR new.policy IS NOT old.policy OR
      new.history_epoch IS NOT old.history_epoch OR new.budget_micro IS NOT old.budget_micro OR
      new.source_count IS NOT old.source_count OR new.revision!=old.revision+1 OR
      (old.state!='queued' AND (new.candidate_count IS NOT old.candidate_count OR new.prepared_at IS NOT old.prepared_at)) OR
      (old.state='queued' AND new.state NOT IN ('queued','ready','complete','cancelled','invalidated','failed')) OR
      (old.state='ready' AND new.state NOT IN ('ready','complete','cancelled','invalidated')) OR
      (old.state='complete' AND new.state NOT IN ('complete','invalidated')) OR
      (old.state='cancelled' AND new.state NOT IN ('cancelled','invalidated')) OR old.state IN ('invalidated','failed')
      BEGIN SELECT RAISE(ABORT,'memory organization batch identity and progress are immutable'); END""",
    """CREATE TRIGGER memory_organization_source_identity BEFORE UPDATE ON memory_organization_sources
      BEGIN SELECT RAISE(ABORT,'memory organization source identity is immutable'); END""",
    """CREATE TRIGGER memory_organization_candidate_identity BEFORE UPDATE ON memory_organization_candidates WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
      new.batch_id IS NOT old.batch_id OR new.keep_source_id IS NOT old.keep_source_id OR
      new.duplicate_source_id IS NOT old.duplicate_source_id OR new.fingerprint IS NOT old.fingerprint OR
      new.revision!=old.revision+1 OR
      (old.state='pending' AND new.state NOT IN ('applied','rejected','cancelled','invalidated')) OR
      (old.state='applied' AND new.state NOT IN ('revoked','invalidated')) OR
      old.state IN ('rejected','revoked','invalidated','cancelled')
      BEGIN SELECT RAISE(ABORT,'memory organization candidate identity and decisions are immutable'); END""",
    """CREATE TRIGGER memory_organization_batch_keep BEFORE DELETE ON memory_organization_batches
      BEGIN SELECT RAISE(ABORT,'memory organization evidence cannot be deleted'); END""",
    """CREATE TRIGGER memory_organization_source_keep BEFORE DELETE ON memory_organization_sources
      BEGIN SELECT RAISE(ABORT,'memory organization evidence cannot be deleted'); END""",
    """CREATE TRIGGER memory_organization_candidate_keep BEFORE DELETE ON memory_organization_candidates
      BEGIN SELECT RAISE(ABORT,'memory organization evidence cannot be deleted'); END""",
    """CREATE TRIGGER memory_organization_memory_changed AFTER UPDATE OF revision,content,deleted_at,person_id,session_id,kind ON memory_entries
      WHEN old.revision IS NOT new.revision OR old.content IS NOT new.content OR old.deleted_at IS NOT new.deleted_at OR
        old.person_id IS NOT new.person_id OR old.session_id IS NOT new.session_id OR old.kind IS NOT new.kind
      BEGIN"""
    + _invalidate(
        "(s.agent_id=old.agent_id AND s.memory_id=old.id) OR (s.agent_id=new.agent_id AND s.memory_id=new.id)"
    )
    + "END",
    """CREATE TRIGGER memory_organization_revision_changed AFTER UPDATE OF content ON memory_revisions
      WHEN old.content IS NOT new.content BEGIN"""
    + _invalidate(
        "(s.agent_id=old.agent_id AND s.memory_revision_id=old.id) OR (s.agent_id=new.agent_id AND s.memory_revision_id=new.id)"
    )
    + "END",
    """CREATE TRIGGER memory_organization_anchor_inserted AFTER INSERT ON source_anchors BEGIN"""
    + _invalidate("s.agent_id=new.agent_id AND s.memory_id=new.memory_id")
    + "END",
    """CREATE TRIGGER memory_organization_anchor_updated AFTER UPDATE ON source_anchors BEGIN"""
    + _invalidate(
        "(s.agent_id=old.agent_id AND s.memory_id=old.memory_id) OR (s.agent_id=new.agent_id AND s.memory_id=new.memory_id)"
    )
    + "END",
    """CREATE TRIGGER memory_organization_anchor_deleted AFTER DELETE ON source_anchors BEGIN"""
    + _invalidate("s.agent_id=old.agent_id AND s.memory_id=old.memory_id")
    + "END",
    """CREATE TRIGGER memory_organization_person_deleted AFTER UPDATE OF deleted_at ON people
      WHEN old.deleted_at IS NOT new.deleted_at BEGIN"""
    + _invalidate(
        "(s.agent_id=old.agent_id AND s.person_id=old.id) OR (s.agent_id=new.agent_id AND s.person_id=new.id)"
    )
    + "END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
