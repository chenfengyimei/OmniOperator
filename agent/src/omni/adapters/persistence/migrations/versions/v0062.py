"""Record one Router delivery and its message per admitted goal version."""

from alembic import op

revision = "v2_0062"
down_revision = "v2_0061"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_deliveries (
       id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
       created_at VARCHAR(40) NOT NULL, ticket_id VARCHAR(36) NOT NULL UNIQUE, run_id VARCHAR(36) NOT NULL UNIQUE,
       message_id VARCHAR(36) UNIQUE, outcome VARCHAR(12), evidence JSON,
       UNIQUE(id,agent_id),
       CHECK ((message_id IS NULL AND outcome IS NULL AND evidence IS NULL) OR
              (message_id IS NOT NULL AND outcome IS NOT NULL AND outcome IN ('completed','partial','failed')
               AND evidence IS NOT NULL AND json_valid(evidence) AND json_type(evidence)='object')),
       FOREIGN KEY(ticket_id,agent_id) REFERENCES owa_tickets(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(message_id,agent_id) REFERENCES messages(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_deliveries_agent_id ON owa_deliveries(agent_id)",
    """CREATE TRIGGER owa_delivery_binding BEFORE INSERT ON owa_deliveries WHEN
       new.message_id IS NOT NULL OR NOT EXISTS (
         SELECT 1 FROM owa_tickets t JOIN owa_executions e ON e.ticket_id=t.id
         JOIN runs source ON source.id=t.run_id JOIN runs r ON r.id=new.run_id
         WHERE t.id=new.ticket_id AND t.agent_id=new.agent_id AND r.agent_id=t.agent_id
           AND e.plan_sha256 IS NOT NULL AND e.cancelled_at IS NULL AND source.state='succeeded'
           AND r.id!=source.id AND r.session_id=source.session_id AND r.input_message_id=source.input_message_id
           AND r.policy_epoch=source.policy_epoch AND r.settings_revision_id=source.settings_revision_id
           AND r.model_revision_id=source.model_revision_id AND r.model_permission_epoch=source.model_permission_epoch
           AND r.execution_kind='model' AND r.parent_id IS NULL AND r.state='queued')
       BEGIN SELECT RAISE(ABORT,'invalid OWA Router delivery'); END""",
    """CREATE TRIGGER owa_delivery_receipt BEFORE UPDATE ON owa_deliveries WHEN
       old.message_id IS NOT NULL OR new.agent_id!=old.agent_id OR new.ticket_id!=old.ticket_id
       OR new.id!=old.id OR new.run_id!=old.run_id OR new.created_at!=old.created_at
       OR NOT EXISTS (SELECT 1 FROM messages m JOIN runs r ON r.id=m.run_id
         JOIN owa_executions e ON e.ticket_id=new.ticket_id
         WHERE m.id=new.message_id AND r.id=new.run_id AND m.agent_id=new.agent_id
         AND m.session_id=r.session_id AND m.role='assistant' AND m.deleted_at IS NULL
         AND e.cancelled_at IS NULL AND r.state='succeeded')
       BEGIN SELECT RAISE(ABORT,'invalid OWA delivery receipt'); END""",
    """CREATE TRIGGER owa_delivery_receipt_retained BEFORE DELETE ON owa_deliveries
       WHEN old.message_id IS NOT NULL
       BEGIN SELECT RAISE(ABORT,'OWA delivery receipt is immutable'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
