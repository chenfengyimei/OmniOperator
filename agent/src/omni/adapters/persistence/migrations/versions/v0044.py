"""Structured clarification requests pausing a run for the owner; frozen SQL, no current schema imports."""

from alembic import op

revision = "v2_0044"
down_revision = "v2_0043"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE clarifications (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,created_at VARCHAR(40) NOT NULL,
      run_id VARCHAR(36) NOT NULL,questions JSON NOT NULL,state VARCHAR(20) NOT NULL,answers JSON,
      outcome VARCHAR(20),notice TEXT,revision INTEGER NOT NULL,policy_revision INTEGER NOT NULL,
      expires_at VARCHAR(40) NOT NULL,resolved_at VARCHAR(40),
      UNIQUE(id,agent_id),
      FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      CHECK(state IN ('pending','answered','cancelled','timed_out') AND
        typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND
        typeof(policy_revision)='integer' AND policy_revision>=1),
      CHECK((state='pending' AND answers IS NULL AND outcome IS NULL AND resolved_at IS NULL) OR
        (state<>'pending' AND outcome IS NOT NULL AND resolved_at IS NOT NULL)),
      CHECK(outcome IS NULL OR outcome IN ('submitted','cancelled','timed_out','undelivered')))""",
    "CREATE INDEX ix_clarifications_agent_id ON clarifications(agent_id)",
    "CREATE INDEX ix_clarifications_run ON clarifications(agent_id,run_id)",
    """CREATE TRIGGER clarification_identity BEFORE UPDATE ON clarifications WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
      new.run_id IS NOT old.run_id OR new.questions IS NOT old.questions OR
      new.policy_revision IS NOT old.policy_revision OR new.expires_at IS NOT old.expires_at OR
      new.revision!=old.revision+1 OR
      (old.state='pending' AND new.state NOT IN ('pending','answered','cancelled','timed_out')) OR
      old.state IN ('answered','cancelled','timed_out')
      BEGIN SELECT RAISE(ABORT,'clarification identity and resolution are immutable'); END""",
    "CREATE TRIGGER clarification_keep BEFORE DELETE ON clarifications BEGIN SELECT RAISE(ABORT,'clarification evidence cannot be deleted'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
