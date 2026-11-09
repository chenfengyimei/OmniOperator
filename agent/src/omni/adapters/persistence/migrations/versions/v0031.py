"""Pinned Unicode helper revisions and durable restoration obligations; frozen DDL."""

from alembic import op

revision = "v2_0031"
down_revision = "v2_0030"
branch_labels = None
depends_on = None

SQL = (
    "\nCREATE TABLE android_unicode_profiles (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_android_unicode_profiles_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tgrant_id VARCHAR(36) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tcredential_ref VARCHAR(200) NOT NULL, \n\tapk_sha256 VARCHAR(64) NOT NULL, \n\tcertificate_sha256 VARCHAR(64) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tCONSTRAINT pk_android_unicode_profiles PRIMARY KEY (id), \n\tCONSTRAINT fk_android_unicode_profiles_grant_id_android_grants FOREIGN KEY(grant_id, agent_id) REFERENCES android_grants (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_android_unicode_profiles_grant_id UNIQUE (grant_id, revision), \n\tCONSTRAINT ck_android_unicode_profiles_profile CHECK (revision>0 AND enabled IN (0,1) AND length(apk_sha256) IN (0,64) AND length(certificate_sha256) IN (0,64) AND length(fingerprint)=64 AND (enabled=0 OR (length(credential_ref)>0 AND length(apk_sha256)=64 AND length(certificate_sha256)=64))), \n\tCONSTRAINT uq_android_unicode_profiles_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_android_unicode_profiles_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_android_unicode_profiles_agent_id ON android_unicode_profiles (agent_id)",
    "\nCREATE TABLE android_unicode_environments (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_android_unicode_environments_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\taction_id VARCHAR(36) NOT NULL, \n\tgrant_id VARCHAR(36) NOT NULL, \n\tprofile_id VARCHAR(36) NOT NULL, \n\tdevice_serial VARCHAR(128) NOT NULL, \n\tserver_port INTEGER NOT NULL, \n\tprepare_nonce VARCHAR(36) NOT NULL, \n\toriginal_ime VARCHAR(400) NOT NULL, \n\toriginal_subtype INTEGER NOT NULL, \n\tenabled_sha256 VARCHAR(64) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcurrent_ime VARCHAR(400), \n\tcurrent_subtype INTEGER, \n\tresult_code VARCHAR(80) NOT NULL, \n\tobserved_at VARCHAR(40), \n\tCONSTRAINT pk_android_unicode_environments PRIMARY KEY (id), \n\tCONSTRAINT fk_android_unicode_environments_action_id_actions FOREIGN KEY(action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_android_unicode_environments_grant_id_android_grants FOREIGN KEY(grant_id, agent_id) REFERENCES android_grants (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_android_unicode_environments_profile_id_android_unicode_profiles FOREIGN KEY(profile_id, agent_id) REFERENCES android_unicode_profiles (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_android_unicode_environments_action_id UNIQUE (action_id), \n\tCONSTRAINT ck_android_unicode_environments_environment CHECK (state IN ('pending','restored','accepted') AND revision>0 AND server_port BETWEEN 1 AND 65535 AND length(prepare_nonce)=36 AND length(original_ime) BETWEEN 3 AND 400 AND length(enabled_sha256)=64), \n\tCONSTRAINT uq_android_unicode_environments_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_android_unicode_environments_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_android_unicode_environments_agent_id ON android_unicode_environments (agent_id)",
    "CREATE INDEX ix_android_unicode_environments_device ON android_unicode_environments (device_serial, state)",
    "CREATE TRIGGER android_unicode_profile_insert BEFORE INSERT ON android_unicode_profiles WHEN new.revision != (SELECT coalesce(max(revision),0)+1 FROM android_unicode_profiles WHERE grant_id=new.grant_id AND agent_id=new.agent_id) BEGIN SELECT RAISE(ABORT,'helper revision must increase by one'); END",
    "CREATE TRIGGER android_unicode_profile_update BEFORE UPDATE ON android_unicode_profiles BEGIN SELECT RAISE(ABORT,'helper profile revisions are immutable'); END",
    "CREATE TRIGGER android_unicode_profile_delete BEFORE DELETE ON android_unicode_profiles BEGIN SELECT RAISE(ABORT,'helper profile revisions are immutable'); END",
    "CREATE TRIGGER android_unicode_environment_update BEFORE UPDATE ON android_unicode_environments WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.action_id IS NOT old.action_id OR new.grant_id IS NOT old.grant_id OR new.profile_id IS NOT old.profile_id OR new.device_serial IS NOT old.device_serial OR new.server_port IS NOT old.server_port OR new.prepare_nonce IS NOT old.prepare_nonce OR new.original_ime IS NOT old.original_ime OR new.original_subtype IS NOT old.original_subtype OR new.enabled_sha256 IS NOT old.enabled_sha256 OR old.state!='pending' OR new.revision!=old.revision+1 OR new.state NOT IN ('pending','restored','accepted') BEGIN SELECT RAISE(ABORT,'IME obligation identity or transition is immutable'); END",
    "CREATE TRIGGER android_unicode_environment_delete BEFORE DELETE ON android_unicode_environments BEGIN SELECT RAISE(ABORT,'IME restoration evidence is retained'); END",
    "CREATE TRIGGER android_unicode_environment_scope BEFORE INSERT ON android_unicode_environments WHEN NOT EXISTS(SELECT 1 FROM android_unicode_profiles p JOIN android_grants g ON g.id=p.grant_id AND g.agent_id=p.agent_id JOIN actions a ON a.id=new.action_id AND a.agent_id=new.agent_id WHERE p.id=new.profile_id AND p.agent_id=new.agent_id AND p.grant_id=new.grant_id AND g.id=new.grant_id AND g.device->>'serial'=new.device_serial AND a.tool IN ('android_input','android_act') AND a.arguments->>'kind'='append_unicode' AND a.state='executing' AND ((a.tool='android_input' AND a.arguments->>'grant_id'=new.grant_id) OR (a.tool='android_act' AND EXISTS(SELECT 1 FROM android_access x WHERE x.id=a.arguments->>'access_id' AND x.agent_id=new.agent_id AND x.grant_id=new.grant_id)))) BEGIN SELECT RAISE(ABORT,'IME obligation scope mismatch'); END",
)
OWNER = (
    "CREATE TRIGGER owner_command_identity BEFORE UPDATE ON owner_commands WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.run_id IS NOT old.run_id OR new.request_id IS NOT old.request_id OR new.request_sha256 IS NOT old.request_sha256 OR new.boot_id IS NOT old.boot_id OR new.session_epoch IS NOT old.session_epoch OR new.tool IS NOT old.tool OR (old.action_id IS NOT NULL AND new.action_id IS NOT old.action_id) OR (new.arguments IS NOT NULL AND new.arguments IS NOT old.arguments) BEGIN SELECT RAISE(ABORT,'owner command identity is immutable'); END",
    "CREATE TRIGGER owner_command_action BEFORE UPDATE OF action_id ON owner_commands WHEN new.action_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM actions WHERE id=new.action_id AND agent_id=new.agent_id AND run_id=new.run_id AND tool=new.tool AND arguments=old.arguments) BEGIN SELECT RAISE(ABORT,'owner command action binding mismatch'); END",
    "CREATE TRIGGER owner_command_live BEFORE INSERT ON owner_commands WHEN NOT EXISTS(SELECT 1 FROM runs JOIN sessions ON sessions.id=runs.session_id AND sessions.agent_id=runs.agent_id WHERE runs.id=new.run_id AND runs.agent_id=new.agent_id AND runs.state='queued' AND runs.execution_kind='owner' AND sessions.deleted_at IS NULL AND sessions.content_epoch=new.session_epoch) BEGIN SELECT RAISE(ABORT,'owner command session changed'); END",
)
KINDS = (
    "CREATE TRIGGER android_access_kinds_update BEFORE UPDATE ON android_access_kinds BEGIN SELECT RAISE(ABORT,'approved run scope is immutable'); END",
    "CREATE TRIGGER android_access_kinds_delete BEFORE DELETE ON android_access_kinds BEGIN SELECT RAISE(ABORT,'approved run scope is immutable'); END",
)


def upgrade():
    for statement in SQL:
        op.execute(statement)
    for name in ("android_access_kinds_update", "android_access_kinds_delete"):
        op.execute("DROP TRIGGER " + name)
    with op.batch_alter_table("android_access_kinds", recreate="always") as batch:
        batch.drop_constraint("ck_android_access_kinds_kind", type_="check")
        batch.create_check_constraint(
            op.f("ck_android_access_kinds_kind"),
            "kind IN ('tap','toggle','append_ascii','append_unicode','long_press','swipe','key','launch')",
        )
    for statement in KINDS:
        op.execute(statement)
    for name in ("owner_command_identity", "owner_command_action", "owner_command_live"):
        op.execute("DROP TRIGGER " + name)
    with op.batch_alter_table("owner_commands", recreate="always") as batch:
        batch.drop_constraint("ck_owner_commands_command", type_="check")
        batch.create_check_constraint(
            op.f("ck_owner_commands_command"),
            "session_epoch>0 AND tool IN ('desktop_semantic','android_input','android_restore_ime')",
        )
    for statement in OWNER:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Restore a verified backup into a new home instead of downgrading")
