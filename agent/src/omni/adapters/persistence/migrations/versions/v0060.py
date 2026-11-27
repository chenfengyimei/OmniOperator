"""Bind immutable artifact versions, directed sharing and subscription receipts."""

from alembic import op

revision = "v2_0060"
down_revision = "v2_0059"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_artifact_links (
      id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      created_at VARCHAR(40) NOT NULL, producer_id VARCHAR(36) NOT NULL, reader_id VARCHAR(36) NOT NULL,
      subscription_revision INTEGER NOT NULL DEFAULT 0 CHECK(subscription_revision>=0),
      subscribed BOOLEAN NOT NULL DEFAULT 0 CHECK(subscribed IN (0,1)),
      UNIQUE(id,agent_id), UNIQUE(producer_id,reader_id), CHECK(producer_id!=reader_id),
      FOREIGN KEY(producer_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(reader_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_artifact_links_agent_id ON owa_artifact_links(agent_id)",
    """CREATE TRIGGER owa_artifact_link_binding BEFORE INSERT ON owa_artifact_links
      WHEN NOT EXISTS (SELECT 1 FROM owa_tasks a JOIN owa_tasks b ON b.ticket_id=a.ticket_id
        WHERE a.id=new.producer_id AND b.id=new.reader_id AND a.agent_id=new.agent_id AND b.agent_id=new.agent_id)
      BEGIN SELECT RAISE(ABORT,'invalid OWA artifact link'); END""",
    """CREATE TRIGGER owa_artifact_link_revision BEFORE UPDATE ON owa_artifact_links
      WHEN old.agent_id!=new.agent_id OR old.producer_id!=new.producer_id OR old.reader_id!=new.reader_id
        OR old.subscribed=new.subscribed OR new.subscription_revision!=old.subscription_revision+1
      BEGIN SELECT RAISE(ABORT,'invalid OWA subscription revision'); END""",
    """CREATE TABLE owa_artifacts (
      id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      created_at VARCHAR(40) NOT NULL, ticket_id VARCHAR(36) NOT NULL, producer_id VARCHAR(36) NOT NULL,
      producer_run_id VARCHAR(36) NOT NULL, action_id VARCHAR(36) NOT NULL,
      goal_version INTEGER NOT NULL CHECK(goal_version>0), name VARCHAR(32) NOT NULL,
      version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 32), command_key VARCHAR(32) NOT NULL,
      payload_sha256 VARCHAR(64) NOT NULL CHECK(length(payload_sha256)=64), object_id VARCHAR(36) NOT NULL,
      UNIQUE(id,agent_id), UNIQUE(producer_id,goal_version,command_key), UNIQUE(producer_id,goal_version,name,version),
      FOREIGN KEY(ticket_id,agent_id) REFERENCES owa_tickets(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(producer_id,agent_id) REFERENCES owa_tasks(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(producer_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(action_id,agent_id) REFERENCES actions(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(object_id,agent_id) REFERENCES objects(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_artifacts_agent_id ON owa_artifacts(agent_id)",
    "CREATE INDEX ix_owa_artifacts_ticket ON owa_artifacts(ticket_id,goal_version)",
    """CREATE TRIGGER owa_artifact_binding BEFORE INSERT ON owa_artifacts
      WHEN NOT EXISTS (SELECT 1 FROM owa_tasks t JOIN owa_task_attempts a ON a.task_id=t.id
        JOIN actions x ON x.run_id=a.run_id JOIN objects o ON o.id=new.object_id
        WHERE t.id=new.producer_id AND t.ticket_id=new.ticket_id AND t.agent_id=new.agent_id
          AND a.run_id=new.producer_run_id AND a.goal_version=new.goal_version
          AND x.id=new.action_id AND x.tool='publish_artifact' AND x.state='executing'
          AND o.agent_id=new.agent_id AND o.media_type='application/vnd.omniweave.artifact+json')
      BEGIN SELECT RAISE(ABORT,'invalid OWA artifact source'); END""",
    """CREATE TRIGGER owa_artifact_immutable BEFORE UPDATE ON owa_artifacts
      BEGIN SELECT RAISE(ABORT,'OWA artifact version is immutable'); END""",
    """CREATE TABLE owa_artifact_grants (
      id VARCHAR(36) PRIMARY KEY NOT NULL CHECK(length(id)=36), agent_id VARCHAR(36) NOT NULL REFERENCES agents(id),
      created_at VARCHAR(40) NOT NULL, artifact_id VARCHAR(36) NOT NULL, link_id VARCHAR(36) NOT NULL,
      reader_version INTEGER NOT NULL CHECK(reader_version>0), subscription_revision INTEGER,
      ack_run_id VARCHAR(36), revoked_at VARCHAR(40), UNIQUE(id,agent_id), UNIQUE(artifact_id,link_id),
      FOREIGN KEY(artifact_id,agent_id) REFERENCES owa_artifacts(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(link_id,agent_id) REFERENCES owa_artifact_links(id,agent_id) ON DELETE RESTRICT,
      FOREIGN KEY(ack_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_owa_artifact_grants_agent_id ON owa_artifact_grants(agent_id)",
    """CREATE TRIGGER owa_artifact_grant_binding BEFORE INSERT ON owa_artifact_grants
      WHEN NOT EXISTS (SELECT 1 FROM owa_artifacts a JOIN owa_artifact_links l ON l.producer_id=a.producer_id
        WHERE a.id=new.artifact_id AND l.id=new.link_id AND a.agent_id=new.agent_id AND l.agent_id=new.agent_id
          AND a.goal_version=new.reader_version AND (new.subscription_revision IS NULL OR
              l.subscribed=1 AND new.subscription_revision=l.subscription_revision))
      BEGIN SELECT RAISE(ABORT,'invalid OWA artifact grant'); END""",
    """CREATE TRIGGER owa_artifact_grant_receipt BEFORE UPDATE ON owa_artifact_grants
      WHEN old.artifact_id!=new.artifact_id OR old.link_id!=new.link_id OR old.agent_id!=new.agent_id
        OR old.reader_version!=new.reader_version OR old.subscription_revision IS NOT new.subscription_revision
        OR (old.revoked_at IS NOT NULL AND old.revoked_at IS NOT new.revoked_at)
        OR (old.ack_run_id IS NOT NULL AND old.ack_run_id IS NOT new.ack_run_id)
        OR (new.ack_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM owa_artifact_links l JOIN owa_task_attempts t ON t.task_id=l.reader_id
              WHERE l.id=new.link_id AND t.run_id=new.ack_run_id AND t.goal_version=new.reader_version))
      BEGIN SELECT RAISE(ABORT,'invalid OWA artifact receipt'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
