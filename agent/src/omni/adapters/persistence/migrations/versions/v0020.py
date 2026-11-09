"""Immutable owner observations for unknown writes, erasable evidence and source-invalidation fences."""

from alembic import op

revision = "v2_0020"
down_revision = "v2_0019"
branch_labels = None
depends_on = None

SQL = (
    "CREATE UNIQUE INDEX uq_actions_run_identity ON actions (id, run_id, agent_id)",
    """CREATE TABLE action_review_heads (
              sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
              id VARCHAR(36) NOT NULL CONSTRAINT ck_action_review_heads_uuid_length CHECK (length(id)=36), 
              agent_id VARCHAR(36) NOT NULL, 
              created_at VARCHAR(40) NOT NULL, 
              action_id VARCHAR(36) NOT NULL, 
              run_id VARCHAR(36) NOT NULL, 
              revision INTEGER NOT NULL, 
              history_epoch VARCHAR(36) NOT NULL, 
              policy VARCHAR(40) NOT NULL, 
              fingerprint VARCHAR(64) NOT NULL, 
              arguments_sha256 VARCHAR(64) NOT NULL, 
              result_sha256 VARCHAR(64) NOT NULL, 
              session_epoch INTEGER NOT NULL, 
              state VARCHAR(20) NOT NULL, 
              resolution VARCHAR(20), 
              last_review_id VARCHAR(36), 
              updated_at VARCHAR(40) NOT NULL, 
              error_code VARCHAR(80) DEFAULT '' NOT NULL, 
              CONSTRAINT uq_action_review_heads_id UNIQUE (id), 
              CONSTRAINT uq_action_review_heads_id UNIQUE (id, agent_id), 
              CONSTRAINT uq_action_review_heads_action_id UNIQUE (action_id), 
              CONSTRAINT uq_action_review_heads_id UNIQUE (id, action_id, run_id, agent_id), 
              CONSTRAINT fk_action_review_heads_action_id_actions FOREIGN KEY(action_id, run_id, agent_id) REFERENCES actions (id, run_id, agent_id) ON DELETE RESTRICT, 
              CONSTRAINT fk_action_review_heads_last_review_id_action_reviews FOREIGN KEY(last_review_id, id, agent_id) REFERENCES action_reviews (id, head_id, agent_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED, 
              CONSTRAINT ck_action_review_heads_bounds CHECK (sequence>0 AND typeof(revision)='integer' AND revision BETWEEN 0 AND 9223372036854775807 AND typeof(session_epoch)='integer' AND session_epoch BETWEEN 1 AND 9223372036854775807), 
              CONSTRAINT ck_action_review_heads_identity CHECK (policy='action-review-1' AND length(history_epoch)=36 AND length(error_code)<=80 AND length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*' AND length(arguments_sha256)=64 AND arguments_sha256 NOT GLOB '*[^0-9a-f]*' AND length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'), 
              CONSTRAINT ck_action_review_heads_resolution CHECK ((state IN ('pending','invalidated') AND resolution IS NULL AND last_review_id IS NULL) OR (state='reviewed' AND resolution IN ('applied','not_applied','still_unknown') AND last_review_id IS NOT NULL)), 
              CONSTRAINT fk_action_review_heads_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
            )""",
    "CREATE INDEX ix_action_review_heads_agent_id ON action_review_heads (agent_id)",
    "CREATE INDEX ix_action_review_heads_agent_sequence ON action_review_heads (agent_id, sequence)",
    "CREATE INDEX ix_action_review_heads_run ON action_review_heads (agent_id, run_id)",
    """CREATE TABLE action_reviews (
              id VARCHAR(36) NOT NULL CONSTRAINT ck_action_reviews_uuid_length CHECK (length(id) = 36), 
              agent_id VARCHAR(36) NOT NULL, 
              created_at VARCHAR(40) NOT NULL, 
              head_id VARCHAR(36) NOT NULL, 
              action_id VARCHAR(36) NOT NULL, 
              run_id VARCHAR(36) NOT NULL, 
              head_revision INTEGER NOT NULL, 
              history_epoch VARCHAR(36) NOT NULL, 
              policy VARCHAR(40) NOT NULL, 
              fingerprint VARCHAR(64) NOT NULL, 
              request_sha256 VARCHAR(64) NOT NULL, 
              proof JSON NOT NULL, 
              decision VARCHAR(20) NOT NULL, 
              evidence_sha256 VARCHAR(64) NOT NULL, 
              review_audit_id VARCHAR(36) NOT NULL, 
              CONSTRAINT pk_action_reviews PRIMARY KEY (id), 
              CONSTRAINT fk_action_reviews_head_id_action_review_heads FOREIGN KEY(head_id, action_id, run_id, agent_id) REFERENCES action_review_heads (id, action_id, run_id, agent_id) ON DELETE RESTRICT, 
              CONSTRAINT fk_action_reviews_review_audit_id_audit FOREIGN KEY(review_audit_id, agent_id) REFERENCES audit (id, agent_id) ON DELETE RESTRICT, 
              CONSTRAINT uq_action_reviews_head_id UNIQUE (head_id, head_revision), 
              CONSTRAINT uq_action_reviews_id UNIQUE (id, head_id, agent_id), 
              CONSTRAINT ck_action_reviews_decision CHECK (typeof(head_revision)='integer' AND head_revision BETWEEN 0 AND 9223372036854775807 AND policy='action-review-1' AND length(history_epoch)=36 AND decision IN ('applied','not_applied','still_unknown')), 
              CONSTRAINT ck_action_reviews_fingerprints CHECK (length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*' AND length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*' AND length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'), 
              CONSTRAINT ck_action_reviews_proof CHECK (json_valid(proof) AND json_type(proof)='object'), 
              CONSTRAINT uq_action_reviews_id UNIQUE (id, agent_id), 
              CONSTRAINT fk_action_reviews_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
            )""",
    "CREATE INDEX ix_action_reviews_action ON action_reviews (agent_id, action_id, head_revision)",
    "CREATE INDEX ix_action_reviews_agent_id ON action_reviews (agent_id)",
    "CREATE INDEX ix_action_reviews_review_audit ON action_reviews (agent_id, review_audit_id)",
    """CREATE TABLE action_review_evidence (
              id VARCHAR(36) NOT NULL CONSTRAINT ck_action_review_evidence_uuid_length CHECK (length(id) = 36), 
              agent_id VARCHAR(36) NOT NULL, 
              created_at VARCHAR(40) NOT NULL, 
              review_id VARCHAR(36) NOT NULL, 
              body TEXT NOT NULL, 
              body_sha256 VARCHAR(64) NOT NULL, 
              erased_at VARCHAR(40), 
              CONSTRAINT pk_action_review_evidence PRIMARY KEY (id), 
              CONSTRAINT fk_action_review_evidence_review_id_action_reviews FOREIGN KEY(review_id, agent_id) REFERENCES action_reviews (id, agent_id) ON DELETE RESTRICT, 
              CONSTRAINT uq_action_review_evidence_review_id UNIQUE (review_id), 
              CONSTRAINT ck_action_review_evidence_body CHECK (length(body_sha256)=64 AND body_sha256 NOT GLOB '*[^0-9a-f]*' AND ((erased_at IS NULL AND length(body) BETWEEN 1 AND 2000) OR (erased_at IS NOT NULL AND body=''))), 
              CONSTRAINT uq_action_review_evidence_id UNIQUE (id, agent_id), 
              CONSTRAINT fk_action_review_evidence_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
            )""",
    "CREATE INDEX ix_action_review_evidence_agent_id ON action_review_evidence (agent_id)",
    """CREATE TRIGGER action_unknown_origin_identity BEFORE UPDATE ON actions WHEN
                  old.writes=1 AND old.state NOT IN ('pending','approved','executing') AND (old.state='unknown' OR old.effect='unknown') AND EXISTS (
                    SELECT 1 FROM runs r WHERE r.id=old.run_id AND r.agent_id=old.agent_id AND
                    r.state IN ('succeeded','failed','cancelled','interrupted')) AND (
                  new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
                  new.run_id IS NOT old.run_id OR new.tool IS NOT old.tool OR new.writes IS NOT old.writes OR
                  new.state IS NOT old.state OR new.effect IS NOT old.effect OR
                  new.arguments_sha256 IS NOT old.arguments_sha256 OR new.policy_revision IS NOT old.policy_revision OR
                  new.ended_at IS NOT old.ended_at)
                  BEGIN SELECT RAISE(ABORT,'unknown execution origin is immutable; owner observations use a separate ledger'); END""",
    """CREATE TRIGGER action_review_head_birth BEFORE INSERT ON action_review_heads WHEN
                  new.revision!=0 OR new.state!='pending' OR NOT EXISTS (
                    SELECT 1 FROM actions a JOIN runs r ON r.id=a.run_id AND r.agent_id=a.agent_id
                    JOIN sessions se ON se.id=r.session_id AND se.agent_id=r.agent_id JOIN history_identity h ON h.slot=1
                    WHERE a.id=new.action_id AND a.run_id=new.run_id AND a.agent_id=new.agent_id AND a.writes=1 AND
                      a.state NOT IN ('pending','approved','executing') AND (a.state='unknown' OR a.effect='unknown') AND r.state IN ('succeeded','failed','cancelled','interrupted') AND
                      se.content_epoch=new.session_epoch AND h.epoch=new.history_epoch AND
                      a.arguments_sha256=new.arguments_sha256 AND (a.body_pruned_at IS NULL OR a.result_sha256=new.result_sha256))
                  BEGIN SELECT RAISE(ABORT,'action review requires the exact terminal unknown write'); END""",
    """CREATE TRIGGER action_review_head_identity BEFORE UPDATE ON action_review_heads WHEN
                  new.sequence IS NOT old.sequence OR new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR
                  new.created_at IS NOT old.created_at OR new.action_id IS NOT old.action_id OR new.run_id IS NOT old.run_id OR
                  new.policy IS NOT old.policy OR new.revision!=old.revision+1 OR
                  (new.state='reviewed' AND NOT EXISTS (
                    SELECT 1 FROM action_reviews v WHERE v.id=new.last_review_id AND v.head_id=new.id AND
                      v.agent_id=new.agent_id AND v.action_id=new.action_id AND v.run_id=new.run_id AND
                      v.head_revision=old.revision AND v.history_epoch=new.history_epoch AND
                      v.fingerprint=new.fingerprint AND v.decision=new.resolution))
                  BEGIN SELECT RAISE(ABORT,'action review identity and revision require an immutable observation'); END""",
    """CREATE TRIGGER action_review_birth BEFORE INSERT ON action_reviews WHEN NOT EXISTS (
                  SELECT 1 FROM action_review_heads h JOIN actions a ON a.id=h.action_id AND a.agent_id=h.agent_id
                  JOIN runs r ON r.id=h.run_id AND r.agent_id=h.agent_id
                  JOIN sessions se ON se.id=r.session_id AND se.agent_id=r.agent_id JOIN history_identity hi ON hi.slot=1
                  WHERE h.id=new.head_id AND h.action_id=new.action_id AND h.run_id=new.run_id AND h.agent_id=new.agent_id AND
                    h.revision=new.head_revision AND h.state IN ('pending','reviewed') AND h.history_epoch=new.history_epoch AND
                    hi.epoch=new.history_epoch AND h.fingerprint=new.fingerprint AND h.policy=new.policy AND
                    h.session_epoch=se.content_epoch AND h.arguments_sha256=a.arguments_sha256 AND a.writes=1 AND
                    a.state NOT IN ('pending','approved','executing') AND (a.state='unknown' OR a.effect='unknown') AND r.state IN ('succeeded','failed','cancelled','interrupted'))
                  BEGIN SELECT RAISE(ABORT,'action review proof or owner revision changed'); END""",
    """CREATE TRIGGER action_review_evidence_birth BEFORE INSERT ON action_review_evidence WHEN
                  new.erased_at IS NOT NULL OR NOT EXISTS (SELECT 1 FROM action_reviews v WHERE v.id=new.review_id AND
                    v.agent_id=new.agent_id AND v.evidence_sha256=new.body_sha256)
                  BEGIN SELECT RAISE(ABORT,'review evidence must bind its immutable digest'); END""",
    """CREATE TRIGGER action_review_evidence_monotone BEFORE UPDATE ON action_review_evidence WHEN
                  new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
                  new.review_id IS NOT old.review_id OR new.body_sha256 IS NOT old.body_sha256 OR
                  old.erased_at IS NOT NULL OR new.erased_at IS NULL OR new.body!=''
                  BEGIN SELECT RAISE(ABORT,'review evidence may only be erased once'); END""",
    """CREATE TRIGGER action_review_body_changed AFTER UPDATE OF arguments,result,result_sha256,body_pruned_at ON actions
                  WHEN old.arguments IS NOT new.arguments OR old.result IS NOT new.result OR
                    old.result_sha256 IS NOT new.result_sha256 OR old.body_pruned_at IS NOT new.body_pruned_at BEGIN
                  UPDATE action_review_evidence SET body='',erased_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE erased_at IS NULL AND review_id IN (
                    SELECT v.id FROM action_reviews v JOIN action_review_heads h ON h.id=v.head_id AND h.agent_id=v.agent_id
                    WHERE h.action_id=old.id AND h.agent_id=old.agent_id AND NOT COALESCE((old.body_pruned_at IS NULL AND new.body_pruned_at IS NOT NULL AND
                  new.arguments_sha256=old.arguments_sha256 AND new.result_sha256=h.result_sha256 AND
                  COALESCE(json_extract(new.result,'$.body_pruned'),0)=1 AND
                  json_extract(new.result,'$.sha256')=h.result_sha256 AND json_extract(new.result,'$.status')=new.state AND
                  json_extract(new.result,'$.effect')=new.effect),0));
                  UPDATE action_review_heads SET revision=revision+1,state='invalidated',resolution=NULL,last_review_id=NULL,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),error_code='action_review_source_changed' WHERE action_review_heads.action_id=old.id AND action_review_heads.agent_id=old.agent_id AND NOT COALESCE((old.body_pruned_at IS NULL AND new.body_pruned_at IS NOT NULL AND
                  new.arguments_sha256=old.arguments_sha256 AND new.result_sha256=action_review_heads.result_sha256 AND
                  COALESCE(json_extract(new.result,'$.body_pruned'),0)=1 AND
                  json_extract(new.result,'$.sha256')=action_review_heads.result_sha256 AND json_extract(new.result,'$.status')=new.state AND
                  json_extract(new.result,'$.effect')=new.effect),0);
                END""",
    """CREATE TRIGGER action_review_source_erased AFTER UPDATE OF content_epoch ON sessions
                  WHEN old.content_epoch IS NOT new.content_epoch BEGIN
                  UPDATE action_review_evidence SET body='',erased_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE erased_at IS NULL AND review_id IN (
                    SELECT v.id FROM action_reviews v JOIN action_review_heads h ON h.id=v.head_id AND h.agent_id=v.agent_id
                    WHERE h.agent_id=old.agent_id AND h.run_id IN (SELECT r.id FROM runs r WHERE r.session_id=old.id AND r.agent_id=old.agent_id));
                  UPDATE action_review_heads SET revision=revision+1,state='invalidated',resolution=NULL,last_review_id=NULL,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),error_code='action_review_source_erased' WHERE action_review_heads.agent_id=old.agent_id AND action_review_heads.run_id IN (SELECT r.id FROM runs r WHERE r.session_id=old.id AND r.agent_id=old.agent_id);
                END""",
    "CREATE TRIGGER action_reviews_immutable BEFORE UPDATE ON action_reviews BEGIN SELECT RAISE(ABORT,'action review observations are immutable'); END",
    "CREATE TRIGGER action_review_heads_keep BEFORE DELETE ON action_review_heads BEGIN SELECT RAISE(ABORT,'action review metadata cannot be deleted'); END",
    "CREATE TRIGGER action_reviews_keep BEFORE DELETE ON action_reviews BEGIN SELECT RAISE(ABORT,'action review metadata cannot be deleted'); END",
    "CREATE TRIGGER action_review_evidence_keep BEFORE DELETE ON action_review_evidence BEGIN SELECT RAISE(ABORT,'action review metadata cannot be deleted'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
