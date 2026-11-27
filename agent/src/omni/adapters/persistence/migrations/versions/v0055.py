"""Add durable execution authorization and dependency edges for OmniWeave Tickets."""

from alembic import op

revision = "v2_0055"
down_revision = "v2_0054"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_executions (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_owa_executions_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    ticket_id VARCHAR(36) NOT NULL,
    plan_sha256 VARCHAR(64),
    cancelled_at VARCHAR(40),
    CONSTRAINT pk_owa_executions PRIMARY KEY (id),
    CONSTRAINT fk_owa_executions_ticket_id_owa_tickets FOREIGN KEY(ticket_id, agent_id) REFERENCES owa_tickets (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_owa_executions_ticket_id UNIQUE (ticket_id),
    CONSTRAINT ck_owa_executions_fingerprint CHECK (plan_sha256 IS NULL OR (length(plan_sha256)=64 AND plan_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT uq_owa_executions_id UNIQUE (id, agent_id),
    CONSTRAINT fk_owa_executions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_owa_executions_agent_id ON owa_executions (agent_id)""",
    """CREATE TABLE owa_tasks (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_owa_tasks_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    ticket_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    task_key VARCHAR(32) NOT NULL,
    CONSTRAINT pk_owa_tasks PRIMARY KEY (id),
    CONSTRAINT fk_owa_tasks_ticket_id_owa_tickets FOREIGN KEY(ticket_id, agent_id) REFERENCES owa_tickets (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_owa_tasks_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_owa_tasks_ticket_id UNIQUE (ticket_id, task_key),
    CONSTRAINT uq_owa_tasks_run_id UNIQUE (run_id),
    CONSTRAINT ck_owa_tasks_task_key CHECK (length(task_key) BETWEEN 1 AND 32 AND task_key GLOB '[a-z]*' AND task_key NOT GLOB '*[^a-z0-9-]*'),
    CONSTRAINT uq_owa_tasks_id UNIQUE (id, agent_id),
    CONSTRAINT fk_owa_tasks_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_owa_tasks_agent_id ON owa_tasks (agent_id)""",
    """CREATE TABLE owa_dependencies (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_owa_dependencies_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    task_id VARCHAR(36) NOT NULL,
    needs_id VARCHAR(36) NOT NULL,
    CONSTRAINT pk_owa_dependencies PRIMARY KEY (id),
    CONSTRAINT fk_owa_dependencies_task_id_owa_tasks FOREIGN KEY(task_id, agent_id) REFERENCES owa_tasks (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_owa_dependencies_needs_id_owa_tasks FOREIGN KEY(needs_id, agent_id) REFERENCES owa_tasks (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_owa_dependencies_task_id UNIQUE (task_id, needs_id),
    CONSTRAINT ck_owa_dependencies_different_tasks CHECK (task_id!=needs_id),
    CONSTRAINT uq_owa_dependencies_id UNIQUE (id, agent_id),
    CONSTRAINT fk_owa_dependencies_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_owa_dependencies_agent_id ON owa_dependencies (agent_id)""",
    """CREATE TRIGGER owa_task_binding BEFORE INSERT ON owa_tasks
WHEN NOT EXISTS (SELECT 1 FROM owa_tickets t JOIN owa_executions e ON e.ticket_id=t.id AND e.agent_id=t.agent_id
  JOIN run_delegations d ON d.parent_run_id=t.run_id AND d.agent_id=t.agent_id
  WHERE t.id=new.ticket_id AND t.agent_id=new.agent_id AND d.child_run_id=new.run_id
  AND e.plan_sha256 IS NOT NULL AND e.cancelled_at IS NULL)
BEGIN SELECT RAISE(ABORT, 'invalid OWA Worker binding'); END""",
    """CREATE TRIGGER owa_dependency_binding BEFORE INSERT ON owa_dependencies
WHEN NOT EXISTS (SELECT 1 FROM owa_tasks t JOIN owa_tasks d ON d.ticket_id=t.ticket_id AND d.agent_id=t.agent_id
  WHERE t.id=new.task_id AND d.id=new.needs_id AND t.agent_id=new.agent_id)
BEGIN SELECT RAISE(ABORT, 'invalid OWA Worker dependency'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
