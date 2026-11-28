"""Managed long jobs with stable ids, fenced receipts and immutable cancellation."""

from alembic import op

revision = "v2_0064"
down_revision = "v2_0063"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_jobs (
       id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
       created_at VARCHAR(40) NOT NULL, ticket_id VARCHAR(36) NOT NULL, task_id VARCHAR(36) NOT NULL,
       goal_version INTEGER NOT NULL CHECK(goal_version BETWEEN 1 AND 64),
       request_key VARCHAR(80) NOT NULL, request_sha256 CHAR(64) NOT NULL,
       tool VARCHAR(80) NOT NULL, arguments JSON NOT NULL,
       action_id VARCHAR(36), run_id VARCHAR(36),
       state VARCHAR(12) NOT NULL DEFAULT 'queued' CHECK(state IN
         ('queued','running','cancelling','succeeded','failed','cancelled','interrupted')),
       receipt JSON, receipt_sha256 CHAR(64), cancel_requested_at VARCHAR(40), deadline_at VARCHAR(40),
       resumes INTEGER NOT NULL DEFAULT 0, followup_run_id VARCHAR(36),
       updated_at VARCHAR(40) NOT NULL, ended_at VARCHAR(40), error_code VARCHAR(80) NOT NULL DEFAULT '',
       UNIQUE(id,agent_id),
       UNIQUE(task_id,request_key),
       UNIQUE(run_id),
       FOREIGN KEY(ticket_id,agent_id) REFERENCES owa_tickets(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(task_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(action_id,agent_id) REFERENCES actions(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(followup_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
       CHECK ((receipt IS NULL AND receipt_sha256 IS NULL AND ended_at IS NULL AND error_code='') OR
              (receipt IS NOT NULL AND receipt_sha256 IS NOT NULL AND length(receipt_sha256)=64
               AND receipt_sha256 NOT GLOB '*[^0-9a-f]*' AND ended_at IS NOT NULL
               AND json_valid(receipt) AND json_type(receipt)='object'))
    )""",
    "CREATE INDEX ix_owa_jobs_agent_id ON owa_jobs(agent_id)",
    """CREATE UNIQUE INDEX uq_owa_jobs_live ON owa_jobs(task_id)
       WHERE state IN ('queued','running','cancelling')""",
    """CREATE TRIGGER owa_job_identity BEFORE UPDATE OF ticket_id,task_id,goal_version,request_key,
       request_sha256,tool,arguments ON owa_jobs
       BEGIN SELECT RAISE(ABORT,'OWA job identity is immutable'); END""",
    """CREATE TRIGGER owa_job_terminal BEFORE UPDATE ON owa_jobs WHEN
       (old.state IN ('succeeded','failed','cancelled','interrupted') AND new.state!=old.state)
       OR (old.receipt_sha256 IS NOT NULL AND new.receipt_sha256 IS NOT old.receipt_sha256)
       OR (old.ended_at IS NOT NULL AND new.ended_at IS NOT old.ended_at)
       BEGIN SELECT RAISE(ABORT,'OWA job receipt is immutable'); END""",
    """CREATE TRIGGER owa_job_cancel_immutable BEFORE UPDATE OF cancel_requested_at ON owa_jobs
       WHEN old.cancel_requested_at IS NOT NULL AND old.cancel_requested_at IS NOT new.cancel_requested_at
       BEGIN SELECT RAISE(ABORT,'OWA job cancellation is immutable'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
