"""Explicit QQ conversation grants and atomic inbound run attribution."""

from alembic import op

revision = "v2_0027"
down_revision = "v2_0026"
branch_labels = None
depends_on = None

sql_statements = (
    "\nCREATE TABLE qq_bindings (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_qq_bindings_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tconfig_id VARCHAR(36) NOT NULL, \n\tsession_id VARCHAR(36) NOT NULL, \n\tscene VARCHAR(12) NOT NULL, \n\tpeer_id VARCHAR(19) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tpersona TEXT NOT NULL, \n\tcooldown_s INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tlast_received_at VARCHAR(40), \n\tCONSTRAINT pk_qq_bindings PRIMARY KEY (id), \n\tCONSTRAINT fk_qq_bindings_config_id_configurations FOREIGN KEY(config_id, agent_id) REFERENCES configurations (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_qq_bindings_session_id_sessions FOREIGN KEY(session_id, agent_id) REFERENCES sessions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_qq_bindings_agent_id UNIQUE (agent_id, config_id, scene, peer_id), \n\tCONSTRAINT uq_qq_bindings_session_id UNIQUE (session_id), \n\tCONSTRAINT ck_qq_bindings_scope CHECK (scene IN ('private','group') AND enabled IN (0,1)), \n\tCONSTRAINT ck_qq_bindings_peer CHECK (length(peer_id) BETWEEN 1 AND 19 AND peer_id NOT GLOB '*[^0-9]*' AND substr(peer_id,1,1)!='0' AND CAST(peer_id AS INTEGER)>0), \n\tCONSTRAINT ck_qq_bindings_persona CHECK (length(persona) BETWEEN 1 AND 8000 AND instr(persona,char(0))=0), \n\tCONSTRAINT ck_qq_bindings_revision CHECK (typeof(revision)='integer' AND revision>0 AND typeof(cooldown_s)='integer' AND cooldown_s BETWEEN 1 AND 3600), \n\tCONSTRAINT uq_qq_bindings_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_qq_bindings_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_qq_bindings_agent_id ON qq_bindings (agent_id)",
    "\nCREATE TABLE qq_runs (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_qq_runs_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\trun_id VARCHAR(36) NOT NULL, \n\tbinding_id VARCHAR(36) NOT NULL, \n\tbinding_revision INTEGER NOT NULL, \n\tconfig_revision_id VARCHAR(36) NOT NULL, \n\taccount_binding_id VARCHAR(36) NOT NULL, \n\tperson_id VARCHAR(36) NOT NULL, \n\tsender_id VARCHAR(19) NOT NULL, \n\tevent_key VARCHAR(64) NOT NULL, \n\tevent_fingerprint VARCHAR(64) NOT NULL, \n\tpersona TEXT NOT NULL, \n\tCONSTRAINT pk_qq_runs PRIMARY KEY (id), \n\tCONSTRAINT fk_qq_runs_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_qq_runs_binding_id_qq_bindings FOREIGN KEY(binding_id, agent_id) REFERENCES qq_bindings (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_qq_runs_config_revision_id_configuration_revisions FOREIGN KEY(config_revision_id, agent_id) REFERENCES configuration_revisions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_qq_runs_person_id_people FOREIGN KEY(person_id, agent_id) REFERENCES people (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_qq_runs_run_id UNIQUE (run_id), \n\tCONSTRAINT uq_qq_runs_agent_id UNIQUE (agent_id, event_key), \n\tCONSTRAINT ck_qq_runs_identity CHECK (binding_revision>0 AND length(event_key)=64 AND length(event_fingerprint)=64), \n\tCONSTRAINT ck_qq_runs_content CHECK (length(persona) BETWEEN 1 AND 8000 AND length(sender_id) BETWEEN 1 AND 19), \n\tCONSTRAINT uq_qq_runs_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_qq_runs_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_qq_runs_agent_id ON qq_runs (agent_id)",
)


sql_statements += (
    "CREATE TRIGGER qq_run_immutable BEFORE UPDATE ON qq_runs BEGIN SELECT RAISE(ABORT,'QQ inbound attribution is immutable'); END",
    "CREATE TRIGGER qq_run_retained BEFORE DELETE ON qq_runs BEGIN SELECT RAISE(ABORT,'QQ inbound attribution is retained'); END",
)


def upgrade() -> None:
    for statement in sql_statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
