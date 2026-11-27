"""Bind owner goal updates to the original Ticket and fence Worker versions."""

from alembic import op

revision = "v2_0061"
down_revision = "v2_0060"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE owa_tickets ADD COLUMN root_ticket_id VARCHAR(36) REFERENCES owa_tickets(id) ON DELETE RESTRICT",
    """ALTER TABLE owa_tickets ADD COLUMN goal_version INTEGER NOT NULL DEFAULT 1
       CHECK ((goal_version=1 AND root_ticket_id IS NULL) OR
              (goal_version BETWEEN 2 AND 64 AND root_ticket_id IS NOT NULL))""",
    "CREATE UNIQUE INDEX uq_owa_tickets_goal_version ON owa_tickets(root_ticket_id,goal_version)",
    """CREATE TRIGGER owa_goal_binding BEFORE INSERT ON owa_tickets
       WHEN new.root_ticket_id IS NOT NULL AND NOT EXISTS (
         SELECT 1 FROM owa_tickets root JOIN owa_tickets prior
            ON coalesce(prior.root_ticket_id,prior.id)=root.id AND prior.goal_version=new.goal_version-1
         JOIN owa_executions e ON e.ticket_id=prior.id
         WHERE root.id=new.root_ticket_id AND root.root_ticket_id IS NULL AND root.agent_id=new.agent_id
           AND prior.agent_id=new.agent_id AND root.thread_id=new.thread_id AND prior.thread_id=new.thread_id
           AND e.agent_id=new.agent_id AND e.cancelled_at IS NULL)
       BEGIN SELECT RAISE(ABORT,'invalid OWA goal lineage'); END""",
    """CREATE TRIGGER owa_goal_supersedes AFTER INSERT ON owa_tickets WHEN new.root_ticket_id IS NOT NULL
       BEGIN UPDATE owa_executions SET cancelled_at=new.created_at WHERE ticket_id=(
         SELECT prior.id FROM owa_tickets prior WHERE coalesce(prior.root_ticket_id,prior.id)=new.root_ticket_id
         AND prior.goal_version=new.goal_version-1); END""",
    """CREATE TRIGGER owa_goal_immutable BEFORE UPDATE OF agent_id,thread_id,run_id,root_ticket_id,goal_version
       ON owa_tickets BEGIN SELECT RAISE(ABORT,'OWA goal identity is immutable'); END""",
    """CREATE TRIGGER owa_goal_cancellation_immutable BEFORE UPDATE OF cancelled_at ON owa_executions
       WHEN old.cancelled_at IS NOT NULL AND old.cancelled_at IS NOT new.cancelled_at
       BEGIN SELECT RAISE(ABORT,'OWA goal cancellation is immutable'); END""",
    """CREATE TRIGGER owa_mandate_goal BEFORE INSERT ON owa_mandates
       WHEN NOT EXISTS (SELECT 1 FROM owa_tickets t WHERE t.id=new.ticket_id AND t.goal_version=new.goal_version)
       BEGIN SELECT RAISE(ABORT,'invalid OWA Mandate goal'); END""",
    "DROP TRIGGER owa_task_first_attempt",
    "DROP TRIGGER owa_task_attempt_binding",
    """CREATE TRIGGER owa_task_attempt_binding BEFORE INSERT ON owa_task_attempts
    WHEN NOT EXISTS (
      SELECT 1 FROM owa_tasks w JOIN owa_tickets t ON t.id=w.ticket_id JOIN runs anchor ON anchor.id=w.run_id
      JOIN runs current ON current.id=new.run_id AND current.agent_id=w.agent_id
      WHERE w.id=new.task_id AND w.agent_id=new.agent_id AND current.session_id=anchor.session_id
        AND current.parent_id=anchor.parent_id AND current.policy_epoch=anchor.policy_epoch
        AND current.settings_revision_id=anchor.settings_revision_id AND current.model_revision_id=anchor.model_revision_id
        AND current.model_permission_epoch=anchor.model_permission_epoch AND current.execution_kind='model'
        AND t.goal_version=new.goal_version
        AND (new.attempt_no=1 AND current.id=anchor.id OR new.attempt_no>1 AND current.state='queued'
          AND EXISTS (SELECT 1 FROM owa_task_attempts a JOIN runs r ON r.id=a.run_id
                      WHERE a.task_id=w.id AND a.attempt_no=new.attempt_no-1 AND a.goal_version=new.goal_version AND r.state='succeeded')
          AND NOT EXISTS (SELECT 1 FROM owa_task_attempts a JOIN runs r ON r.id=a.run_id
                          WHERE a.task_id=w.id AND r.state IN ('queued','running','waiting_approval','cancelling')))
    ) BEGIN SELECT RAISE(ABORT,'invalid OWA Worker attempt'); END""",
    """CREATE TRIGGER owa_task_first_attempt AFTER INSERT ON owa_tasks
       BEGIN INSERT INTO owa_task_attempts(id,agent_id,created_at,task_id,run_id,attempt_no,goal_version)
       SELECT new.id,new.agent_id,new.created_at,new.id,new.run_id,1,t.goal_version
       FROM owa_tickets t WHERE t.id=new.ticket_id; END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
