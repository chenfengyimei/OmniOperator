"""Persistent conversation summaries and context barriers; frozen SQL, no current metadata."""

import json

from alembic import op

revision = "v2_0004"
down_revision = "v2_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    op.execute(
        "ALTER TABLE sessions ADD COLUMN content_epoch INTEGER NOT NULL DEFAULT 1 CONSTRAINT ck_sessions_content_epoch CHECK(content_epoch>0)"
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN context_floor_seq INTEGER NOT NULL DEFAULT 0 CONSTRAINT ck_sessions_context_floor CHECK(context_floor_seq>=0 AND context_floor_seq<=next_seq)"
    )
    op.execute(
        "ALTER TABLE model_calls ADD COLUMN purpose VARCHAR(20) NOT NULL DEFAULT 'turn' CONSTRAINT ck_model_calls_purpose CHECK(purpose IN ('turn','summary'))"
    )
    op.execute(
        "ALTER TABLE context_snapshots ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 1 CONSTRAINT ck_context_snapshots_ordinal CHECK(ordinal>0)"
    )
    op.execute(
        "UPDATE context_snapshots AS c SET ordinal=(SELECT COUNT(*) FROM context_snapshots x WHERE x.run_id=c.run_id AND x.rowid<=c.rowid)"
    )
    op.execute("CREATE UNIQUE INDEX uq_context_snapshots_ordinal ON context_snapshots(run_id,ordinal)")
    for statement in SNAPSHOT_COLUMNS:
        op.execute(statement)
    op.execute(
        "CREATE TEMP TABLE _v2_snapshot_messages AS SELECT id,agent_id,created_at,message_ids FROM context_snapshots"
    )
    op.execute("ALTER TABLE context_snapshots DROP COLUMN message_ids")
    op.execute(
        "CREATE TEMP TABLE _v2_old_summaries AS SELECT id,agent_id,created_at,session_id,first_seq,last_seq,revision FROM summaries"
    )
    op.execute("DROP TABLE summaries")
    op.execute("CREATE UNIQUE INDEX uq_messages_scope ON messages(id,session_id,agent_id)")
    for statement in TABLES:
        op.execute(statement)
    op.execute(
        "INSERT INTO summaries(id,agent_id,created_at,session_id,first_seq,last_seq,content,revision,session_epoch,state,source_hash,covered_count,error_code) SELECT id,agent_id,created_at,session_id,first_seq,last_seq,'',revision,1,'invalidated','',0,'legacy_unverified' FROM _v2_old_summaries"
    )
    for row in connection.exec_driver_sql("SELECT * FROM _v2_snapshot_messages").mappings():
        for position, message_id in enumerate(dict.fromkeys(json.loads(row["message_ids"]))):
            import uuid

            connection.exec_driver_sql(
                "INSERT INTO context_messages(id,agent_id,created_at,snapshot_id,message_id,position) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), row["agent_id"], row["created_at"], row["id"], message_id, position),
            )
    for statement in INDEXES:
        op.execute(statement)
    # Cross-session links cannot be expressed by the old snapshot's run-only FK.
    for table, source in (("context_messages", "messages"), ("context_summaries", "summaries")):
        column = "message_id" if source == "messages" else "summary_id"
        for event in ("INSERT", "UPDATE"):
            op.execute(
                f"CREATE TRIGGER {table}_session_{event.lower()} BEFORE {event} ON {table} WHEN NOT EXISTS(SELECT 1 FROM context_snapshots c JOIN runs r ON r.id=c.run_id JOIN {source} t ON t.id=new.{column} WHERE c.id=new.snapshot_id AND c.agent_id=new.agent_id AND t.session_id=r.session_id AND t.agent_id=new.agent_id) BEGIN SELECT RAISE(ABORT,'context source belongs to another session'); END"
            )
    for event in ("INSERT", "UPDATE"):
        op.execute(
            f"CREATE TRIGGER summaries_run_{event.lower()} BEFORE {event} ON summaries WHEN (new.run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM runs r WHERE r.id=new.run_id AND r.session_id=new.session_id AND r.agent_id=new.agent_id)) OR (new.model_call_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_calls c WHERE c.id=new.model_call_id AND c.run_id=new.run_id AND c.agent_id=new.agent_id AND c.purpose='summary')) OR (new.state='ready' AND NOT EXISTS(SELECT 1 FROM model_calls c WHERE c.id=new.model_call_id AND c.state IN ('settled','unknown'))) BEGIN SELECT RAISE(ABORT,'summary execution identity mismatch'); END"
        )
    op.execute("DROP TABLE _v2_snapshot_messages")
    op.execute("DROP TABLE _v2_old_summaries")


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")


SNAPSHOT_COLUMNS = (
    "ALTER TABLE context_snapshots ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0 CONSTRAINT ck_context_snapshots_session_epoch CHECK(session_epoch>=0)",
    "ALTER TABLE context_snapshots ADD COLUMN mode VARCHAR(20) NOT NULL DEFAULT 'legacy' CONSTRAINT ck_context_snapshots_mode CHECK(mode IN ('complete','summary','truncated','redacted','blocked','preparing','legacy'))",
    "ALTER TABLE context_snapshots ADD COLUMN omitted_count INTEGER NOT NULL DEFAULT 0 CONSTRAINT ck_context_snapshots_omitted_count CHECK(omitted_count>=0)",
    "ALTER TABLE context_snapshots ADD COLUMN reason VARCHAR(80) NOT NULL DEFAULT ''",
    "ALTER TABLE context_snapshots ADD COLUMN input_chars INTEGER NOT NULL DEFAULT 0 CONSTRAINT ck_context_snapshots_input_chars CHECK(input_chars>=0)",
)

