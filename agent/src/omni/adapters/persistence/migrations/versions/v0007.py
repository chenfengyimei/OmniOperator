"""Immutable schedules, due identities and fenced work claims; frozen migration."""

import datetime as dt
import uuid

from alembic import op

revision = "v2_0007"
down_revision = "v2_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    for name in ("schedules", "occurrences", "leases", "work_items"):
        op.execute(f"CREATE TEMP TABLE _v7_{name} AS SELECT * FROM {name}")
    for name in ("occurrences", "schedules", "leases", "work_items"):
        op.execute(f"DROP TABLE {name}")
    op.execute(
        "ALTER TABLE instances ADD COLUMN next_work_sequence INTEGER NOT NULL DEFAULT 0 CONSTRAINT ck_instances_work_sequence CHECK(next_work_sequence>=0)"
    )
    for statement in TABLES:
        op.execute(statement)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    for row in db.exec_driver_sql("SELECT * FROM _v7_schedules ORDER BY id").mappings():
        revision_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
        db.exec_driver_sql(
            "INSERT INTO sessions(id,agent_id,created_at,title,revision,next_seq) VALUES(?,?,?,'Unverified legacy schedule',1,0)",
            (session_id, row["agent_id"], now),
        )
        db.exec_driver_sql(
            "INSERT INTO schedules(id,agent_id,created_at,name,revision,enabled) VALUES(?,?,?,?,?,0)",
            (row["id"], row["agent_id"], row["created_at"], row["name"], row["revision"]),
        )
        db.exec_driver_sql(
            "INSERT INTO schedule_revisions(id,agent_id,created_at,schedule_id,revision,name,enabled,kind,timezone,tzdb_version,expression,session_id,prompt,misfire) VALUES(?,?,?,?,?,?,0,'cron',?,'unverified',?,?,?,'latest')",
            (
                revision_id,
                row["agent_id"],
                row["created_at"],
                row["id"],
                row["revision"],
                row["name"],
                row["timezone"],
                row["expression"],
                session_id,
                row["prompt"],
            ),
        )
        sequence = 0
        for occurrence in db.exec_driver_sql(
            "SELECT * FROM _v7_occurrences WHERE schedule_id=? ORDER BY scheduled_at,id", (row["id"],)
        ).mappings():
            sequence += 1
            db.exec_driver_sql(
                "INSERT INTO occurrences(id,agent_id,created_at,schedule_id,revision_id,sequence,scheduled_at,local_time,state,error_code,updated_at,run_id) VALUES(?,?,?,?,?,?,?,?,'legacy','legacy_schedule_unverified',?,?)",
                (
                    occurrence["id"],
                    row["agent_id"],
                    occurrence["created_at"],
                    row["id"],
                    revision_id,
                    sequence,
                    occurrence["scheduled_at"],
                    occurrence["scheduled_at"],
                    now,
                    occurrence["run_id"],
                ),
            )
        db.exec_driver_sql(
            "INSERT INTO schedule_progress(schedule_id,agent_id,revision_id,sequence,reviewed_sequence,revision,error_code,updated_at) VALUES(?,?,?,?,0,1,'legacy_schedule_unverified',?)",
            (row["id"], row["agent_id"], revision_id, sequence, now),
        )
    for row in db.exec_driver_sql("SELECT * FROM _v7_leases").mappings():
        token = row["token"] if len(row["token"]) == 36 else str(uuid.uuid4())
        boot = row["boot_id"] if len(row["boot_id"]) == 36 else str(uuid.uuid4())
        db.exec_driver_sql(
            "INSERT INTO leases(agent_id,resource,token,boot_id,fence,expires_at,state) VALUES(?,?,?,?,1,?,'released')",
            (row["agent_id"], row["resource"], token, boot, now),
        )
    sequence = 0
    for row in db.exec_driver_sql("SELECT * FROM _v7_work_items ORDER BY created_at,id").mappings():
        sequence += 1
        state = "unknown" if row["kind"] == "turn" and row["state"] == "running" else row["state"]
        db.exec_driver_sql(
            "INSERT INTO work_items(id,agent_id,created_at,run_id,kind,state,payload,claimed_by,attempt,queue_sequence) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["agent_id"],
                row["created_at"],
                row["run_id"],
                row["kind"],
                state,
                row["payload"],
                row["claimed_by"],
                row["attempt"],
                sequence,
            ),
        )
    db.exec_driver_sql("UPDATE instances SET next_work_sequence=?", (sequence,))
    for statement in INDEXES + TRIGGERS:
        op.execute(statement)
    for name in ("schedules", "occurrences", "leases", "work_items"):
        op.execute(f"DROP TABLE _v7_{name}")


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")


