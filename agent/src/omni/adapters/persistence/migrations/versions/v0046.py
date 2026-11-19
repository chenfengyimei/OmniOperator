"""Per-account Bilibili risk-control pauses; frozen SQL, no current schema imports."""

from alembic import op

revision = "v2_0046"
down_revision = "v2_0045"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE bilibili_risk_pauses (
      id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
      agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
      created_at VARCHAR(40) NOT NULL,
      config_id VARCHAR(36) NOT NULL,
      code INTEGER NOT NULL,
      paused_at VARCHAR(40) NOT NULL,
      resumed_at VARCHAR(40),
      UNIQUE(id,agent_id),
      UNIQUE(agent_id,config_id),
      FOREIGN KEY(config_id,agent_id) REFERENCES configurations(id,agent_id) ON DELETE RESTRICT,
      CHECK(code IN (12015,-105,-101,401)),
      CHECK(typeof(code)='integer' AND length(paused_at) BETWEEN 1 AND 40 AND
        (resumed_at IS NULL OR length(resumed_at) BETWEEN 1 AND 40)))""",
    "CREATE INDEX ix_bilibili_risk_pauses_agent_id ON bilibili_risk_pauses(agent_id)",
    """CREATE TRIGGER bilibili_risk_identity BEFORE UPDATE ON bilibili_risk_pauses WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
      new.config_id IS NOT old.config_id
      BEGIN SELECT RAISE(ABORT,'risk pause identity is immutable'); END""",
    "CREATE TRIGGER bilibili_risk_keep BEFORE DELETE ON bilibili_risk_pauses BEGIN SELECT RAISE(ABORT,'risk pause history cannot be deleted'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
