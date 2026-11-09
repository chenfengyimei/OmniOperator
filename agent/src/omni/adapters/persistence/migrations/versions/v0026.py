"""Owner terminal observations, immutable metadata and explicitly erasable evidence."""

from alembic import op

revision = "v2_0026"
down_revision = "v2_0025"
branch_labels = None
depends_on = None

SQL = (
    "CREATE INDEX ix_terminal_operations_review_cursor ON terminal_operations (agent_id, state, id)",
    "CREATE UNIQUE INDEX uq_terminal_operations_review_scope ON terminal_operations (id, session_id, agent_id)",
    "\nCREATE TABLE terminal_reviews (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_terminal_reviews_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsession_id VARCHAR(36) NOT NULL, \n\toperation_id VARCHAR(36) NOT NULL, \n\trevision INTEGER NOT NULL, \n\thistory_epoch VARCHAR(36) NOT NULL, \n\tpolicy VARCHAR(32) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tproof JSON NOT NULL, \n\tdecision VARCHAR(20) NOT NULL, \n\tevidence_sha256 VARCHAR(64) NOT NULL, \n\tevidence TEXT, \n\tevidence_erased_at VARCHAR(40), \n\taudit_id VARCHAR(36) NOT NULL, \n\tCONSTRAINT pk_terminal_reviews PRIMARY KEY (id), \n\tCONSTRAINT fk_terminal_reviews_session_id_terminal_sessions FOREIGN KEY(session_id, agent_id) REFERENCES terminal_sessions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_terminal_reviews_operation_id_terminal_operations FOREIGN KEY(operation_id, session_id, agent_id) REFERENCES terminal_operations (id, session_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_terminal_reviews_audit_id_audit FOREIGN KEY(audit_id, agent_id) REFERENCES audit (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_terminal_reviews_agent_id UNIQUE (agent_id, operation_id, revision), \n\tCONSTRAINT ck_terminal_reviews_identity CHECK (typeof(revision)='integer' AND revision BETWEEN 1 AND 100 AND length(history_epoch)=36 AND policy='terminal-review-1'), \n\tCONSTRAINT ck_terminal_reviews_decision CHECK (decision IN ('applied','not_applied','still_unknown')), \n\tCONSTRAINT ck_terminal_reviews_fingerprints CHECK (length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*' AND length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*' AND length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'), \n\tCONSTRAINT ck_terminal_reviews_evidence CHECK ((evidence IS NOT NULL AND length(evidence) BETWEEN 1 AND 2000 AND length(CAST(evidence AS BLOB))<=8000 AND instr(evidence,char(0))=0 AND evidence_erased_at IS NULL) OR (evidence IS NULL AND evidence_erased_at IS NOT NULL AND length(evidence_erased_at) BETWEEN 1 AND 40)), \n\tCONSTRAINT ck_terminal_reviews_proof CHECK (json_valid(proof) AND length(CAST(proof AS BLOB))<=16384 AND COALESCE(json_type(proof,'$.schema')='integer' AND json_extract(proof,'$.schema')=1,0)), \n\tCONSTRAINT uq_terminal_reviews_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_terminal_reviews_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_terminal_reviews_agent_id ON terminal_reviews (agent_id)",
    "CREATE INDEX ix_terminal_reviews_audit ON terminal_reviews (agent_id, audit_id)",
    "CREATE TRIGGER terminal_review_source BEFORE INSERT ON terminal_reviews WHEN NOT EXISTS(SELECT 1 FROM terminal_operations o WHERE o.id=new.operation_id AND o.session_id=new.session_id AND o.agent_id=new.agent_id AND o.state='unknown' AND o.ended_at IS NOT NULL) OR NOT EXISTS(SELECT 1 FROM history_identity WHERE epoch=new.history_epoch) BEGIN SELECT RAISE(ABORT,'terminal review source changed'); END",
    "CREATE TRIGGER terminal_review_revision BEFORE INSERT ON terminal_reviews WHEN new.revision != (SELECT COALESCE(MAX(revision),0)+1 FROM terminal_reviews WHERE agent_id=new.agent_id AND operation_id=new.operation_id) BEGIN SELECT RAISE(ABORT,'terminal review revision changed'); END",
    "CREATE TRIGGER terminal_review_immutable BEFORE UPDATE ON terminal_reviews WHEN NOT(old.evidence IS NOT NULL AND new.evidence IS NULL AND new.evidence_erased_at IS NOT NULL AND new.id IS old.id AND new.agent_id IS old.agent_id AND new.created_at IS old.created_at AND new.session_id IS old.session_id AND new.operation_id IS old.operation_id AND new.revision IS old.revision AND new.history_epoch IS old.history_epoch AND new.policy IS old.policy AND new.fingerprint IS old.fingerprint AND new.request_sha256 IS old.request_sha256 AND new.proof IS old.proof AND new.decision IS old.decision AND new.evidence_sha256 IS old.evidence_sha256 AND new.audit_id IS old.audit_id) BEGIN SELECT RAISE(ABORT,'terminal review observation is immutable'); END",
    "CREATE TRIGGER terminal_review_delete BEFORE DELETE ON terminal_reviews BEGIN SELECT RAISE(ABORT,'terminal review observation is retained'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
