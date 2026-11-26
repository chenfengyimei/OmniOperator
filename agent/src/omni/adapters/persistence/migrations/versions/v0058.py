"""Capture immutable root and Worker authority in the V2 database."""

from alembic import op

revision = "v2_0058"
down_revision = "v2_0057"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE owa_executions ADD COLUMN mandate_required BOOLEAN NOT NULL DEFAULT 0 CHECK(mandate_required IN (0,1))",
    """CREATE TABLE owa_mandates (
        id VARCHAR(36) NOT NULL PRIMARY KEY CHECK(length(id)=36),
        agent_id VARCHAR(36) NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
        created_at VARCHAR(40) NOT NULL, ticket_id VARCHAR(36) NOT NULL,
        task_id VARCHAR(36), parent_id VARCHAR(36), run_id VARCHAR(36) NOT NULL,
        main_session_id VARCHAR(36) NOT NULL, owner_id VARCHAR(36) NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
        profile VARCHAR(32) NOT NULL, profile_version INTEGER NOT NULL CHECK(profile_version>0),
        goal_version INTEGER NOT NULL CHECK(goal_version>0), goal_sha256 VARCHAR(64) NOT NULL
          CHECK(length(goal_sha256)=64 AND goal_sha256 NOT GLOB '*[^0-9a-f]*'),
        policy_epoch INTEGER NOT NULL CHECK(policy_epoch>0), history_epoch VARCHAR(36) NOT NULL CHECK(length(history_epoch)=36),
        session_epoch INTEGER NOT NULL CHECK(session_epoch>0), expires_at VARCHAR(40) NOT NULL CHECK(expires_at>created_at),
        tools JSON NOT NULL CHECK(json_valid(tools) AND json_type(tools)='object' AND length(tools)<=131072),
        budget_micro INTEGER NOT NULL CHECK(budget_micro BETWEEN 1 AND 1000000),
        max_calls INTEGER NOT NULL CHECK(max_calls BETWEEN 1 AND 64),
        CHECK ((task_id IS NULL AND parent_id IS NULL AND profile='router') OR
          (task_id IS NOT NULL AND parent_id IS NOT NULL AND profile!='router')),
        UNIQUE(id,agent_id), UNIQUE(run_id,goal_version), UNIQUE(task_id,goal_version),
        FOREIGN KEY(ticket_id,agent_id) REFERENCES owa_tickets(id,agent_id) ON DELETE RESTRICT,
        FOREIGN KEY(task_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
        FOREIGN KEY(parent_id,agent_id) REFERENCES owa_mandates(id,agent_id) ON DELETE RESTRICT,
        FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
        FOREIGN KEY(main_session_id,agent_id) REFERENCES sessions(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_mandates_agent_id ON owa_mandates(agent_id)",
    """CREATE TRIGGER owa_execution_mandate_required BEFORE UPDATE OF mandate_required ON owa_executions
    WHEN old.mandate_required!=new.mandate_required
    BEGIN SELECT RAISE(ABORT,'OWA Mandate requirement is immutable'); END""",
    """CREATE TRIGGER owa_mandate_binding BEFORE INSERT ON owa_mandates
    WHEN NOT EXISTS (
        SELECT 1 FROM owa_tickets t JOIN owa_threads h ON h.id=t.thread_id
        JOIN runs r ON r.id=new.run_id AND r.agent_id=new.agent_id
        WHERE t.id=new.ticket_id AND t.agent_id=new.agent_id AND h.owner_id=new.owner_id
          AND h.main_session_id=new.main_session_id AND r.policy_epoch=new.policy_epoch
          AND ((new.task_id IS NULL AND t.run_id=new.run_id) OR EXISTS (
              SELECT 1 FROM owa_tasks w JOIN owa_mandates p ON p.id=new.parent_id
              JOIN run_delegations d ON d.child_run_id=w.run_id AND d.agent_id=w.agent_id
              WHERE w.id=new.task_id AND w.ticket_id=t.id AND w.run_id=new.run_id
                AND p.ticket_id=t.id AND p.run_id=t.run_id AND p.task_id IS NULL
                AND p.goal_version=new.goal_version AND p.policy_epoch=new.policy_epoch
                AND p.history_epoch=new.history_epoch AND p.session_epoch=new.session_epoch
                AND new.expires_at<=p.expires_at AND new.budget_micro<=p.budget_micro
                AND new.max_calls<=p.max_calls AND new.budget_micro=d.budget_micro AND new.max_calls=d.max_calls
                AND NOT EXISTS (SELECT 1 FROM json_each(new.tools) x LEFT JOIN json_each(p.tools) y ON y.key=x.key
                                WHERE y.key IS NULL OR x.value!=y.value)
          ))
    ) BEGIN SELECT RAISE(ABORT,'invalid OWA Mandate binding'); END""",
    """CREATE TRIGGER owa_mandate_immutable BEFORE UPDATE ON owa_mandates
    BEGIN SELECT RAISE(ABORT,'OWA Mandate is immutable'); END""",
    """CREATE TRIGGER owa_mandate_no_delete BEFORE DELETE ON owa_mandates
    BEGIN SELECT RAISE(ABORT,'OWA Mandate is immutable'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
