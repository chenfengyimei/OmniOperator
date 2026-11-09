"""Typed business configuration and immutable run references; frozen migration."""

import hashlib
import json
import unicodedata
import uuid

from alembic import op

revision = "v2_0005"
down_revision = "v2_0004"
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    op.execute("CREATE TEMP TABLE _v5_settings AS SELECT * FROM settings")
    op.execute("CREATE TEMP TABLE _v5_runs AS SELECT id,agent_id,config_revision,config_snapshot FROM runs")
    op.execute("CREATE TEMP TABLE _v5_presets AS SELECT * FROM presets")
    op.execute("DROP TABLE settings")
    op.execute("DROP TABLE presets")
    for statement in TABLES:
        op.execute(statement)
    for statement in INDEXES:
        op.execute(statement)
    for statement in COLUMNS:
        op.execute(statement)
    options = {}
    ordinal = {}

    def configuration(agent, kind, name, value, now, number=1, identity=None):
        entity = identity or str(uuid.uuid4())
        rev = str(uuid.uuid4())
        key = unicodedata.normalize("NFKC", name.strip()).casefold()
        digest = hashlib.sha256(
            json.dumps([kind, name, True, value], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        db.exec_driver_sql(
            "INSERT INTO configurations(id,agent_id,created_at,kind,name,name_key,enabled,revision,permission_epoch) VALUES(?,?,?,?,?,?,1,?,1)",
            (entity, agent, now, kind, name, key, number),
        )
        db.exec_driver_sql(
            "INSERT INTO configuration_revisions(id,agent_id,created_at,config_id,kind,revision,name,enabled,fingerprint) VALUES(?,?,?,?,?,?,?,1,?)",
            (rev, agent, now, entity, kind, number, name, digest),
        )
        table = "model_options" if kind == "model" else "preset_options"
        fields = ["revision_id", "agent_id", "kind", *value]
        db.exec_driver_sql(
            f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
            (rev, agent, kind, *value.values()),
        )
        return entity, rev

    def model(agent, value, now):
        payload = {
            **DEFAULT_MODEL,
            "model": value.get("model", ""),
            "base_url": value.get("model_base_url", ""),
            "credential_ref": value.get("credential_ref", ""),
        }
        key = (agent, json.dumps(payload, sort_keys=True))
        if key not in options:
            ordinal[agent] = ordinal.get(agent, 0) + 1
            options[key] = configuration(agent, "model", f"Migrated model {ordinal[agent]}", payload, now)
        return options[key]

    preferences = {}

    def preference(agent, number, value, now):
        model_id, model_revision = model(agent, value, now)
        payload = {k: value[k] for k in PREF_KEYS}
        payload["model_profile_id"] = model_id
        canonical = json.dumps(payload, sort_keys=True)
        key = (agent, number)
        if key in preferences:
            if preferences[key][1] != canonical:
                raise RuntimeError("Conflicting historical configuration revision")
            return preferences[key][0], model_revision, payload
        identity = str(uuid.uuid4())
        fields = ["id", "agent_id", "created_at", "revision", "permission_epoch", *payload]
        values = [
            identity,
            agent,
            now,
            number,
            number,
            *[json.dumps(v) if k == "disabled_tools" else v for k, v in payload.items()],
        ]
        db.exec_driver_sql(
            f"INSERT INTO settings_revisions({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(values)
        )
        preferences[key] = (identity, canonical)
        return identity, model_revision, payload

    previous = {r["agent_id"]: dict(r) for r in db.exec_driver_sql("SELECT * FROM _v5_settings").mappings()}
    for row in db.exec_driver_sql("SELECT id,created_at FROM agents").mappings():
        agent, now = row["id"], row["created_at"]
        old = previous.get(agent)
        value = json.loads(old["value"]) if old else dict(DEFAULT_PREFERENCES)
        if not set(PREF_KEYS) <= set(value):
            raise RuntimeError("Current Agent settings are incomplete")
        number = old["revision"] if old else 1
        _, _, payload = preference(agent, number, value, now)
        fields = [
            "agent_id",
            "revision",
            "permission_epoch",
            "updated_at",
            *[k for k in payload if k != "disabled_tools"],
        ]
        db.exec_driver_sql(
            f"INSERT INTO settings({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
            (
                agent,
                number,
                number,
                old["updated_at"] if old else now,
                *[v for k, v in payload.items() if k != "disabled_tools"],
            ),
        )
        for tool in payload["disabled_tools"]:
            db.exec_driver_sql("INSERT INTO settings_disabled_tools(agent_id,tool) VALUES(?,?)", (agent, tool))
    for row in db.exec_driver_sql("SELECT * FROM _v5_runs").mappings():
        value = json.loads(row["config_snapshot"])
        if not set(PREF_KEYS) <= set(value) or "model" not in value:
            db.exec_driver_sql(
                "UPDATE runs SET state=CASE WHEN state IN ('queued','running','waiting_approval','cancelling') THEN 'interrupted' ELSE state END,error_code='legacy_configuration_unresolved' WHERE id=?",
                (row["id"],),
            )
            db.exec_driver_sql(
                "UPDATE work_items SET state='unknown' WHERE run_id=? AND state IN ('pending','running')",
                (row["id"],),
            )
            continue
        created = db.exec_driver_sql("SELECT created_at FROM runs WHERE id=?", (row["id"],)).scalar_one()
        pref, rev, _ = preference(row["agent_id"], row["config_revision"], value, created)
        db.exec_driver_sql(
            "UPDATE runs SET settings_revision_id=?,model_revision_id=?,model_permission_epoch=1,policy_epoch=? WHERE id=?",
            (pref, rev, row["config_revision"], row["id"]),
        )
    for row in db.exec_driver_sql("SELECT * FROM _v5_presets").mappings():
        configuration(
            row["agent_id"],
            "preset",
            row["name"],
            {"content": row["content"]},
            row["created_at"],
            row["revision"],
            row["id"],
        )
    op.execute("ALTER TABLE runs DROP COLUMN config_snapshot")
    for statement in TRIGGERS:
        op.execute(statement)
    for table in (
        "configuration_revisions",
        "model_options",
        "channel_options",
        "mcp_options",
        "preset_options",
        "settings_revisions",
    ):
        for event in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'configuration revisions are immutable'); END"
            )
    for table in ("_v5_settings", "_v5_runs", "_v5_presets"):
        op.execute("DROP TABLE " + table)