TABLES = (
    "\nCREATE TABLE schedules (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_schedules_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tdeleted_at VARCHAR(40), \n\tCONSTRAINT pk_schedules PRIMARY KEY (id), \n\tCONSTRAINT ck_schedules_bounds CHECK (revision>0 AND enabled IN (0,1) AND length(trim(name)) BETWEEN 1 AND 100), \n\tCONSTRAINT fk_schedules_id_schedule_revisions FOREIGN KEY(id, revision, agent_id) REFERENCES schedule_revisions (schedule_id, revision, agent_id) DEFERRABLE INITIALLY DEFERRED, \n\tCONSTRAINT uq_schedules_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_schedules_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE schedule_revisions (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_schedule_revisions_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tschedule_id VARCHAR(36) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\ttimezone VARCHAR(100) NOT NULL, \n\ttzdb_version VARCHAR(100) NOT NULL, \n\texpression VARCHAR(100) NOT NULL, \n\tinterval_seconds INTEGER, \n\tstart_at VARCHAR(40), \n\tsession_id VARCHAR(36) NOT NULL, \n\tprompt TEXT NOT NULL, \n\tmisfire VARCHAR(20) NOT NULL, \n\tCONSTRAINT pk_schedule_revisions PRIMARY KEY (id), \n\tCONSTRAINT fk_schedule_revisions_schedule_id_schedules FOREIGN KEY(schedule_id, agent_id) REFERENCES schedules (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_revisions_session_id_sessions FOREIGN KEY(session_id, agent_id) REFERENCES sessions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_schedule_revisions_schedule_id UNIQUE (schedule_id, revision, agent_id), \n\tCONSTRAINT uq_schedule_revisions_id UNIQUE (id, schedule_id, agent_id), \n\tCONSTRAINT ck_schedule_revisions_bounds CHECK (revision>0 AND length(prompt) BETWEEN 1 AND 20000 AND misfire='latest' AND length(tzdb_version)>0), \n\tCONSTRAINT ck_schedule_revisions_kind CHECK ((kind='cron' AND interval_seconds IS NULL AND start_at IS NULL) OR (kind='interval' AND expression='' AND interval_seconds BETWEEN 60 AND 31536000 AND start_at IS NOT NULL) OR (kind='once' AND expression='' AND interval_seconds IS NULL AND start_at IS NOT NULL)), \n\tCONSTRAINT uq_schedule_revisions_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_schedule_revisions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE schedule_progress (\n\tschedule_id VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\trevision_id VARCHAR(36) NOT NULL, \n\tnext_at VARCHAR(40), \n\tlast_scheduled_at VARCHAR(40), \n\tsequence INTEGER NOT NULL, \n\treviewed_sequence INTEGER NOT NULL, \n\treview_epoch INTEGER DEFAULT '1' NOT NULL, \n\trevision INTEGER NOT NULL, \n\terror_code VARCHAR(100) NOT NULL, \n\tupdated_at VARCHAR(40) NOT NULL, \n\tCONSTRAINT pk_schedule_progress PRIMARY KEY (schedule_id), \n\tCONSTRAINT fk_schedule_progress_schedule_id_schedules FOREIGN KEY(schedule_id, agent_id) REFERENCES schedules (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_progress_revision_id_schedule_revisions FOREIGN KEY(revision_id, schedule_id, agent_id) REFERENCES schedule_revisions (id, schedule_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_schedule_progress_bounds CHECK (revision>0 AND sequence>=0 AND reviewed_sequence>=0 AND reviewed_sequence<=sequence AND review_epoch>0), \n\tCONSTRAINT fk_schedule_progress_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE occurrences (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_occurrences_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tschedule_id VARCHAR(36) NOT NULL, \n\trevision_id VARCHAR(36) NOT NULL, \n\tsequence INTEGER NOT NULL, \n\tscheduled_at VARCHAR(40) NOT NULL, \n\tlocal_time VARCHAR(48) NOT NULL, \n\tcoalesced_from VARCHAR(40), \n\tstate VARCHAR(20) NOT NULL, \n\terror_code VARCHAR(100) NOT NULL, \n\tupdated_at VARCHAR(40) NOT NULL, \n\trun_id VARCHAR(36), \n\tCONSTRAINT pk_occurrences PRIMARY KEY (id), \n\tCONSTRAINT fk_occurrences_schedule_id_schedules FOREIGN KEY(schedule_id, agent_id) REFERENCES schedules (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_occurrences_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_occurrences_revision_id_schedule_revisions FOREIGN KEY(revision_id, schedule_id, agent_id) REFERENCES schedule_revisions (id, schedule_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_occurrences_schedule_id UNIQUE (schedule_id, scheduled_at), \n\tCONSTRAINT uq_occurrences_schedule_id UNIQUE (schedule_id, sequence), \n\tCONSTRAINT ck_occurrences_state CHECK (sequence>0 AND state IN ('pending','enqueued','blocked','cancelled','legacy') AND (state!='enqueued' OR run_id IS NOT NULL)), \n\tCONSTRAINT uq_occurrences_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_occurrences_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_occurrences_run_id UNIQUE (run_id)\n)\n\n",
    "\nCREATE TABLE leases (\n\tagent_id VARCHAR(36) NOT NULL, \n\tresource VARCHAR(200) NOT NULL, \n\ttoken VARCHAR(36) NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\tfence INTEGER NOT NULL, \n\texpires_at VARCHAR(40) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\trun_id VARCHAR(36), \n\tCONSTRAINT pk_leases PRIMARY KEY (agent_id, resource), \n\tCONSTRAINT fk_leases_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_leases_bounds CHECK (fence>0 AND length(token)=36 AND length(boot_id)=36 AND length(resource) BETWEEN 1 AND 200 AND state IN ('held','released')), \n\tCONSTRAINT fk_leases_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE work_items (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_work_items_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\trun_id VARCHAR(36), \n\tkind VARCHAR(40) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tpayload JSON NOT NULL, \n\tclaimed_by VARCHAR(36), \n\tclaim_token VARCHAR(36), \n\tlease_fence INTEGER, \n\tqueue_sequence INTEGER NOT NULL, \n\tattempt INTEGER NOT NULL, \n\tCONSTRAINT pk_work_items PRIMARY KEY (id), \n\tCONSTRAINT ck_work_items_queue CHECK (queue_sequence>0 AND attempt>=0 AND (kind!='turn' OR run_id IS NOT NULL)), \n\tCONSTRAINT ck_work_items_claim CHECK (kind!='turn' OR state NOT IN ('claimed','running') OR (claimed_by IS NOT NULL AND claim_token IS NOT NULL AND length(claim_token)=36 AND lease_fence IS NOT NULL AND lease_fence>0)), \n\tCONSTRAINT fk_work_items_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_work_items_run_id UNIQUE (run_id, kind), \n\tCONSTRAINT ck_work_items_state CHECK (state IN ('pending','claimed','running','done','failed','cancelled','unknown')), \n\tCONSTRAINT uq_work_items_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_work_items_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_work_items_queue_sequence UNIQUE (queue_sequence)\n)\n\n",
    "\nCREATE TABLE schedule_reviews (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_schedule_reviews_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\toccurrence_id VARCHAR(36) NOT NULL, \n\tmodel_call_id VARCHAR(36), \n\taction_id VARCHAR(36), \n\tdelivery_id VARCHAR(36), \n\tstate VARCHAR(20) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\treview_epoch INTEGER NOT NULL, \n\tCONSTRAINT pk_schedule_reviews PRIMARY KEY (id), \n\tCONSTRAINT fk_schedule_reviews_occurrence_id_occurrences FOREIGN KEY(occurrence_id, agent_id) REFERENCES occurrences (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_reviews_model_call_id_model_calls FOREIGN KEY(model_call_id, agent_id) REFERENCES model_calls (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_reviews_action_id_actions FOREIGN KEY(action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_reviews_delivery_id_outbox FOREIGN KEY(delivery_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_schedule_reviews_intent CHECK ((model_call_id IS NOT NULL)+(action_id IS NOT NULL)+(delivery_id IS NOT NULL)=1 AND length(fingerprint)=64 AND review_epoch>0 AND state IN ('reserved','unknown','executing','sending')), \n\tCONSTRAINT uq_schedule_reviews_occurrence_id UNIQUE (occurrence_id, model_call_id, state, review_epoch), \n\tCONSTRAINT uq_schedule_reviews_occurrence_id UNIQUE (occurrence_id, action_id, state, review_epoch), \n\tCONSTRAINT uq_schedule_reviews_occurrence_id UNIQUE (occurrence_id, delivery_id, state, review_epoch), \n\tCONSTRAINT uq_schedule_reviews_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_schedule_reviews_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
)
INDEXES = (
    "CREATE INDEX ix_lease_owner ON leases (boot_id, state, expires_at)",
    "CREATE INDEX ix_occurrence_cursor ON occurrences (agent_id, schedule_id, sequence)",
    "CREATE INDEX ix_occurrences_agent_id ON occurrences (agent_id)",
    "CREATE INDEX ix_run_session_state ON runs (session_id, state)",
    "CREATE INDEX ix_schedule_action_state ON actions (run_id, state)",
    "CREATE INDEX ix_schedule_call_state ON model_calls (run_id, state)",
    "CREATE INDEX ix_schedule_cursor ON schedules (agent_id, id)",
    "CREATE INDEX ix_schedule_delivery_state ON outbox (run_id, state)",
    "CREATE INDEX ix_schedule_due ON schedule_progress (next_at, schedule_id)",
    "CREATE INDEX ix_schedule_progress_agent_id ON schedule_progress (agent_id)",
    "CREATE INDEX ix_schedule_reviews_agent_id ON schedule_reviews (agent_id)",
    "CREATE INDEX ix_schedule_revisions_agent_id ON schedule_revisions (agent_id)",
    "CREATE INDEX ix_schedules_agent_id ON schedules (agent_id)",
    "CREATE INDEX ix_work_items_agent_id ON work_items (agent_id)",
    "CREATE INDEX ix_work_pending ON work_items (state, created_at)",
)
TRIGGERS = (
    "CREATE TRIGGER schedule_revision_update BEFORE UPDATE ON schedule_revisions BEGIN SELECT RAISE(ABORT,'schedule revisions are immutable'); END",
    "CREATE TRIGGER schedule_revision_delete BEFORE DELETE ON schedule_revisions BEGIN SELECT RAISE(ABORT,'schedule revisions must remain for provenance'); END",
    "CREATE TRIGGER schedule_identity BEFORE UPDATE ON schedules WHEN new.id!=old.id OR new.agent_id!=old.agent_id OR (old.deleted_at IS NOT NULL AND new.deleted_at IS NULL) BEGIN SELECT RAISE(ABORT,'schedule identity is immutable'); END",
    "CREATE TRIGGER occurrence_identity BEFORE UPDATE ON occurrences WHEN new.id!=old.id OR new.agent_id!=old.agent_id OR new.schedule_id!=old.schedule_id OR new.revision_id!=old.revision_id OR new.sequence!=old.sequence OR new.scheduled_at!=old.scheduled_at OR new.local_time!=old.local_time OR new.coalesced_from IS NOT old.coalesced_from OR (old.run_id IS NOT NULL AND new.run_id IS NOT old.run_id) BEGIN SELECT RAISE(ABORT,'due occurrence identity is immutable'); END",
    "CREATE TRIGGER schedule_progress_head_insert BEFORE INSERT ON schedule_progress WHEN NOT EXISTS(SELECT 1 FROM schedules h JOIN schedule_revisions r ON r.schedule_id=h.id AND r.revision=h.revision WHERE h.id=new.schedule_id AND h.agent_id=new.agent_id AND r.id=new.revision_id) BEGIN SELECT RAISE(ABORT,'schedule progress does not reference the current definition'); END",
    "CREATE TRIGGER schedule_progress_head_update BEFORE UPDATE ON schedule_progress WHEN NOT EXISTS(SELECT 1 FROM schedules h JOIN schedule_revisions r ON r.schedule_id=h.id AND r.revision=h.revision WHERE h.id=new.schedule_id AND h.agent_id=new.agent_id AND r.id=new.revision_id) BEGIN SELECT RAISE(ABORT,'schedule progress does not reference the current definition'); END",
    "CREATE TRIGGER schedule_head_revision BEFORE UPDATE ON schedules WHEN (new.name!=old.name OR new.enabled!=old.enabled OR new.revision!=old.revision) AND new.revision!=old.revision+1 BEGIN SELECT RAISE(ABORT,'schedule changes require the next revision'); END",
    "CREATE TRIGGER schedule_revision_head AFTER INSERT ON schedule_revisions WHEN EXISTS(SELECT 1 FROM schedules h WHERE h.id=new.schedule_id AND h.revision=new.revision AND (h.name!=new.name OR h.enabled!=new.enabled)) BEGIN SELECT RAISE(ABORT,'schedule head differs from its revision'); END",
    "CREATE TRIGGER work_queue_identity BEFORE UPDATE ON work_items WHEN new.queue_sequence!=old.queue_sequence OR new.agent_id!=old.agent_id OR new.run_id IS NOT old.run_id OR new.kind!=old.kind BEGIN SELECT RAISE(ABORT,'work identity and queue sequence are immutable'); END",
    "CREATE TRIGGER lease_fence_monotone BEFORE UPDATE ON leases WHEN new.fence<old.fence OR ((new.token!=old.token OR new.boot_id!=old.boot_id OR new.run_id IS NOT old.run_id) AND new.fence<=old.fence) BEGIN SELECT RAISE(ABORT,'lease ownership requires a new fence'); END",
    "CREATE TRIGGER schedule_review_update BEFORE UPDATE ON schedule_reviews BEGIN SELECT RAISE(ABORT,'reviewed intent identity is immutable'); END",
    "CREATE TRIGGER schedule_review_delete BEFORE DELETE ON schedule_reviews BEGIN SELECT RAISE(ABORT,'review history must remain'); END",
    "CREATE TRIGGER schedule_progress_monotone BEFORE UPDATE ON schedule_progress WHEN new.sequence<old.sequence OR new.review_epoch<old.review_epoch OR (old.last_scheduled_at IS NOT NULL AND (new.last_scheduled_at IS NULL OR new.last_scheduled_at<old.last_scheduled_at)) BEGIN SELECT RAISE(ABORT,'schedule high water and review epoch cannot decrease'); END",
)
