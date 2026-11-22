"""Reflection media provenance, revision-bound review and owner day slots."""

from alembic import op

revision = "v2_0052"
down_revision = "v2_0051"
branch_labels = None
depends_on = None

SQL = (
    """CREATE TABLE reflection_media_links (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_reflection_media_links_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    journal_id VARCHAR(36) NOT NULL,
    source_id VARCHAR(36) NOT NULL,
    action_id VARCHAR(36) NOT NULL,
    position INTEGER NOT NULL,
    field VARCHAR(32) NOT NULL,
    receipt JSON NOT NULL,
    CONSTRAINT pk_reflection_media_links PRIMARY KEY (id),
    CONSTRAINT fk_reflection_media_links_journal_id_reflection_journals FOREIGN KEY(journal_id, agent_id) REFERENCES reflection_journals (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reflection_media_links_source_id_media_sources FOREIGN KEY(source_id, agent_id) REFERENCES media_sources (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reflection_media_links_action_id_actions FOREIGN KEY(action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_reflection_media_links_journal_id UNIQUE (journal_id, action_id, position, field),
    CONSTRAINT uq_reflection_media_links_id UNIQUE (id, agent_id),
    CONSTRAINT fk_reflection_media_links_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE reflection_reads (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_reflection_reads_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    journal_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    revision INTEGER NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    CONSTRAINT pk_reflection_reads PRIMARY KEY (id),
    CONSTRAINT fk_reflection_reads_journal_id_reflection_journals FOREIGN KEY(journal_id, agent_id) REFERENCES reflection_journals (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reflection_reads_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_reflection_reads_journal_id UNIQUE (journal_id, run_id, revision),
    CONSTRAINT uq_reflection_reads_id UNIQUE (id, agent_id),
    CONSTRAINT fk_reflection_reads_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE reflection_candidates (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_reflection_candidates_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    journal_id VARCHAR(36) NOT NULL,
    read_id VARCHAR(36) NOT NULL,
    journal_revision INTEGER NOT NULL,
    field VARCHAR(32) NOT NULL,
    start INTEGER NOT NULL,
    "end" INTEGER NOT NULL,
    digest VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL,
    revision INTEGER NOT NULL,
    reason VARCHAR(32) NOT NULL,
    replaces VARCHAR(36),
    CONSTRAINT pk_reflection_candidates PRIMARY KEY (id),
    CONSTRAINT fk_reflection_candidates_journal_id_reflection_journals FOREIGN KEY(journal_id, agent_id) REFERENCES reflection_journals (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reflection_candidates_read_id_reflection_reads FOREIGN KEY(read_id, agent_id) REFERENCES reflection_reads (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_reflection_candidates_journal_id UNIQUE (journal_id, journal_revision),
    CONSTRAINT ck_reflection_candidates_state CHECK (state IN ('proposed','skipped','approved','rejected','superseded','retracted') AND revision>0),
    CONSTRAINT uq_reflection_candidates_id UNIQUE (id, agent_id),
    CONSTRAINT fk_reflection_candidates_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE reflection_slots (
    id VARCHAR(36) NOT NULL CONSTRAINT ck_reflection_slots_uuid_length CHECK (length(id) = 36),
    agent_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    journal_id VARCHAR(36) NOT NULL,
    source_day VARCHAR(10) NOT NULL,
    approved_day VARCHAR(10) NOT NULL,
    active_candidate VARCHAR(36),
    withdrawn BOOLEAN NOT NULL,
    CONSTRAINT pk_reflection_slots PRIMARY KEY (id),
    CONSTRAINT uq_reflection_approval_day UNIQUE (agent_id, approved_day),
    CONSTRAINT uq_reflection_source_day UNIQUE (agent_id, source_day),
    CONSTRAINT uq_reflection_slots_journal_id UNIQUE (journal_id),
    CONSTRAINT fk_reflection_slots_journal_id_reflection_journals FOREIGN KEY(journal_id, agent_id) REFERENCES reflection_journals (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reflection_slots_active_candidate_reflection_candidates FOREIGN KEY(active_candidate, agent_id) REFERENCES reflection_candidates (id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT uq_reflection_slots_id UNIQUE (id, agent_id),
    CONSTRAINT fk_reflection_slots_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_reflection_media_links_agent_id ON reflection_media_links (agent_id)""",
    """CREATE INDEX ix_reflection_media_source ON reflection_media_links (source_id)""",
    """CREATE INDEX ix_reflection_reads_agent_id ON reflection_reads (agent_id)""",
    """CREATE INDEX ix_reflection_candidates_agent_id ON reflection_candidates (agent_id)""",
    """CREATE INDEX ix_reflection_slots_agent_id ON reflection_slots (agent_id)""",
    """CREATE TRIGGER reflection_media_delete BEFORE DELETE ON media_sources BEGIN
    UPDATE reflection_journals SET state='erased', body=NULL, fingerprint=NULL, revision=revision+1
    WHERE agent_id=old.agent_id AND state='pending' AND id IN
        (SELECT journal_id FROM reflection_media_links WHERE source_id=old.id);
END""",
    """CREATE TRIGGER reflection_erase_review AFTER UPDATE OF state ON reflection_journals
WHEN new.state='erased' BEGIN
    UPDATE reflection_slots SET active_candidate=NULL, withdrawn=1 WHERE journal_id=new.id;
    DELETE FROM reflection_candidates WHERE journal_id=new.id;
    DELETE FROM reflection_reads WHERE journal_id=new.id;
    DELETE FROM reflection_media_links WHERE journal_id=new.id;
END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