def downgrade():
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")


PREF_KEYS = ("persona", "workspace", "daily_budget_micro", "call_reservation_micro", "max_steps", "disabled_tools")
DEFAULT_PREFERENCES = {
    "persona": "You are a helpful personal assistant.",
    "workspace": "",
    "daily_budget_micro": 1000000,
    "call_reservation_micro": 10000,
    "max_steps": 12,
    "disabled_tools": [],
    "model": "",
}
DEFAULT_MODEL = {
    "model": "",
    "base_url": "",
    "credential_ref": "",
    "context_tokens": 32768,
    "output_tokens": 2048,
    "summary_tokens": 1024,
    "margin_tokens": 1024,
    "counter_mode": "utf8_upper_bound",
    "counter_ref": "utf8-envelope-v1",
}

TABLES = (
    "\nCREATE TABLE configurations (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_configurations_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tname_key VARCHAR(200) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\trevision INTEGER NOT NULL, \n\tpermission_epoch INTEGER NOT NULL, \n\tdeleted_at VARCHAR(40), \n\tCONSTRAINT pk_configurations PRIMARY KEY (id), \n\tCONSTRAINT uq_configurations_id UNIQUE (id, agent_id, kind), \n\tCONSTRAINT ck_configurations_kind CHECK (kind IN ('model','channel','mcp','preset')), \n\tCONSTRAINT ck_configurations_bounds CHECK (revision>0 AND permission_epoch>0 AND enabled IN (0,1) AND length(trim(name)) BETWEEN 1 AND 100), \n\tCONSTRAINT fk_configurations_id_configuration_revisions FOREIGN KEY(id, revision, agent_id) REFERENCES configuration_revisions (config_id, revision, agent_id) DEFERRABLE INITIALLY DEFERRED, \n\tCONSTRAINT uq_configurations_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_configurations_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE configuration_revisions (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_configuration_revisions_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tconfig_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\trevision INTEGER NOT NULL CONSTRAINT ck_configuration_revisions_revision CHECK (revision>0), \n\tname VARCHAR(100) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL CONSTRAINT ck_configuration_revisions_fingerprint CHECK (length(fingerprint)=64), \n\tCONSTRAINT pk_configuration_revisions PRIMARY KEY (id), \n\tCONSTRAINT fk_configuration_revisions_config_id_configurations FOREIGN KEY(config_id, agent_id, kind) REFERENCES configurations (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT uq_configuration_revisions_config_id UNIQUE (config_id, revision, agent_id), \n\tCONSTRAINT uq_configuration_revisions_id UNIQUE (id, agent_id, kind), \n\tCONSTRAINT uq_configuration_revisions_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_configuration_revisions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE model_options (\n\trevision_id VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(20) DEFAULT 'model' NOT NULL CONSTRAINT ck_model_options_kind CHECK (kind='model'), \n\tmodel VARCHAR(200) NOT NULL, \n\tbase_url VARCHAR(2000) NOT NULL, \n\tcredential_ref VARCHAR(200) NOT NULL, \n\tcontext_tokens INTEGER NOT NULL, \n\toutput_tokens INTEGER NOT NULL, \n\tsummary_tokens INTEGER NOT NULL, \n\tmargin_tokens INTEGER NOT NULL, \n\tcounter_mode VARCHAR(30) NOT NULL, \n\tcounter_ref VARCHAR(100) NOT NULL, \n\tCONSTRAINT pk_model_options PRIMARY KEY (revision_id), \n\tCONSTRAINT fk_model_options_revision_id_configuration_revisions FOREIGN KEY(revision_id, agent_id, kind) REFERENCES configuration_revisions (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT ck_model_options_capacity CHECK (context_tokens BETWEEN 1024 AND 2000000 AND output_tokens>0 AND summary_tokens>0 AND margin_tokens>=128 AND output_tokens+margin_tokens<context_tokens AND summary_tokens+margin_tokens<context_tokens), \n\tCONSTRAINT ck_model_options_counter CHECK (counter_mode IN ('utf8_upper_bound','required') AND length(counter_ref)>0)\n)\n\n",
    "\nCREATE TABLE channel_options (\n\trevision_id VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(20) DEFAULT 'channel' NOT NULL CONSTRAINT ck_channel_options_kind CHECK (kind='channel'), \n\tdriver VARCHAR(20) NOT NULL, \n\tendpoint VARCHAR(2000) NOT NULL, \n\taccount VARCHAR(200) NOT NULL, \n\tcredential_ref VARCHAR(200) NOT NULL, \n\tCONSTRAINT pk_channel_options PRIMARY KEY (revision_id), \n\tCONSTRAINT fk_channel_options_revision_id_configuration_revisions FOREIGN KEY(revision_id, agent_id, kind) REFERENCES configuration_revisions (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT ck_channel_options_driver CHECK (driver IN ('qq','bilibili','android'))\n)\n\n",
    "\nCREATE TABLE mcp_options (\n\trevision_id VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(20) DEFAULT 'mcp' NOT NULL CONSTRAINT ck_mcp_options_kind CHECK (kind='mcp'), \n\ttransport VARCHAR(30) NOT NULL, \n\tcommand VARCHAR(4000) NOT NULL, \n\targuments JSON NOT NULL, \n\tendpoint VARCHAR(2000) NOT NULL, \n\tauth_mode VARCHAR(20) NOT NULL, \n\tcredential_ref VARCHAR(200) NOT NULL, \n\tCONSTRAINT pk_mcp_options PRIMARY KEY (revision_id), \n\tCONSTRAINT fk_mcp_options_revision_id_configuration_revisions FOREIGN KEY(revision_id, agent_id, kind) REFERENCES configuration_revisions (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT ck_mcp_options_auth_mode CHECK (auth_mode IN ('none','bearer','oauth')), \n\tCONSTRAINT ck_mcp_options_transport CHECK ((transport='stdio' AND endpoint='' AND auth_mode='none') OR (transport='streamable_http' AND command='' AND json_array_length(arguments)=0 AND length(endpoint)>0))\n)\n\n",
    "\nCREATE TABLE preset_options (\n\trevision_id VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(20) DEFAULT 'preset' NOT NULL CONSTRAINT ck_preset_options_kind CHECK (kind='preset'), \n\tcontent TEXT NOT NULL CONSTRAINT ck_preset_options_length CHECK (length(content)<=50000), \n\tCONSTRAINT pk_preset_options PRIMARY KEY (revision_id), \n\tCONSTRAINT fk_preset_options_revision_id_configuration_revisions FOREIGN KEY(revision_id, agent_id, kind) REFERENCES configuration_revisions (id, agent_id, kind) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE settings (\n\tagent_id VARCHAR(36) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at VARCHAR(40) NOT NULL, \n\tpermission_epoch INTEGER NOT NULL CONSTRAINT ck_settings_permission_epoch CHECK (permission_epoch>0), \n\tpersona TEXT NOT NULL, \n\tworkspace TEXT NOT NULL, \n\tdaily_budget_micro INTEGER NOT NULL CONSTRAINT ck_settings_daily_budget CHECK (daily_budget_micro>=0), \n\tcall_reservation_micro INTEGER NOT NULL CONSTRAINT ck_settings_call_budget CHECK (call_reservation_micro>0), \n\tmax_steps INTEGER NOT NULL CONSTRAINT ck_settings_step_budget CHECK (max_steps BETWEEN 1 AND 32), \n\tmodel_profile_id VARCHAR(36) NOT NULL, \n\tmodel_kind VARCHAR(20) DEFAULT 'model' NOT NULL CONSTRAINT ck_settings_model_kind CHECK (model_kind='model'), \n\tCONSTRAINT fk_settings_model_profile_id_configurations FOREIGN KEY(model_profile_id, agent_id, model_kind) REFERENCES configurations (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT uq_settings_agent_id UNIQUE (agent_id), \n\tCONSTRAINT ck_settings_revision CHECK (revision>0), \n\tCONSTRAINT fk_settings_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_settings_agent_id_settings_revisions FOREIGN KEY(agent_id, revision) REFERENCES settings_revisions (agent_id, revision) DEFERRABLE INITIALLY DEFERRED\n)\n\n",
    "\nCREATE TABLE settings_disabled_tools (\n\tagent_id VARCHAR(36) NOT NULL, \n\ttool VARCHAR(100) NOT NULL CONSTRAINT ck_settings_disabled_tools_tool CHECK (length(tool) BETWEEN 1 AND 100), \n\tCONSTRAINT pk_settings_disabled_tools PRIMARY KEY (agent_id, tool), \n\tCONSTRAINT fk_settings_disabled_tools_agent_id_settings FOREIGN KEY(agent_id) REFERENCES settings (agent_id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE settings_revisions (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_settings_revisions_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\trevision INTEGER NOT NULL CONSTRAINT ck_settings_revisions_revision CHECK (revision>0), \n\tpersona TEXT NOT NULL, \n\tworkspace TEXT NOT NULL, \n\tdaily_budget_micro INTEGER NOT NULL CONSTRAINT ck_settings_revisions_daily_budget CHECK (daily_budget_micro>=0), \n\tcall_reservation_micro INTEGER NOT NULL CONSTRAINT ck_settings_revisions_call_budget CHECK (call_reservation_micro>0), \n\tmax_steps INTEGER NOT NULL CONSTRAINT ck_settings_revisions_step_budget CHECK (max_steps BETWEEN 1 AND 32), \n\tmodel_profile_id VARCHAR(36) NOT NULL, \n\tmodel_kind VARCHAR(20) DEFAULT 'model' NOT NULL CONSTRAINT ck_settings_revisions_model_kind CHECK (model_kind='model'), \n\tpermission_epoch INTEGER NOT NULL, \n\tdisabled_tools JSON NOT NULL, \n\tCONSTRAINT pk_settings_revisions PRIMARY KEY (id), \n\tCONSTRAINT fk_settings_revisions_model_profile_id_configurations FOREIGN KEY(model_profile_id, agent_id, model_kind) REFERENCES configurations (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT uq_settings_revisions_agent_id UNIQUE (agent_id, revision), \n\tCONSTRAINT uq_settings_revisions_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_settings_revisions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
)

