"""Reference prior accepted Worker evidence and authorized peer citations."""

from alembic import op

revision = "v2_0063"
down_revision = "v2_0062"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_task_reuse (
       id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
       created_at VARCHAR(40) NOT NULL, ticket_id VARCHAR(36) NOT NULL, task_key VARCHAR(32) NOT NULL,
       source_task_id VARCHAR(36) NOT NULL, source_goal_version INTEGER NOT NULL,
       UNIQUE(id,agent_id),
       UNIQUE(ticket_id,task_key),
       CHECK (length(task_key) BETWEEN 1 AND 32 AND task_key GLOB '[a-z]*' AND task_key NOT GLOB '*[^a-z0-9-]*'),
       CHECK (source_goal_version BETWEEN 1 AND 64),
       FOREIGN KEY(ticket_id,agent_id) REFERENCES owa_tickets(id,agent_id) ON DELETE RESTRICT,
       FOREIGN KEY(source_task_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_task_reuse_agent_id ON owa_task_reuse(agent_id)",
    """CREATE TRIGGER owa_task_reuse_source BEFORE INSERT ON owa_task_reuse WHEN
       new.source_goal_version >= (SELECT goal_version FROM owa_tickets WHERE id=new.ticket_id AND agent_id=new.agent_id)
       OR NOT EXISTS (
         SELECT 1 FROM owa_tickets source_ticket JOIN owa_tasks source_task ON source_task.ticket_id=source_ticket.id
         WHERE source_task.id=new.source_task_id AND source_task.agent_id=new.agent_id
           AND source_task.task_key=new.task_key AND source_ticket.goal_version=new.source_goal_version
           AND COALESCE(source_ticket.root_ticket_id,source_ticket.id)=
               COALESCE((SELECT root_ticket_id FROM owa_tickets WHERE id=new.ticket_id AND agent_id=new.agent_id),
                        new.ticket_id))
       BEGIN SELECT RAISE(ABORT,'invalid OWA task reuse'); END""",
    "ALTER TABLE owa_peer_requests ADD COLUMN request_references JSON",
    "ALTER TABLE owa_peer_requests ADD COLUMN reply_references JSON",
    """CREATE TRIGGER owa_peer_references BEFORE UPDATE ON owa_peer_requests WHEN
       (new.request_references IS NOT NULL AND CASE WHEN json_valid(new.request_references)
         THEN json_type(new.request_references) NOT IN ('array','null') ELSE 1 END)
       OR (new.reply_references IS NOT NULL AND CASE WHEN json_valid(new.reply_references)
         THEN json_type(new.reply_references) NOT IN ('array','null') ELSE 1 END)
       BEGIN SELECT RAISE(ABORT,'invalid OWA peer references'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
