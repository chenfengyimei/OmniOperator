"""Owner terminal metadata and immutable input receipts; no transcript or native PID execution."""

from alembic import op

revision = "v2_0021"
down_revision = "v2_0020"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE terminal_sessions (\n\tsequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, \n\tid VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\tshell VARCHAR(32) NOT NULL, \n\texecutable TEXT NOT NULL, \n\tcwd TEXT NOT NULL, \n\tworkspace_sha256 VARCHAR(64) NOT NULL, \n\tpermission_epoch INTEGER NOT NULL, \n\tcols INTEGER NOT NULL, \n\trows INTEGER NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tpid INTEGER, \n\tprocess_created FLOAT, \n\tcleanup_pending BOOLEAN NOT NULL, \n\terror_code VARCHAR(80) NOT NULL, \n\texit_code INTEGER, \n\tended_at VARCHAR(40), \n\tCONSTRAINT uq_terminal_sessions_id UNIQUE (id), \n\tCONSTRAINT uq_terminal_sessions_id UNIQUE (id, agent_id), \n\tCONSTRAINT ck_terminal_sessions_identity CHECK (length(id)=36 AND length(boot_id)=36 AND sequence>0), \n\tCONSTRAINT ck_terminal_sessions_profile CHECK (shell IN ('powershell','pwsh','cmd','bash','zsh','sh') AND length(executable) BETWEEN 1 AND 4000 AND length(cwd) BETWEEN 1 AND 4000), \n\tCONSTRAINT ck_terminal_sessions_workspace CHECK (length(workspace_sha256)=64 AND workspace_sha256 NOT GLOB '*[^0-9a-f]*'), \n\tCONSTRAINT ck_terminal_sessions_revision CHECK (typeof(permission_epoch)='integer' AND permission_epoch>0 AND typeof(revision)='integer' AND revision>0), \n\tCONSTRAINT ck_terminal_sessions_geometry CHECK (typeof(cols)='integer' AND cols BETWEEN 20 AND 500 AND typeof(rows)='integer' AND rows BETWEEN 5 AND 200), \n\tCONSTRAINT ck_terminal_sessions_process CHECK ((pid IS NULL AND process_created IS NULL) OR (typeof(pid)='integer' AND pid>0 AND process_created>0)), \n\tCONSTRAINT ck_terminal_sessions_state CHECK (length(error_code)<=80 AND cleanup_pending IN (0,1) AND state IN ('starting','running','closing','exited','unknown')), \n\tCONSTRAINT ck_terminal_sessions_exit CHECK ((state='exited' AND cleanup_pending=0 AND ended_at IS NOT NULL) OR (state!='exited' AND ended_at IS NULL)), \n\tCONSTRAINT ck_terminal_sessions_running CHECK (state!='running' OR (pid IS NOT NULL AND cleanup_pending=0)), \n\tCONSTRAINT ck_terminal_sessions_cleanup CHECK (state NOT IN ('starting','closing','unknown') OR cleanup_pending=1), \n\tCONSTRAINT fk_terminal_sessions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)",
    "CREATE INDEX ix_terminal_sessions_agent_id ON terminal_sessions (agent_id)",
    "CREATE INDEX ix_terminal_sessions_agent_sequence ON terminal_sessions (agent_id, sequence)",
    "CREATE INDEX ix_terminal_sessions_state ON terminal_sessions (boot_id, state)",
    "CREATE TABLE terminal_operations (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_terminal_operations_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsession_id VARCHAR(36) NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tbytes INTEGER NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tintent_audit_id VARCHAR(36) NOT NULL, \n\terror_code VARCHAR(80) NOT NULL, \n\tended_at VARCHAR(40), \n\tCONSTRAINT pk_terminal_operations PRIMARY KEY (id), \n\tCONSTRAINT fk_terminal_operations_session_id_terminal_sessions FOREIGN KEY(session_id, agent_id) REFERENCES terminal_sessions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_terminal_operations_intent_audit_id_audit FOREIGN KEY(intent_audit_id, agent_id) REFERENCES audit (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_terminal_operations_identity CHECK (length(boot_id)=36 AND kind IN ('open','input','resize','close')), \n\tCONSTRAINT ck_terminal_operations_fingerprint CHECK (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'), \n\tCONSTRAINT ck_terminal_operations_bytes CHECK (typeof(bytes)='integer' AND ((kind='input' AND bytes BETWEEN 1 AND 16384) OR (kind!='input' AND bytes=0))), \n\tCONSTRAINT ck_terminal_operations_state CHECK (state IN ('prepared','accepted','not_sent','unknown') AND length(error_code)<=80), \n\tCONSTRAINT ck_terminal_operations_settled CHECK ((state='prepared' AND ended_at IS NULL) OR (state!='prepared' AND ended_at IS NOT NULL)), \n\tCONSTRAINT uq_terminal_operations_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_terminal_operations_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)",
    "CREATE INDEX ix_terminal_operations_agent_id ON terminal_operations (agent_id)",
    "CREATE INDEX ix_terminal_operations_audit ON terminal_operations (agent_id, intent_audit_id)",
    "CREATE INDEX ix_terminal_operations_session ON terminal_operations (agent_id, session_id, created_at)",
    "CREATE TRIGGER terminal_session_identity BEFORE UPDATE ON terminal_sessions WHEN new.id IS NOT old.id OR new.sequence IS NOT old.sequence OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.boot_id IS NOT old.boot_id OR new.shell IS NOT old.shell OR new.executable IS NOT old.executable OR new.cwd IS NOT old.cwd OR new.workspace_sha256 IS NOT old.workspace_sha256 OR new.permission_epoch IS NOT old.permission_epoch OR new.revision!=old.revision+1 OR (old.pid IS NOT NULL AND (new.pid IS NOT old.pid OR new.process_created IS NOT old.process_created)) BEGIN SELECT RAISE(ABORT,'terminal identity and revisions are immutable'); END",
    "CREATE TRIGGER terminal_receipt_identity BEFORE UPDATE ON terminal_operations WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.session_id IS NOT old.session_id OR new.boot_id IS NOT old.boot_id OR new.kind IS NOT old.kind OR new.payload_sha256 IS NOT old.payload_sha256 OR new.bytes IS NOT old.bytes OR new.intent_audit_id IS NOT old.intent_audit_id OR old.state!='prepared' BEGIN SELECT RAISE(ABORT,'terminal operation receipts cannot be rewritten'); END",
    "CREATE TRIGGER terminal_session_keep BEFORE DELETE ON terminal_sessions BEGIN SELECT RAISE(ABORT,'terminal evidence cannot be deleted by a runtime cleanup'); END",
    "CREATE TRIGGER terminal_receipt_keep BEFORE DELETE ON terminal_operations BEGIN SELECT RAISE(ABORT,'terminal evidence cannot be deleted by a runtime cleanup'); END",
)


SESSION_GUARDS = (
    """CREATE TRIGGER terminal_session_transitions BEFORE UPDATE OF state,exit_code ON terminal_sessions WHEN
       (old.state='exited' AND new.state!='exited') OR
       (old.state='unknown' AND new.state NOT IN ('unknown','closing','exited')) OR
       (old.state='closing' AND new.state NOT IN ('closing','unknown','exited')) OR
       (old.state='running' AND new.state='starting') OR
       (old.exit_code IS NOT NULL AND new.exit_code IS NOT old.exit_code)
       BEGIN SELECT RAISE(ABORT,'terminal exit and unknown observations cannot be resurrected'); END""",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)
    for statement in SESSION_GUARDS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
