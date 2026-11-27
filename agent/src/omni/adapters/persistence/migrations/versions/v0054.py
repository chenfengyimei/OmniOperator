"""Add scoped OmniWeave observation records to the existing V2 database."""

from alembic import op

revision = "v2_0054"
down_revision = "v2_0053"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE owa_threads (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_owa_threads_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    main_session_id VARCHAR(36) NOT NULL,
    owner_id VARCHAR(36) NOT NULL,
    CONSTRAINT pk_owa_threads PRIMARY KEY (id),
    CONSTRAINT fk_owa_threads_main_session_id_sessions FOREIGN KEY(main_session_id, agent_id) REFERENCES sessions (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_owa_threads_main_session_id UNIQUE (main_session_id),
    CONSTRAINT uq_owa_threads_id UNIQUE (id, agent_id),
    CONSTRAINT fk_owa_threads_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT,
    CONSTRAINT fk_owa_threads_owner_id_principals FOREIGN KEY(owner_id) REFERENCES principals (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE owa_tickets (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_owa_tickets_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    thread_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    CONSTRAINT pk_owa_tickets PRIMARY KEY (id),
    CONSTRAINT fk_owa_tickets_thread_id_owa_threads FOREIGN KEY(thread_id, agent_id) REFERENCES owa_threads (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_owa_tickets_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_owa_tickets_run_id UNIQUE (run_id),
    CONSTRAINT uq_owa_tickets_id UNIQUE (id, agent_id),
    CONSTRAINT fk_owa_tickets_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE owa_triggers (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_owa_triggers_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    ticket_id VARCHAR(36) NOT NULL,
    inbox_id VARCHAR(36) NOT NULL,
    source VARCHAR(20) NOT NULL,
    version INTEGER NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    decision VARCHAR(20) NOT NULL,
    CONSTRAINT pk_owa_triggers PRIMARY KEY (id),
    CONSTRAINT fk_owa_triggers_ticket_id_owa_tickets FOREIGN KEY(ticket_id, agent_id) REFERENCES owa_tickets (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_owa_triggers_inbox_id_inbox FOREIGN KEY(inbox_id, agent_id) REFERENCES inbox (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_owa_triggers_inbox_id UNIQUE (inbox_id),
    CONSTRAINT ck_owa_triggers_contract CHECK (source='owner_web' AND version=1 AND decision='observe'),
    CONSTRAINT ck_owa_triggers_fingerprint CHECK (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    CONSTRAINT uq_owa_triggers_id UNIQUE (id, agent_id),
    CONSTRAINT fk_owa_triggers_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    "CREATE INDEX ix_owa_threads_agent_id ON owa_threads (agent_id)",
    "CREATE INDEX ix_owa_tickets_agent_id ON owa_tickets (agent_id)",
    "CREATE INDEX ix_owa_triggers_agent_id ON owa_triggers (agent_id)",
    """CREATE TRIGGER owa_thread_binding BEFORE INSERT ON owa_threads
WHEN NOT EXISTS (SELECT 1 FROM sessions s JOIN instances i ON i.slot=1
  WHERE s.id=new.main_session_id AND s.agent_id=new.agent_id AND s.deleted_at IS NULL AND i.owner_id=new.owner_id)
  OR EXISTS (SELECT 1 FROM run_delegations d WHERE d.child_session_id=new.main_session_id)
  OR EXISTS (SELECT 1 FROM qq_bindings q WHERE q.session_id=new.main_session_id)
BEGIN SELECT RAISE(ABORT, 'invalid OWA main session binding'); END""",
    """CREATE TRIGGER owa_ticket_binding BEFORE INSERT ON owa_tickets
WHEN NOT EXISTS (SELECT 1 FROM owa_threads t JOIN runs r ON r.session_id=t.main_session_id AND r.agent_id=t.agent_id
  WHERE t.id=new.thread_id AND r.id=new.run_id AND t.agent_id=new.agent_id AND r.parent_id IS NULL AND r.execution_kind='model')
BEGIN SELECT RAISE(ABORT, 'invalid OWA Ticket binding'); END""",
    """CREATE TRIGGER owa_trigger_binding BEFORE INSERT ON owa_triggers
WHEN NOT EXISTS (SELECT 1 FROM owa_tickets t JOIN inbox i ON i.run_id=t.run_id AND i.agent_id=t.agent_id
  WHERE t.id=new.ticket_id AND i.id=new.inbox_id AND t.agent_id=new.agent_id AND i.channel='chat'
  AND i.request_fingerprint=new.payload_sha256)
BEGIN SELECT RAISE(ABORT, 'invalid OWA Trigger binding'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
