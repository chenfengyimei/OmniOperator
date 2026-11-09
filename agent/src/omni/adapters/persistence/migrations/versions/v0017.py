"""Durable local document maintenance with owned, fenced attempts and retained evidence."""

from alembic import op

revision = "v2_0017"
down_revision = "v2_0016"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE maintenance_jobs (
      sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      id VARCHAR(36) NOT NULL CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT, created_at VARCHAR(40) NOT NULL,
      kind VARCHAR(40) NOT NULL, document_id VARCHAR(36) NOT NULL, revision_id VARCHAR(36) NOT NULL,
      source_revision INTEGER NOT NULL, erasure_job_id VARCHAR(36),
      request_key VARCHAR(200) NOT NULL, request_sha256 VARCHAR(64) NOT NULL, index_version VARCHAR(80) NOT NULL,
      state VARCHAR(20) NOT NULL, revision INTEGER NOT NULL, attempt_count INTEGER NOT NULL,
      cancel_requested_at VARCHAR(40), updated_at VARCHAR(40) NOT NULL, finished_at VARCHAR(40),
      error_code VARCHAR(80) NOT NULL DEFAULT '', result_build_id VARCHAR(36),
      UNIQUE(id), UNIQUE(id,agent_id), UNIQUE(agent_id,request_key),
      FOREIGN KEY(document_id,agent_id) REFERENCES source_documents(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(revision_id,agent_id) REFERENCES document_revisions(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(erasure_job_id,agent_id) REFERENCES document_erasure_jobs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(result_build_id,agent_id) REFERENCES document_index_builds(id,agent_id) ON DELETE RESTRICT,
      CHECK(sequence>0 AND length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64),
      CHECK(typeof(source_revision)='integer' AND source_revision BETWEEN 1 AND 9223372036854775807 AND
        typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND
        typeof(attempt_count)='integer' AND attempt_count BETWEEN 0 AND 9223372036854775807),
      CHECK((kind='document_index_rebuild' AND erasure_job_id IS NULL AND length(index_version) BETWEEN 1 AND 80) OR
        (kind='document_erasure_resume' AND erasure_job_id IS NOT NULL AND index_version='')),
      CHECK((state IN ('queued','claimed','running','cancelling') AND finished_at IS NULL) OR
        (state IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)))""",
    "CREATE INDEX ix_maintenance_jobs_agent_id ON maintenance_jobs(agent_id)",
    "CREATE INDEX ix_maintenance_jobs_agent_sequence ON maintenance_jobs(agent_id,sequence)",
    """CREATE UNIQUE INDEX ix_maintenance_jobs_active_document ON maintenance_jobs(agent_id,document_id)
      WHERE state IN ('claimed','running','cancelling')""",
    """CREATE TABLE maintenance_attempts (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT, created_at VARCHAR(40) NOT NULL,
      job_id VARCHAR(36) NOT NULL, attempt_no INTEGER NOT NULL, boot_id VARCHAR(36) NOT NULL,
      token VARCHAR(36) NOT NULL, fence INTEGER NOT NULL, lease_until VARCHAR(40) NOT NULL,
      state VARCHAR(20) NOT NULL, started_at VARCHAR(40), ended_at VARCHAR(40),
      error_code VARCHAR(80) NOT NULL DEFAULT '', build_id VARCHAR(36),
      UNIQUE(id,agent_id), UNIQUE(job_id,attempt_no), UNIQUE(token),
      FOREIGN KEY(job_id,agent_id) REFERENCES maintenance_jobs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(build_id,agent_id) REFERENCES document_index_builds(id,agent_id) ON DELETE RESTRICT,
      CHECK(length(boot_id)=36 AND length(token)=36),
      CHECK(typeof(attempt_no)='integer' AND attempt_no BETWEEN 1 AND 9223372036854775807 AND
        typeof(fence)='integer' AND fence BETWEEN 1 AND 9223372036854775807),
      CHECK((state='claimed' AND started_at IS NULL AND ended_at IS NULL) OR
        (state='running' AND started_at IS NOT NULL AND ended_at IS NULL) OR
        (state='succeeded' AND started_at IS NOT NULL AND ended_at IS NOT NULL) OR
        (state IN ('failed','cancelled','interrupted') AND ended_at IS NOT NULL)))""",
    "CREATE INDEX ix_maintenance_attempts_agent_id ON maintenance_attempts(agent_id)",
    """CREATE UNIQUE INDEX ix_maintenance_attempts_active_job ON maintenance_attempts(job_id)
      WHERE state IN ('claimed','running')""",
    """CREATE TRIGGER maintenance_job_identity BEFORE UPDATE ON maintenance_jobs WHEN
      new.sequence IS NOT old.sequence OR new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR
      new.created_at IS NOT old.created_at OR new.kind IS NOT old.kind OR new.document_id IS NOT old.document_id OR
      new.revision_id IS NOT old.revision_id OR new.source_revision IS NOT old.source_revision OR
      new.erasure_job_id IS NOT old.erasure_job_id OR new.request_key IS NOT old.request_key OR
      new.request_sha256 IS NOT old.request_sha256 OR new.index_version IS NOT old.index_version OR
      new.revision<old.revision OR new.attempt_count<old.attempt_count OR
      (old.state IN ('succeeded','failed','cancelled') AND
        (new.state IS NOT old.state OR new.revision IS NOT old.revision OR
         new.attempt_count IS NOT old.attempt_count OR new.updated_at IS NOT old.updated_at OR
         new.finished_at IS NOT old.finished_at OR new.error_code IS NOT old.error_code OR
         new.cancel_requested_at IS NOT old.cancel_requested_at OR new.result_build_id IS NOT old.result_build_id))
      BEGIN SELECT RAISE(ABORT,'maintenance job identity and terminal outcome are immutable'); END""",
    """CREATE TRIGGER maintenance_attempt_identity BEFORE UPDATE ON maintenance_attempts WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
      new.job_id IS NOT old.job_id OR new.attempt_no IS NOT old.attempt_no OR new.boot_id IS NOT old.boot_id OR
      new.token IS NOT old.token OR new.fence IS NOT old.fence OR
      (new.build_id IS NOT old.build_id AND NOT(old.build_id IS NULL AND new.build_id IS NOT NULL AND
        old.state IN ('claimed','running'))) OR
      (new.started_at IS NOT old.started_at AND NOT(old.state='claimed' AND new.state='running' AND
        old.started_at IS NULL AND new.started_at IS NOT NULL)) OR
      (old.state='claimed' AND new.state NOT IN ('claimed','running','failed','cancelled','interrupted')) OR
      (old.state='running' AND new.state NOT IN ('running','succeeded','failed','cancelled','interrupted')) OR
      (old.state IN ('succeeded','failed','cancelled','interrupted') AND
        (new.state IS NOT old.state OR new.lease_until IS NOT old.lease_until OR
         new.ended_at IS NOT old.ended_at OR new.error_code IS NOT old.error_code))
      BEGIN SELECT RAISE(ABORT,'maintenance attempt identity and terminal outcome are immutable'); END""",
    """CREATE TRIGGER maintenance_job_keep BEFORE DELETE ON maintenance_jobs
      BEGIN SELECT RAISE(ABORT,'maintenance evidence cannot be deleted'); END""",
    """CREATE TRIGGER maintenance_attempt_keep BEFORE DELETE ON maintenance_attempts
      BEGIN SELECT RAISE(ABORT,'maintenance evidence cannot be deleted'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
