"""Outbound intent, attempt, approval and recovery baseline; frozen SQL only."""

import hashlib
import json

from alembic import op

revision = "v2_0008"
down_revision = "v2_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    op.execute("CREATE TEMP TABLE _v8_outbox AS SELECT * FROM outbox")
    op.execute("CREATE TEMP TABLE _v8_reviews AS SELECT * FROM schedule_reviews")
    op.execute("DROP TABLE schedule_reviews")
    op.execute("DROP TABLE outbox")
    op.execute("CREATE UNIQUE INDEX uq_configuration_revision_owner ON configuration_revisions(id,config_id,agent_id)")
    for statement in TABLES:
        op.execute(statement)
    sequence = db.exec_driver_sql("SELECT coalesce(max(next_work_sequence),0) FROM instances").scalar_one()
    for row in db.exec_driver_sql("SELECT * FROM _v8_outbox ORDER BY created_at,id").mappings():
        sequence += 1
        body = db.exec_driver_sql("SELECT content FROM messages WHERE id=?", (row["message_id"],)).scalar_one()
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps({"legacy_id": row["id"], "state": row["state"], "body": body_hash}, sort_keys=True).encode()
        ).hexdigest()
        db.exec_driver_sql(
            "INSERT INTO outbox(id,agent_id,created_at,run_id,message_id,channel,target,body_hash,fingerprint,policy,key,queue_sequence,state,legacy,legacy_state,error_code) VALUES(?,?,?,?,?,?,?,?,?,'unverified',?,?,'unknown',1,?,'legacy_delivery_unverified')",
            (
                row["id"],
                row["agent_id"],
                row["created_at"],
                row["run_id"],
                row["message_id"],
                row["channel"],
                row["target"],
                body_hash,
                fingerprint,
                "legacy:" + row["id"],
                sequence,
                row["state"],
            ),
        )
    db.exec_driver_sql("UPDATE instances SET next_work_sequence=?", (sequence,))
    db.exec_driver_sql(
        "INSERT INTO schedule_reviews(id,agent_id,created_at,occurrence_id,model_call_id,action_id,delivery_id,delivery_revision,state,fingerprint,review_epoch) SELECT id,agent_id,created_at,occurrence_id,model_call_id,action_id,delivery_id,CASE WHEN delivery_id IS NOT NULL THEN 1 ELSE NULL END,state,fingerprint,review_epoch FROM _v8_reviews"
    )
    db.exec_driver_sql(
        "UPDATE schedule_progress SET review_epoch=review_epoch+1,reviewed_sequence=0,revision=revision+1 WHERE schedule_id IN (SELECT o.schedule_id FROM occurrences o JOIN outbox d ON d.run_id=o.run_id)"
    )
    for statement in INDEXES + TRIGGERS:
        op.execute(statement)
    op.execute("DROP TABLE _v8_reviews")
    op.execute("DROP TABLE _v8_outbox")


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")