INDEXES = (
    "CREATE INDEX ix_configuration_cursor ON configurations (agent_id, kind, id)",
    "CREATE INDEX ix_configurations_agent_id ON configurations (agent_id)",
    "CREATE UNIQUE INDEX uq_configuration_name ON configurations (agent_id, kind, name_key) WHERE deleted_at IS NULL",
    "CREATE INDEX ix_configuration_revisions_agent_id ON configuration_revisions (agent_id)",
    "CREATE INDEX ix_settings_agent_id ON settings (agent_id)",
    "CREATE INDEX ix_settings_revisions_agent_id ON settings_revisions (agent_id)",
)

COLUMNS = (
    "ALTER TABLE runs ADD COLUMN settings_revision_id VARCHAR(36) REFERENCES settings_revisions(id) ON DELETE RESTRICT",
    "ALTER TABLE runs ADD COLUMN model_revision_id VARCHAR(36) REFERENCES configuration_revisions(id) ON DELETE RESTRICT",
    "ALTER TABLE runs ADD COLUMN model_permission_epoch INTEGER CHECK(model_permission_epoch IS NULL OR model_permission_epoch>0)",
    "ALTER TABLE runs ADD COLUMN policy_epoch INTEGER CHECK(policy_epoch IS NULL OR policy_epoch>0)",
    "ALTER TABLE model_calls ADD COLUMN input_token_estimate INTEGER CONSTRAINT ck_model_calls_token_estimate CHECK(input_token_estimate IS NULL OR input_token_estimate>=0)",
    "ALTER TABLE model_calls ADD COLUMN counting_method VARCHAR(20) NOT NULL DEFAULT 'unmeasured'",
    "ALTER TABLE model_calls ADD COLUMN counter_id VARCHAR(100) NOT NULL DEFAULT ''",
    "ALTER TABLE model_calls ADD COLUMN output_token_limit INTEGER",
    "ALTER TABLE model_calls ADD COLUMN capacity_tokens INTEGER",
    "ALTER TABLE context_snapshots ADD COLUMN input_token_estimate INTEGER",
    "ALTER TABLE context_snapshots ADD COLUMN counting_method VARCHAR(20) NOT NULL DEFAULT 'unmeasured'",
    "ALTER TABLE context_snapshots ADD COLUMN counter_id VARCHAR(100) NOT NULL DEFAULT ''",
    "ALTER TABLE context_snapshots ADD COLUMN output_token_limit INTEGER",
)

