"""Scoped Android grants and shared owner-command/capture contracts; frozen DDL."""

from alembic import op

revision = "v2_0029"
down_revision = "v2_0028"
branch_labels = None
depends_on = None
SQL = (
    "\nCREATE TABLE android_grants (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_android_grants_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tconfig_id VARCHAR(36) NOT NULL, \n\tconfig_revision_id VARCHAR(36) NOT NULL, \n\tdevice JSON NOT NULL, \n\tdevice_sha256 VARCHAR(64) NOT NULL, \n\thistory_epoch VARCHAR(36) NOT NULL, \n\trevoked_at VARCHAR(40), \n\trevision INTEGER NOT NULL, \n\tCONSTRAINT pk_android_grants PRIMARY KEY (id), \n\tCONSTRAINT fk_android_grants_config_id_configurations FOREIGN KEY(config_id, agent_id) REFERENCES configurations (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_android_grants_config_revision_id_configuration_revisions FOREIGN KEY(config_revision_id, agent_id) REFERENCES configuration_revisions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_android_grants_device CHECK (json_valid(device) AND json_type(device)='object' AND length(CAST(device AS BLOB))<=2048 AND length(device_sha256)=64), \n\tCONSTRAINT ck_android_grants_revision CHECK (typeof(revision)='integer' AND revision>0), \n\tCONSTRAINT ck_android_grants_history_epoch CHECK (length(history_epoch)=36), \n\tCONSTRAINT uq_android_grants_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_android_grants_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_android_grants_agent_id ON android_grants (agent_id)",
    "CREATE TRIGGER android_grant_identity BEFORE UPDATE ON android_grants WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.history_epoch IS NOT old.history_epoch OR new.config_id IS NOT old.config_id OR new.config_revision_id IS NOT old.config_revision_id OR new.device IS NOT old.device OR new.device_sha256 IS NOT old.device_sha256 OR old.revoked_at IS NOT NULL OR new.revoked_at IS NULL OR new.revision!=old.revision+1 BEGIN SELECT RAISE(ABORT,'device grants only allow revocation'); END",
)
CAPTURE = (
    "CREATE TRIGGER capture_session_live BEFORE INSERT ON session_captures WHEN NOT EXISTS(SELECT 1 FROM sessions WHERE id=new.session_id AND agent_id=new.agent_id AND deleted_at IS NULL AND content_epoch=new.content_epoch) BEGIN SELECT RAISE(ABORT,'capture session changed'); END",
    "CREATE TRIGGER capture_identity BEFORE UPDATE ON session_captures BEGIN SELECT RAISE(ABORT,'capture references are immutable'); END",
    "CREATE TRIGGER capture_session_erased AFTER UPDATE OF deleted_at ON sessions WHEN new.deleted_at IS NOT NULL BEGIN DELETE FROM session_captures WHERE session_id=new.id AND agent_id=new.agent_id; END",
)
OWNER = (
    "CREATE TRIGGER owner_command_identity BEFORE UPDATE ON owner_commands WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.run_id IS NOT old.run_id OR new.request_id IS NOT old.request_id OR new.request_sha256 IS NOT old.request_sha256 OR new.boot_id IS NOT old.boot_id OR new.session_epoch IS NOT old.session_epoch OR new.tool IS NOT old.tool OR (old.action_id IS NOT NULL AND new.action_id IS NOT old.action_id) OR (new.arguments IS NOT NULL AND new.arguments IS NOT old.arguments) BEGIN SELECT RAISE(ABORT,'owner command identity is immutable'); END",
    "CREATE TRIGGER owner_command_action BEFORE UPDATE OF action_id ON owner_commands WHEN new.action_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM actions WHERE id=new.action_id AND agent_id=new.agent_id AND run_id=new.run_id AND tool=new.tool AND arguments=old.arguments) BEGIN SELECT RAISE(ABORT,'owner command action binding mismatch'); END",
    "CREATE TRIGGER owner_command_live BEFORE INSERT ON owner_commands WHEN NOT EXISTS(SELECT 1 FROM runs JOIN sessions ON sessions.id=runs.session_id AND sessions.agent_id=runs.agent_id WHERE runs.id=new.run_id AND runs.agent_id=new.agent_id AND runs.state='queued' AND runs.execution_kind='owner' AND sessions.deleted_at IS NULL AND sessions.content_epoch=new.session_epoch) BEGIN SELECT RAISE(ABORT,'owner command session changed'); END",
)


def upgrade():
    for statement in SQL:
        op.execute(statement)
    for name in ("capture_session_live", "capture_identity", "capture_session_erased"):
        op.execute("DROP TRIGGER " + name)
    with op.batch_alter_table("session_captures", recreate="always") as batch:
        batch.drop_constraint("ck_session_captures_method", type_="check")
        batch.create_check_constraint(
            op.f("ck_session_captures_method"),
            "method IN ('print_window','screen_capture_kit','x11_composite','pipewire_window','adb_screencap')",
        )
    for statement in CAPTURE:
        op.execute(statement)
    for name in ("owner_command_identity", "owner_command_action", "owner_command_live"):
        op.execute("DROP TRIGGER " + name)
    with op.batch_alter_table("owner_commands", recreate="always") as batch:
        batch.drop_constraint("ck_owner_commands_command", type_="check")
        batch.create_check_constraint(
            op.f("ck_owner_commands_command"), "session_epoch>0 AND tool IN ('desktop_semantic','android_input')"
        )
    for statement in OWNER:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Restore a verified backup into a new home instead of downgrading")