TABLES = (
    "\nCREATE TABLE outbox (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_outbox_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\trun_id VARCHAR(36) NOT NULL, \n\tmessage_id VARCHAR(36) NOT NULL, \n\tchannel VARCHAR(40) NOT NULL, \n\ttarget VARCHAR(512) NOT NULL, \n\tconfig_id VARCHAR(36), \n\tconfig_revision_id VARCHAR(36), \n\tconfig_kind VARCHAR(20) DEFAULT 'channel' NOT NULL, \n\tadapter_id VARCHAR(100), \n\taccount_key VARCHAR(200), \n\tresource_key VARCHAR(64), \n\tsession_epoch INTEGER, \n\tbody_hash VARCHAR(64) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tpolicy VARCHAR(50) NOT NULL, \n\t\"key\" VARCHAR(200) NOT NULL, \n\tparent_id VARCHAR(36), \n\tqueue_sequence INTEGER NOT NULL, \n\tstate VARCHAR(24) NOT NULL, \n\trevision INTEGER DEFAULT '1' NOT NULL, \n\tattempt_seq INTEGER DEFAULT '0' NOT NULL, \n\toutcome_revision INTEGER DEFAULT '1' NOT NULL, \n\tlatest_attempt_id VARCHAR(36), \n\tnext_attempt_at VARCHAR(40), \n\tapproval_until VARCHAR(40), \n\terror_code VARCHAR(80) DEFAULT '' NOT NULL, \n\treview_epoch INTEGER DEFAULT '1' NOT NULL, \n\treviewed_epoch INTEGER DEFAULT '0' NOT NULL, \n\tlast_review_id VARCHAR(36), \n\tresolution VARCHAR(24), \n\tarchived_at VARCHAR(40), \n\tlegacy BOOLEAN DEFAULT '0' NOT NULL, \n\tlegacy_state VARCHAR(20), \n\tCONSTRAINT pk_outbox PRIMARY KEY (id), \n\tCONSTRAINT fk_outbox_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_outbox_message_id_messages FOREIGN KEY(message_id, agent_id) REFERENCES messages (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_outbox_parent_id_outbox FOREIGN KEY(parent_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_outbox_config_id_configurations FOREIGN KEY(config_id, agent_id, config_kind) REFERENCES configurations (id, agent_id, kind) ON DELETE RESTRICT, \n\tCONSTRAINT fk_outbox_config_revision_id_configuration_revisions FOREIGN KEY(config_revision_id, config_id, agent_id) REFERENCES configuration_revisions (id, config_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_outbox_latest_attempt_id_delivery_attempts FOREIGN KEY(latest_attempt_id, id, agent_id) REFERENCES delivery_attempts (id, delivery_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_outbox_last_review_id_delivery_reviews FOREIGN KEY(last_review_id, id, agent_id) REFERENCES delivery_reviews (id, delivery_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_outbox_agent_id UNIQUE (agent_id, \"key\"), \n\tCONSTRAINT ck_outbox_state CHECK (state IN ('awaiting_approval','approved','claimed','sending','retry_wait','sent','unknown','cancelled','rejected','blocked')), \n\tCONSTRAINT ck_outbox_bounds CHECK (revision>0 AND outcome_revision>0 AND queue_sequence>0 AND attempt_seq BETWEEN 0 AND 3 AND review_epoch>0 AND reviewed_epoch BETWEEN 0 AND review_epoch AND length(body_hash)=64 AND length(fingerprint)=64 AND config_kind='channel' AND length(trim(target)) BETWEEN 1 AND 512 AND legacy IN (0,1)), \n\tCONSTRAINT ck_outbox_authority CHECK (legacy=1 OR (config_id IS NOT NULL AND config_revision_id IS NOT NULL AND adapter_id IS NOT NULL AND account_key IS NOT NULL AND resource_key IS NOT NULL AND session_epoch>0 AND policy='delivery-policy-v1')), \n\tCONSTRAINT ck_outbox_resolution CHECK (resolution IS NULL OR resolution IN ('confirmed_sent','confirmed_not_sent','allow_future')), \n\tCONSTRAINT uq_outbox_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_outbox_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_outbox_queue_sequence UNIQUE (queue_sequence)\n)\n\n",
    "\nCREATE TABLE delivery_approvals (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_delivery_approvals_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tdelivery_id VARCHAR(36) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tapproved_at VARCHAR(40), \n\trevoked_at VARCHAR(40), \n\tCONSTRAINT pk_delivery_approvals PRIMARY KEY (id), \n\tCONSTRAINT fk_delivery_approvals_delivery_id_outbox FOREIGN KEY(delivery_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_delivery_approvals_state CHECK (revision>0 AND length(fingerprint)=64 AND state IN ('pending','approved','denied','revoked')), \n\tCONSTRAINT uq_delivery_approvals_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_delivery_approvals_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_delivery_approvals_delivery_id UNIQUE (delivery_id)\n)\n\n",
    "\nCREATE TABLE delivery_attempts (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_delivery_attempts_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tdelivery_id VARCHAR(36) NOT NULL, \n\tseq INTEGER NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\ttoken VARCHAR(36) NOT NULL, \n\tfence INTEGER NOT NULL, \n\tstarted_at VARCHAR(40), \n\tended_at VARCHAR(40), \n\terror_code VARCHAR(80) DEFAULT '' NOT NULL, \n\tCONSTRAINT pk_delivery_attempts PRIMARY KEY (id), \n\tCONSTRAINT fk_delivery_attempts_delivery_id_outbox FOREIGN KEY(delivery_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_delivery_attempts_delivery_id UNIQUE (delivery_id, seq), \n\tCONSTRAINT uq_delivery_attempts_id UNIQUE (id, delivery_id, agent_id), \n\tCONSTRAINT ck_delivery_attempts_state CHECK (seq BETWEEN 1 AND 3 AND fence>0 AND length(boot_id)=36 AND length(token)=36 AND state IN ('claimed','sending','sent','not_sent','unknown','cancelled')), \n\tCONSTRAINT uq_delivery_attempts_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_delivery_attempts_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE delivery_results (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_delivery_results_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tdelivery_id VARCHAR(36) NOT NULL, \n\tattempt_id VARCHAR(36) NOT NULL, \n\tobservation_key VARCHAR(100) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\treceipt_id VARCHAR(200) NOT NULL, \n\terror_code VARCHAR(80) NOT NULL, \n\tretryable BOOLEAN NOT NULL, \n\tlate BOOLEAN NOT NULL, \n\tCONSTRAINT pk_delivery_results PRIMARY KEY (id), \n\tCONSTRAINT fk_delivery_results_attempt_id_delivery_attempts FOREIGN KEY(attempt_id, delivery_id, agent_id) REFERENCES delivery_attempts (id, delivery_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_delivery_results_delivery_id_outbox FOREIGN KEY(delivery_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_delivery_results_attempt_id UNIQUE (attempt_id, observation_key), \n\tCONSTRAINT ck_delivery_results_result CHECK (status IN ('sent','not_sent','unknown') AND length(fingerprint)=64 AND (status!='sent' OR length(receipt_id)>0) AND (status='sent' OR receipt_id='') AND (retryable=0 OR status='not_sent') AND retryable IN (0,1) AND late IN (0,1)), \n\tCONSTRAINT uq_delivery_results_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_delivery_results_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE delivery_reviews (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_delivery_reviews_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\tdelivery_id VARCHAR(36) NOT NULL, \n\tattempt_id VARCHAR(36), \n\treview_epoch INTEGER NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tdecision VARCHAR(24) NOT NULL, \n\tevidence VARCHAR(2000) NOT NULL, \n\tCONSTRAINT pk_delivery_reviews PRIMARY KEY (id), \n\tCONSTRAINT fk_delivery_reviews_delivery_id_outbox FOREIGN KEY(delivery_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_delivery_reviews_attempt_id_delivery_attempts FOREIGN KEY(attempt_id, delivery_id, agent_id) REFERENCES delivery_attempts (id, delivery_id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_delivery_reviews_id UNIQUE (id, delivery_id, agent_id), \n\tCONSTRAINT ck_delivery_reviews_review CHECK (review_epoch>0 AND length(fingerprint)=64 AND length(trim(evidence)) BETWEEN 1 AND 2000 AND decision IN ('confirmed_sent','confirmed_not_sent','allow_future')), \n\tCONSTRAINT uq_delivery_reviews_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_delivery_reviews_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE dispatch_resources (\n\tresource_key VARCHAR(64) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tattempt_id VARCHAR(36) NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\ttoken VARCHAR(36) NOT NULL, \n\tfence INTEGER NOT NULL, \n\texpires_at VARCHAR(40) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tCONSTRAINT pk_dispatch_resources PRIMARY KEY (resource_key), \n\tCONSTRAINT fk_dispatch_resources_attempt_id_delivery_attempts FOREIGN KEY(attempt_id, agent_id) REFERENCES delivery_attempts (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_dispatch_resources_bounds CHECK (fence>0 AND length(resource_key)=64 AND length(boot_id)=36 AND length(token)=36 AND state IN ('held','released')), \n\tCONSTRAINT fk_dispatch_resources_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE schedule_reviews (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_schedule_reviews_uuid_length CHECK (length(id) = 36), \n\tagent_id VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\toccurrence_id VARCHAR(36) NOT NULL, \n\tmodel_call_id VARCHAR(36), \n\taction_id VARCHAR(36), \n\tdelivery_id VARCHAR(36), \n\tdelivery_revision INTEGER, \n\tstate VARCHAR(20) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\treview_epoch INTEGER NOT NULL, \n\tCONSTRAINT pk_schedule_reviews PRIMARY KEY (id), \n\tCONSTRAINT fk_schedule_reviews_occurrence_id_occurrences FOREIGN KEY(occurrence_id, agent_id) REFERENCES occurrences (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_reviews_model_call_id_model_calls FOREIGN KEY(model_call_id, agent_id) REFERENCES model_calls (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_reviews_action_id_actions FOREIGN KEY(action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_reviews_delivery_id_outbox FOREIGN KEY(delivery_id, agent_id) REFERENCES outbox (id, agent_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_schedule_reviews_intent CHECK ((model_call_id IS NOT NULL)+(action_id IS NOT NULL)+(delivery_id IS NOT NULL)=1 AND length(fingerprint)=64 AND review_epoch>0 AND state IN ('reserved','unknown','executing','sending')), \n\tCONSTRAINT uq_schedule_reviews_occurrence_id UNIQUE (occurrence_id, model_call_id, state, review_epoch), \n\tCONSTRAINT uq_schedule_reviews_occurrence_id UNIQUE (occurrence_id, action_id, state, review_epoch), \n\tCONSTRAINT uq_schedule_reviews_occurrence_id UNIQUE (occurrence_id, delivery_id, state, review_epoch, delivery_revision), \n\tCONSTRAINT uq_schedule_reviews_id UNIQUE (id, agent_id), \n\tCONSTRAINT fk_schedule_reviews_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT\n)\n\n",
)
INDEXES = (
    "CREATE INDEX ix_delivery_account ON outbox (resource_key, queue_sequence)",
    "CREATE INDEX ix_delivery_cursor ON outbox (agent_id, queue_sequence)",
    "CREATE INDEX ix_delivery_pending ON outbox (state, queue_sequence)",
    "CREATE INDEX ix_outbox_agent_id ON outbox (agent_id)",
    "CREATE INDEX ix_schedule_delivery_state ON outbox (run_id, state)",
    "CREATE INDEX ix_delivery_approvals_agent_id ON delivery_approvals (agent_id)",
    "CREATE INDEX ix_delivery_attempt_cursor ON delivery_attempts (agent_id, delivery_id, seq)",
    "CREATE INDEX ix_delivery_attempts_agent_id ON delivery_attempts (agent_id)",
    "CREATE INDEX ix_delivery_results_agent_id ON delivery_results (agent_id)",
    "CREATE INDEX ix_delivery_reviews_agent_id ON delivery_reviews (agent_id)",
    "CREATE INDEX ix_dispatch_resources_agent_id ON dispatch_resources (agent_id)",
    "CREATE INDEX ix_schedule_reviews_agent_id ON schedule_reviews (agent_id)",
)
TRIGGERS = (
    "CREATE TRIGGER schedule_review_update BEFORE UPDATE ON schedule_reviews BEGIN SELECT RAISE(ABORT,'reviewed intent identity is immutable'); END",
    "CREATE TRIGGER schedule_review_delete BEFORE DELETE ON schedule_reviews BEGIN SELECT RAISE(ABORT,'review history must remain'); END",
    "CREATE TRIGGER delivery_identity BEFORE UPDATE ON outbox WHEN new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.run_id IS NOT old.run_id OR new.message_id IS NOT old.message_id OR new.channel IS NOT old.channel OR new.target IS NOT old.target OR new.config_id IS NOT old.config_id OR new.config_revision_id IS NOT old.config_revision_id OR new.config_kind IS NOT old.config_kind OR new.adapter_id IS NOT old.adapter_id OR new.account_key IS NOT old.account_key OR new.resource_key IS NOT old.resource_key OR new.session_epoch IS NOT old.session_epoch OR new.body_hash IS NOT old.body_hash OR new.fingerprint IS NOT old.fingerprint OR new.policy IS NOT old.policy OR new.key IS NOT old.key OR new.parent_id IS NOT old.parent_id OR new.queue_sequence IS NOT old.queue_sequence OR new.legacy IS NOT old.legacy OR new.legacy_state IS NOT old.legacy_state BEGIN SELECT RAISE(ABORT,'delivery intent identity is immutable'); END",
    "CREATE TRIGGER delivery_progress BEFORE UPDATE ON outbox WHEN new.revision<old.revision OR new.outcome_revision<old.outcome_revision OR new.attempt_seq<old.attempt_seq OR new.review_epoch<old.review_epoch OR ((old.state IN ('sent','unknown','cancelled','rejected') AND new.state!=old.state) OR (old.state='blocked' AND new.state NOT IN ('blocked','cancelled'))) BEGIN SELECT RAISE(ABORT,'delivery progress cannot rewind or replay a terminal intent'); END",
    "CREATE TRIGGER delivery_attempt_identity BEFORE UPDATE ON delivery_attempts WHEN new.id!=old.id OR new.agent_id!=old.agent_id OR new.delivery_id!=old.delivery_id OR new.seq!=old.seq OR new.boot_id!=old.boot_id OR new.token!=old.token OR new.fence!=old.fence OR (old.state IN ('sent','not_sent','unknown','cancelled') AND new.state!=old.state) BEGIN SELECT RAISE(ABORT,'delivery attempt identity and terminal outcome are immutable'); END",
    "CREATE TRIGGER delivery_approval_identity BEFORE UPDATE ON delivery_approvals WHEN new.id!=old.id OR new.delivery_id!=old.delivery_id OR new.agent_id!=old.agent_id OR new.fingerprint!=old.fingerprint OR new.revision<old.revision OR (old.state IN ('denied','revoked') AND new.state!=old.state) OR (old.approved_at IS NOT NULL AND new.approved_at IS NOT old.approved_at) BEGIN SELECT RAISE(ABORT,'delivery approval cannot change intent or revive authorization'); END",
    "CREATE TRIGGER dispatch_fence BEFORE UPDATE ON dispatch_resources WHEN new.fence<old.fence OR ((new.token!=old.token OR new.boot_id!=old.boot_id OR new.attempt_id!=old.attempt_id) AND new.fence<=old.fence) BEGIN SELECT RAISE(ABORT,'dispatch ownership requires a new fence'); END",
    "CREATE TRIGGER delivery_results_update BEFORE UPDATE ON delivery_results BEGIN SELECT RAISE(ABORT,'delivery observations and reviews are immutable'); END",
    "CREATE TRIGGER delivery_results_delete BEFORE DELETE ON delivery_results BEGIN SELECT RAISE(ABORT,'delivery observations and reviews are immutable'); END",
    "CREATE TRIGGER delivery_reviews_update BEFORE UPDATE ON delivery_reviews BEGIN SELECT RAISE(ABORT,'delivery observations and reviews are immutable'); END",
    "CREATE TRIGGER delivery_reviews_delete BEFORE DELETE ON delivery_reviews BEGIN SELECT RAISE(ABORT,'delivery observations and reviews are immutable'); END",
    "CREATE TRIGGER delivery_source_identity BEFORE INSERT ON outbox WHEN new.legacy=0 AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.id=new.message_id AND m.agent_id=new.agent_id AND m.run_id=new.run_id AND m.role='assistant' AND m.deleted_at IS NULL) BEGIN SELECT RAISE(ABORT,'delivery must reference its own assistant output'); END",
)