TRIGGERS = (
    "CREATE TRIGGER runs_configuration_identity BEFORE UPDATE OF config_revision,settings_revision_id,model_revision_id,model_permission_epoch,policy_epoch ON runs WHEN new.config_revision IS NOT old.config_revision OR new.settings_revision_id IS NOT old.settings_revision_id OR new.model_revision_id IS NOT old.model_revision_id OR new.model_permission_epoch IS NOT old.model_permission_epoch OR new.policy_epoch IS NOT old.policy_epoch BEGIN SELECT RAISE(ABORT,'run configuration is immutable'); END",
    "CREATE TRIGGER configuration_head_insert BEFORE INSERT ON configurations WHEN EXISTS(SELECT 1 FROM configuration_revisions r WHERE r.config_id=new.id AND r.revision=new.revision AND (r.name!=new.name OR r.enabled!=new.enabled OR r.kind!=new.kind)) BEGIN SELECT RAISE(ABORT,'configuration head mismatch'); END",
    "CREATE TRIGGER runs_configuration_insert BEFORE INSERT ON runs WHEN NOT(new.error_code='legacy_configuration_unresolved' AND new.state IN ('succeeded','failed','cancelled','interrupted')) AND NOT EXISTS(SELECT 1 FROM settings_revisions p JOIN configuration_revisions m ON m.config_id=p.model_profile_id WHERE p.id=new.settings_revision_id AND p.agent_id=new.agent_id AND p.revision=new.config_revision AND m.id=new.model_revision_id AND m.kind='model' AND m.agent_id=new.agent_id AND new.model_permission_epoch>0 AND new.policy_epoch=p.permission_epoch) BEGIN SELECT RAISE(ABORT,'run configuration scope mismatch'); END",
    "CREATE TRIGGER configuration_head_update BEFORE UPDATE ON configurations WHEN EXISTS(SELECT 1 FROM configuration_revisions r WHERE r.config_id=new.id AND r.revision=new.revision AND (r.name!=new.name OR r.enabled!=new.enabled OR r.kind!=new.kind)) BEGIN SELECT RAISE(ABORT,'configuration head mismatch'); END",
    "CREATE TRIGGER runs_configuration_update BEFORE UPDATE ON runs WHEN NOT(new.error_code='legacy_configuration_unresolved' AND new.state IN ('succeeded','failed','cancelled','interrupted')) AND NOT EXISTS(SELECT 1 FROM settings_revisions p JOIN configuration_revisions m ON m.config_id=p.model_profile_id WHERE p.id=new.settings_revision_id AND p.agent_id=new.agent_id AND p.revision=new.config_revision AND m.id=new.model_revision_id AND m.kind='model' AND m.agent_id=new.agent_id AND new.model_permission_epoch>0 AND new.policy_epoch=p.permission_epoch) BEGIN SELECT RAISE(ABORT,'run configuration scope mismatch'); END",
    "CREATE TRIGGER configuration_revision_head BEFORE INSERT ON configuration_revisions WHEN EXISTS(SELECT 1 FROM configurations e WHERE e.id=new.config_id AND e.revision=new.revision AND (e.name!=new.name OR e.enabled!=new.enabled OR e.kind!=new.kind)) BEGIN SELECT RAISE(ABORT,'configuration revision mismatch'); END",
    "CREATE TRIGGER configuration_identity BEFORE UPDATE ON configurations WHEN new.id!=old.id OR new.agent_id!=old.agent_id OR new.kind!=old.kind OR new.permission_epoch<old.permission_epoch OR (new.enabled!=old.enabled AND new.permission_epoch<=old.permission_epoch) OR (new.deleted_at IS NOT NULL AND old.deleted_at IS NULL AND new.permission_epoch<=old.permission_epoch) OR (old.deleted_at IS NOT NULL AND new.deleted_at IS NULL) BEGIN SELECT RAISE(ABORT,'configuration identity or authorization mismatch'); END",
)
