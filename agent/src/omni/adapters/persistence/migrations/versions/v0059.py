"""Keep Worker execution attempts and directed peer messages in the shared ledger."""

from alembic import op

revision = "v2_0059"
down_revision = "v2_0058"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_task_attempts (
      id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      created_at VARCHAR(40) NOT NULL, task_id VARCHAR(36) NOT NULL, run_id VARCHAR(36) NOT NULL UNIQUE,
      attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 64),
      goal_version INTEGER NOT NULL CHECK(goal_version>0),
      UNIQUE(id,agent_id), UNIQUE(task_id,attempt_no),
      FOREIGN KEY(task_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_task_attempts_agent_id ON owa_task_attempts(agent_id)",
    """INSERT INTO owa_task_attempts (id,agent_id,created_at,task_id,run_id,attempt_no,goal_version)
       SELECT id,agent_id,created_at,id,run_id,1,1 FROM owa_tasks""",
    """CREATE TRIGGER owa_task_attempt_binding BEFORE INSERT ON owa_task_attempts
    WHEN NOT EXISTS (
      SELECT 1 FROM owa_tasks w JOIN runs anchor ON anchor.id=w.run_id
      JOIN runs current ON current.id=new.run_id AND current.agent_id=w.agent_id
      WHERE w.id=new.task_id AND w.agent_id=new.agent_id AND current.session_id=anchor.session_id
        AND current.parent_id=anchor.parent_id AND current.policy_epoch=anchor.policy_epoch
        AND current.settings_revision_id=anchor.settings_revision_id AND current.model_revision_id=anchor.model_revision_id
        AND current.model_permission_epoch=anchor.model_permission_epoch AND current.execution_kind='model'
        AND (new.attempt_no=1 AND new.goal_version=1 AND current.id=anchor.id OR new.attempt_no>1 AND current.state='queued'
          AND EXISTS (SELECT 1 FROM owa_task_attempts a JOIN runs r ON r.id=a.run_id
                      WHERE a.task_id=w.id AND a.attempt_no=new.attempt_no-1 AND a.goal_version=new.goal_version AND r.state='succeeded')
          AND NOT EXISTS (SELECT 1 FROM owa_task_attempts a JOIN runs r ON r.id=a.run_id
                          WHERE a.task_id=w.id AND r.state IN ('queued','running','waiting_approval','cancelling')))
    ) BEGIN SELECT RAISE(ABORT,'invalid OWA Worker attempt'); END""",
    """CREATE TRIGGER owa_task_first_attempt AFTER INSERT ON owa_tasks
       BEGIN INSERT INTO owa_task_attempts(id,agent_id,created_at,task_id,run_id,attempt_no,goal_version)
       VALUES(new.id,new.agent_id,new.created_at,new.id,new.run_id,1,1); END""",
    """CREATE TRIGGER owa_task_attempt_immutable BEFORE UPDATE ON owa_task_attempts
       BEGIN SELECT RAISE(ABORT,'OWA attempt identity is immutable'); END""",
    """CREATE TABLE owa_peer_links (
      id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      created_at VARCHAR(40) NOT NULL, sender_id VARCHAR(36) NOT NULL, recipient_id VARCHAR(36) NOT NULL,
      UNIQUE(id,agent_id), UNIQUE(sender_id,recipient_id), CHECK(sender_id!=recipient_id),
      FOREIGN KEY(sender_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(recipient_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_peer_links_agent_id ON owa_peer_links(agent_id)",
    """CREATE TRIGGER owa_peer_link_binding BEFORE INSERT ON owa_peer_links
    WHEN NOT EXISTS (SELECT 1 FROM owa_tasks a JOIN owa_tasks b ON b.ticket_id=a.ticket_id
      WHERE a.id=new.sender_id AND b.id=new.recipient_id AND a.agent_id=new.agent_id AND b.agent_id=new.agent_id)
    BEGIN SELECT RAISE(ABORT,'invalid OWA peer scope'); END""",
    """CREATE TABLE owa_peer_requests (
      id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      created_at VARCHAR(40) NOT NULL, link_id VARCHAR(36) NOT NULL, sender_id VARCHAR(36) NOT NULL,
      sender_run_id VARCHAR(36) NOT NULL, sender_version INTEGER NOT NULL CHECK(sender_version>0),
      recipient_version INTEGER NOT NULL CHECK(recipient_version>0), request_key VARCHAR(80) NOT NULL,
      payload_sha256 VARCHAR(64) NOT NULL, question TEXT NOT NULL CHECK(length(question) BETWEEN 1 AND 4000),
      blocking BOOLEAN NOT NULL CHECK(blocking IN (0,1)), expires_at VARCHAR(40) NOT NULL CHECK(expires_at>created_at),
      cause_id VARCHAR(36), depth INTEGER NOT NULL CHECK(depth BETWEEN 1 AND 4),
      disposition VARCHAR(12) CHECK(disposition IN ('answered','declined','timed_out')),
      answer TEXT CHECK(answer IS NULL OR length(answer)<=4000), reply_run_id VARCHAR(36),
      request_ack_run_id VARCHAR(36), reply_ack_run_id VARCHAR(36),
      UNIQUE(id,agent_id), UNIQUE(sender_id,sender_version,request_key),
      FOREIGN KEY(link_id,agent_id) REFERENCES owa_peer_links(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(sender_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(cause_id,agent_id) REFERENCES owa_peer_requests(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(sender_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(reply_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(request_ack_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(reply_ack_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_peer_requests_agent_id ON owa_peer_requests(agent_id)",
    "CREATE INDEX ix_owa_peer_requests_deadline ON owa_peer_requests(expires_at) WHERE disposition IS NULL",
    """CREATE TRIGGER owa_peer_request_binding BEFORE INSERT ON owa_peer_requests
    WHEN NOT EXISTS (SELECT 1 FROM owa_peer_links l JOIN owa_task_attempts a ON a.task_id=l.sender_id
      WHERE l.id=new.link_id AND l.agent_id=new.agent_id AND l.sender_id=new.sender_id AND a.run_id=new.sender_run_id)
    BEGIN SELECT RAISE(ABORT,'invalid OWA request sender'); END""",
    """CREATE TRIGGER owa_peer_request_immutable BEFORE UPDATE OF agent_id,link_id,sender_id,sender_run_id,
       sender_version,recipient_version,request_key,payload_sha256,question,blocking,expires_at,cause_id,depth
       ON owa_peer_requests BEGIN SELECT RAISE(ABORT,'OWA peer request is immutable'); END""",
    """CREATE TRIGGER owa_peer_receipt_binding BEFORE UPDATE ON owa_peer_requests
    WHEN (old.request_ack_run_id IS NOT NULL AND old.request_ack_run_id IS NOT new.request_ack_run_id)
      OR (old.reply_ack_run_id IS NOT NULL AND old.reply_ack_run_id IS NOT new.reply_ack_run_id)
      OR (old.disposition IS NOT NULL AND (old.disposition IS NOT new.disposition
          OR old.answer IS NOT new.answer OR old.reply_run_id IS NOT new.reply_run_id))
      OR (new.request_ack_run_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM owa_peer_links l JOIN owa_task_attempts a ON a.task_id=l.recipient_id
            WHERE l.id=new.link_id AND a.run_id=new.request_ack_run_id AND a.agent_id=new.agent_id))
      OR (new.reply_ack_run_id IS NOT NULL AND (new.disposition IS NULL OR NOT EXISTS (
          SELECT 1 FROM owa_task_attempts a WHERE a.task_id=new.sender_id AND a.run_id=new.reply_ack_run_id
            AND a.agent_id=new.agent_id)))
      OR (new.disposition IN ('answered','declined') AND (new.answer IS NULL OR new.request_ack_run_id IS NULL
          OR NOT EXISTS (SELECT 1 FROM owa_peer_links l JOIN owa_task_attempts a ON a.task_id=l.recipient_id
                        WHERE l.id=new.link_id AND a.run_id=new.reply_run_id AND a.agent_id=new.agent_id)))
    BEGIN SELECT RAISE(ABORT,'invalid OWA peer receipt'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
