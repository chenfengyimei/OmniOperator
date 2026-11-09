"""Owner semantic commands: atomic queueing and one-way argument transfer."""

from alembic import op

revision = "v2_0023"
down_revision = "v2_0022"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE runs ADD COLUMN execution_kind VARCHAR(12) NOT NULL DEFAULT 'model' CONSTRAINT ck_runs_execution_kind CHECK(execution_kind IN ('model','owner'))",
    "CREATE TRIGGER runs_execution_kind BEFORE UPDATE OF execution_kind ON runs WHEN new.execution_kind IS NOT old.execution_kind BEGIN SELECT RAISE(ABORT,'run execution kind is immutable'); END",
    "DROP TRIGGER runs_configuration_insert",
    "DROP TRIGGER runs_configuration_update",
    "CREATE TRIGGER runs_configuration_insert BEFORE INSERT ON runs WHEN NOT(new.execution_kind='model' AND new.error_code='legacy_configuration_unresolved' AND new.state IN ('succeeded','failed','cancelled','interrupted')) AND NOT EXISTS(SELECT 1 FROM settings_revisions p WHERE p.id=new.settings_revision_id AND p.agent_id=new.agent_id AND p.revision=new.config_revision AND new.policy_epoch=p.permission_epoch AND ((new.execution_kind='owner' AND new.model_revision_id IS NULL AND new.model_permission_epoch IS NULL) OR (new.execution_kind='model' AND EXISTS(SELECT 1 FROM configuration_revisions m WHERE m.config_id=p.model_profile_id AND m.id=new.model_revision_id AND m.kind='model' AND m.agent_id=new.agent_id AND new.model_permission_epoch>0)))) BEGIN SELECT RAISE(ABORT,'run configuration scope mismatch'); END",
    "CREATE TRIGGER runs_configuration_update BEFORE UPDATE ON runs WHEN NOT(new.execution_kind='model' AND new.error_code='legacy_configuration_unresolved' AND new.state IN ('succeeded','failed','cancelled','interrupted')) AND NOT EXISTS(SELECT 1 FROM settings_revisions p WHERE p.id=new.settings_revision_id AND p.agent_id=new.agent_id AND p.revision=new.config_revision AND new.policy_epoch=p.permission_epoch AND ((new.execution_kind='owner' AND new.model_revision_id IS NULL AND new.model_permission_epoch IS NULL) OR (new.execution_kind='model' AND EXISTS(SELECT 1 FROM configuration_revisions m WHERE m.config_id=p.model_profile_id AND m.id=new.model_revision_id AND m.kind='model' AND m.agent_id=new.agent_id AND new.model_permission_epoch>0)))) BEGIN SELECT RAISE(ABORT,'run configuration scope mismatch'); END",
    "\nCREATE TABLE owner_commands (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_owner_commands_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\trun_id VARCHAR(36) NOT NULL, \n\trequest_id VARCHAR(36) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\tsession_epoch INTEGER NOT NULL, \n\ttool VARCHAR(100) NOT NULL, \n\targuments JSON, \n\taction_id VARCHAR(36), \n\tCONSTRAINT pk_owner_commands PRIMARY KEY (id), \n\tCONSTRAINT fk_owner_commands_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_owner_commands_action_id_actions FOREIGN KEY(action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_owner_commands_agent_id UNIQUE (agent_id, request_id), \n\tCONSTRAINT uq_owner_commands_run_id UNIQUE (run_id), \n\tCONSTRAINT uq_owner_commands_action_id UNIQUE (action_id), \n\tCONSTRAINT ck_owner_commands_identity CHECK (length(request_id)=36 AND length(boot_id)=36 AND length(request_sha256)=64), \n\tCONSTRAINT ck_owner_commands_command CHECK (session_epoch>0 AND tool='desktop_semantic'), \n\tCONSTRAINT ck_owner_commands_single_body CHECK (action_id IS NULL OR arguments IS NULL), \n\tCONSTRAINT uq_owner_commands_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_owner_commands_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_owner_commands_agent_id ON owner_commands (agent_id)",
    "CREATE TRIGGER owner_command_identity BEFORE UPDATE ON owner_commands WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.run_id IS NOT old.run_id OR new.request_id IS NOT old.request_id OR new.request_sha256 IS NOT old.request_sha256 OR new.boot_id IS NOT old.boot_id OR new.session_epoch IS NOT old.session_epoch OR new.tool IS NOT old.tool OR (old.action_id IS NOT NULL AND new.action_id IS NOT old.action_id) OR (new.arguments IS NOT NULL AND new.arguments IS NOT old.arguments) BEGIN SELECT RAISE(ABORT,'owner command identity is immutable'); END",
    "CREATE TRIGGER owner_command_action BEFORE UPDATE OF action_id ON owner_commands WHEN new.action_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM actions WHERE id=new.action_id AND agent_id=new.agent_id AND run_id=new.run_id AND tool=new.tool AND arguments=old.arguments) BEGIN SELECT RAISE(ABORT,'owner command action binding mismatch'); END",
    "CREATE TRIGGER owner_command_live BEFORE INSERT ON owner_commands WHEN NOT EXISTS(SELECT 1 FROM runs JOIN sessions ON sessions.id=runs.session_id AND sessions.agent_id=runs.agent_id WHERE runs.id=new.run_id AND runs.agent_id=new.agent_id AND runs.state='queued' AND runs.execution_kind='owner' AND sessions.deleted_at IS NULL AND sessions.content_epoch=new.session_epoch) BEGIN SELECT RAISE(ABORT,'owner command session changed'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
