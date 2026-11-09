"""Normalized per-run Android authority bound to the common approval ledger; frozen DDL."""

from alembic import op

revision = "v2_0030"
down_revision = "v2_0029"
branch_labels = None
depends_on = None
SQL = (
    "\nCREATE TABLE android_access (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_android_access_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tgrant_id VARCHAR(36) NOT NULL, \n\trun_id VARCHAR(36) NOT NULL, \n\tsource_action_id VARCHAR(36) NOT NULL, \n\tsession_epoch INTEGER NOT NULL, \n\tpolicy_revision INTEGER NOT NULL, \n\tscope_sha256 VARCHAR(64) NOT NULL, \n\texpires_at VARCHAR(40) NOT NULL, \n\trevoked_at VARCHAR(40), \n\trevision INTEGER NOT NULL, \n\tCONSTRAINT pk_android_access PRIMARY KEY (id), \n\tCONSTRAINT fk_android_access_grant_id_android_grants FOREIGN KEY(grant_id, agent_id) REFERENCES android_grants (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_android_access_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_android_access_source_action_id_actions FOREIGN KEY(source_action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_android_access_source_action_id UNIQUE (source_action_id), \n\tCONSTRAINT ck_android_access_identity CHECK (session_epoch>0 AND policy_revision>0 AND revision>0 AND length(expires_at) BETWEEN 20 AND 40), \n\tCONSTRAINT ck_android_access_scope CHECK (length(scope_sha256)=64), \n\tCONSTRAINT uq_android_access_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_android_access_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_android_access_agent_id ON android_access (agent_id)",
    "\nCREATE TABLE android_access_packages (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_android_access_packages_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\taccess_id VARCHAR(36) NOT NULL, \n\tpackage VARCHAR(200) NOT NULL, \n\tCONSTRAINT pk_android_access_packages PRIMARY KEY (id), \n\tCONSTRAINT fk_android_access_packages_access_id_android_access FOREIGN KEY(access_id, agent_id) REFERENCES android_access (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_android_access_packages_access_id UNIQUE (access_id, package), \n\tCONSTRAINT ck_android_access_packages_package CHECK (length(package) BETWEEN 3 AND 200 AND instr(package,'.')>1 AND package NOT GLOB '*[^A-Za-z0-9_.]*'), \n\tCONSTRAINT uq_android_access_packages_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_android_access_packages_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_android_access_packages_agent_id ON android_access_packages (agent_id)",
    "\nCREATE TABLE android_access_kinds (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_android_access_kinds_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\taccess_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\tCONSTRAINT pk_android_access_kinds PRIMARY KEY (id), \n\tCONSTRAINT fk_android_access_kinds_access_id_android_access FOREIGN KEY(access_id, agent_id) REFERENCES android_access (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_android_access_kinds_access_id UNIQUE (access_id, kind), \n\tCONSTRAINT ck_android_access_kinds_kind CHECK (kind IN ('tap','toggle','append_ascii','long_press','swipe','key','launch')), \n\tCONSTRAINT uq_android_access_kinds_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_android_access_kinds_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_android_access_kinds_agent_id ON android_access_kinds (agent_id)",
    "CREATE TRIGGER android_access_identity BEFORE UPDATE ON android_access WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.grant_id IS NOT old.grant_id OR new.run_id IS NOT old.run_id OR new.source_action_id IS NOT old.source_action_id OR new.session_epoch IS NOT old.session_epoch OR new.policy_revision IS NOT old.policy_revision OR new.scope_sha256 IS NOT old.scope_sha256 OR new.expires_at IS NOT old.expires_at OR old.revoked_at IS NOT NULL OR new.revoked_at IS NULL OR new.revision!=old.revision+1 BEGIN SELECT RAISE(ABORT,'run access only permits revocation'); END",
    "CREATE TRIGGER android_access_packages_update BEFORE UPDATE ON android_access_packages BEGIN SELECT RAISE(ABORT,'approved run scope is immutable'); END",
    "CREATE TRIGGER android_access_packages_delete BEFORE DELETE ON android_access_packages BEGIN SELECT RAISE(ABORT,'approved run scope is immutable'); END",
    "CREATE TRIGGER android_access_kinds_update BEFORE UPDATE ON android_access_kinds BEGIN SELECT RAISE(ABORT,'approved run scope is immutable'); END",
    "CREATE TRIGGER android_access_kinds_delete BEFORE DELETE ON android_access_kinds BEGIN SELECT RAISE(ABORT,'approved run scope is immutable'); END",
)


def upgrade():
    for statement in SQL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Restore a verified backup into a new home instead of downgrading")
