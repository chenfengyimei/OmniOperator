"""Document-backed reflection drafts, daily quota slots and erasure propagation."""

from alembic import op

revision = "v2_0051"
down_revision = "v2_0050"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE reflection_journals (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_reflection_journals_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    source_day VARCHAR(10) NOT NULL,
    slot INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    state VARCHAR(16) NOT NULL,
    body JSON,
    fingerprint VARCHAR(64),
    CONSTRAINT pk_reflection_journals PRIMARY KEY (id),
    CONSTRAINT uq_reflection_daily_slot UNIQUE (agent_id, source_day, slot),
    CONSTRAINT uq_reflection_fingerprint UNIQUE (agent_id, fingerprint),
    CONSTRAINT ck_reflection_journals_quota CHECK (slot IN (1,2) AND revision>0),
    CONSTRAINT ck_reflection_journals_state CHECK ((state='pending' AND body IS NOT NULL AND fingerprint IS NOT NULL AND length(fingerprint)=64) OR (state='erased' AND body IS NULL AND fingerprint IS NULL)),
    CONSTRAINT uq_reflection_journals_id UNIQUE (id, agent_id),
    CONSTRAINT fk_reflection_journals_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE reflection_document_links (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_reflection_document_links_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    journal_id VARCHAR(36) NOT NULL,
    capture_id VARCHAR(36) NOT NULL,
    field VARCHAR(32) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    CONSTRAINT pk_reflection_document_links PRIMARY KEY (id),
    CONSTRAINT fk_reflection_document_links_journal_id_reflection_journals FOREIGN KEY(journal_id, agent_id) REFERENCES reflection_journals (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reflection_document_links_capture_id_context_documents FOREIGN KEY(capture_id, agent_id) REFERENCES context_documents (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_reflection_document_links_journal_id UNIQUE (journal_id, capture_id, field),
    CONSTRAINT uq_reflection_document_links_id UNIQUE (id, agent_id),
    CONSTRAINT fk_reflection_document_links_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_reflection_journals_agent_id ON reflection_journals (agent_id)""",
    """CREATE INDEX ix_reflection_capture ON reflection_document_links (capture_id)""",
    """CREATE INDEX ix_reflection_document_links_agent_id ON reflection_document_links (agent_id)""",
    """CREATE TRIGGER reflection_document_request AFTER INSERT ON document_erasure_jobs
BEGIN
    UPDATE reflection_journals SET state='erased', body=NULL, fingerprint=NULL, revision=revision+1
    WHERE agent_id=new.agent_id AND state='pending' AND id IN (
        SELECT link.journal_id FROM document_revisions rev
        JOIN context_documents cap ON cap.document_revision_id=rev.id
        JOIN reflection_document_links link ON link.capture_id=cap.id
        WHERE rev.document_id=new.document_id AND rev.agent_id=new.agent_id
    );
END""",
    """CREATE TRIGGER reflection_document_delete AFTER UPDATE OF deleted_at ON source_documents WHEN new.deleted_at IS NOT NULL
BEGIN
    UPDATE reflection_journals SET state='erased', body=NULL, fingerprint=NULL, revision=revision+1
    WHERE agent_id=new.agent_id AND state='pending' AND id IN (
        SELECT link.journal_id FROM document_revisions rev
        JOIN context_documents cap ON cap.document_revision_id=rev.id
        JOIN reflection_document_links link ON link.capture_id=cap.id
        WHERE rev.document_id=new.id AND rev.agent_id=new.agent_id
    );
END""",
    """CREATE TRIGGER reflection_erase_links AFTER UPDATE OF state ON reflection_journals
WHEN new.state='erased'
BEGIN
    DELETE FROM reflection_document_links WHERE journal_id=new.id AND agent_id=new.agent_id;
END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