TABLES = (
    "\nCREATE TABLE summaries (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_summaries_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsession_id VARCHAR(36) NOT NULL, \n\tfirst_seq INTEGER NOT NULL, \n\tlast_seq INTEGER NOT NULL, \n\tcontent TEXT NOT NULL, \n\trevision INTEGER NOT NULL, \n\tsession_epoch INTEGER DEFAULT '1' NOT NULL, \n\tparent_id VARCHAR(36), \n\trun_id VARCHAR(36), \n\tmodel_call_id VARCHAR(36), \n\tstate VARCHAR(20) NOT NULL, \n\tsource_hash VARCHAR(64) NOT NULL CONSTRAINT ck_summaries_summary_source_hash CHECK (length(source_hash) IN (0,64)), \n\tcovered_count INTEGER NOT NULL, \n\terror_code VARCHAR(80) DEFAULT '' NOT NULL, \n\tCONSTRAINT pk_summaries PRIMARY KEY (id), \n\tCONSTRAINT fk_summaries_session_id_sessions FOREIGN KEY(session_id, agent_id) REFERENCES sessions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_summaries_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_summaries_model_call_id_model_calls FOREIGN KEY(model_call_id, agent_id) REFERENCES model_calls (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_summaries_parent_id_summaries FOREIGN KEY(parent_id, session_id, agent_id) REFERENCES summaries (id, session_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_summaries_id UNIQUE (id, session_id, agent_id), \n\tCONSTRAINT ck_summaries_valid_interval CHECK (first_seq <= last_seq), \n\tCONSTRAINT ck_summaries_bounds CHECK (revision > 0 AND session_epoch > 0 AND covered_count >= 0 AND length(content)<=4000), \n\tCONSTRAINT ck_summaries_state CHECK (state IN ('building','ready','failed','unknown','invalidated')), \n\tCONSTRAINT ck_summaries_published CHECK (state!='ready' OR (model_call_id IS NOT NULL AND run_id IS NOT NULL AND length(content)>0 AND length(source_hash)=64)), \n\tCONSTRAINT uq_summaries_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_summaries_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE summary_messages (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_summary_messages_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsummary_id VARCHAR(36) NOT NULL, \n\tmessage_id VARCHAR(36) NOT NULL, \n\tsession_id VARCHAR(36) NOT NULL, \n\tCONSTRAINT pk_summary_messages PRIMARY KEY (id), \n\tCONSTRAINT fk_summary_messages_summary_id_summaries FOREIGN KEY(summary_id, session_id, agent_id) REFERENCES summaries (id, session_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_summary_messages_message_id_messages FOREIGN KEY(message_id, session_id, agent_id) REFERENCES messages (id, session_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_summary_messages_summary_id UNIQUE (summary_id, message_id), \n\tCONSTRAINT uq_summary_messages_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_summary_messages_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE context_messages (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_context_messages_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tmessage_id VARCHAR(36) NOT NULL, \n\tposition INTEGER NOT NULL CONSTRAINT ck_context_messages_position CHECK (position>=0), \n\tCONSTRAINT pk_context_messages PRIMARY KEY (id), \n\tCONSTRAINT fk_context_messages_snapshot_id_context_snapshots FOREIGN KEY(snapshot_id, agent_id) REFERENCES context_snapshots (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_context_messages_message_id_messages FOREIGN KEY(message_id, agent_id) REFERENCES messages (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_context_messages_snapshot_id UNIQUE (snapshot_id, message_id), \n\tCONSTRAINT uq_context_messages_snapshot_id UNIQUE (snapshot_id, position), \n\tCONSTRAINT uq_context_messages_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_context_messages_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE context_summaries (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_context_summaries_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tsummary_id VARCHAR(36) NOT NULL, \n\tCONSTRAINT pk_context_summaries PRIMARY KEY (id), \n\tCONSTRAINT fk_context_summaries_snapshot_id_context_snapshots FOREIGN KEY(snapshot_id, agent_id) REFERENCES context_snapshots (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_context_summaries_summary_id_summaries FOREIGN KEY(summary_id, agent_id) REFERENCES summaries (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_context_summaries_snapshot_id UNIQUE (snapshot_id), \n\tCONSTRAINT uq_context_summaries_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_context_summaries_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
)

INDEXES = (
    "CREATE INDEX ix_summaries_agent_id ON summaries (agent_id)",
    "CREATE INDEX ix_summaries_ready ON summaries (agent_id, session_id, session_epoch, state, last_seq)",
    "CREATE INDEX ix_summary_messages_agent_id ON summary_messages (agent_id)",
    "CREATE INDEX ix_summary_messages_source ON summary_messages (agent_id, message_id)",
    "CREATE INDEX ix_context_messages_agent_id ON context_messages (agent_id)",
    "CREATE INDEX ix_context_messages_snapshot ON context_messages (agent_id, snapshot_id)",
    "CREATE INDEX ix_context_summaries_agent_id ON context_summaries (agent_id)",
)
