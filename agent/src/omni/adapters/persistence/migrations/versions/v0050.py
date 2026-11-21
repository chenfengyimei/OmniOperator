"""Tool-result image ledger: nullable message_id, two-branch scope and run-terminal release; frozen DDL, transactional rebuild preserving every accounting row (v0048 precedent)."""

from alembic import op

revision = "v2_0050"
down_revision = "v2_0049"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE model_call_images RENAME TO _v50_model_call_images",
    """CREATE TABLE model_call_images (
      id VARCHAR(36) NOT NULL CONSTRAINT ck_model_call_images_uuid_length CHECK (length(id) = 36),
      agent_id VARCHAR(36) NOT NULL,
      created_at VARCHAR(40) NOT NULL,
      call_id VARCHAR(36) NOT NULL,
      snapshot_id VARCHAR(36) NOT NULL,
      policy_id VARCHAR(36) NOT NULL,
      message_id VARCHAR(36),
      part_id VARCHAR(36) NOT NULL,
      object_id VARCHAR(36),
      sha256 VARCHAR(64) NOT NULL,
      state VARCHAR(20) NOT NULL,
      erased_at VARCHAR(40),
      CONSTRAINT pk_model_call_images PRIMARY KEY (id),
      CONSTRAINT fk_model_call_images_call_id_model_calls FOREIGN KEY(call_id, agent_id) REFERENCES model_calls (id, agent_id) ON DELETE RESTRICT,
      CONSTRAINT fk_model_call_images_snapshot_id_context_snapshots FOREIGN KEY(snapshot_id, agent_id) REFERENCES context_snapshots (id, agent_id) ON DELETE RESTRICT,
      CONSTRAINT fk_model_call_images_policy_id_model_image_policies FOREIGN KEY(policy_id, agent_id) REFERENCES model_image_policies (id, agent_id) ON DELETE RESTRICT,
      CONSTRAINT fk_model_call_images_message_id_messages FOREIGN KEY(message_id, agent_id) REFERENCES messages (id, agent_id) ON DELETE RESTRICT,
      CONSTRAINT fk_model_call_images_object_id_objects FOREIGN KEY(object_id, agent_id) REFERENCES objects (id, agent_id) ON DELETE RESTRICT,
      CONSTRAINT uq_model_call_images_call_id UNIQUE (call_id, part_id),
      CONSTRAINT ck_model_call_images_image CHECK (state IN ('prepared','dispatched','attached','not_sent','unknown') AND length(part_id)=36 AND length(sha256)=64 AND (erased_at IS NULL OR object_id IS NULL)),
      CONSTRAINT uq_model_call_images_id UNIQUE (id, agent_id),
      CONSTRAINT fk_model_call_images_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
)""",
    "INSERT INTO model_call_images(id,agent_id,created_at,call_id,snapshot_id,policy_id,message_id,part_id,object_id,sha256,state,erased_at) "
    "SELECT id,agent_id,created_at,call_id,snapshot_id,policy_id,message_id,part_id,object_id,sha256,state,erased_at "
    "FROM _v50_model_call_images",
    "DROP TABLE _v50_model_call_images",
    "CREATE INDEX ix_model_call_images_agent_id ON model_call_images (agent_id)",
    "CREATE INDEX ix_model_call_images_call ON model_call_images (agent_id, call_id)",
    # RENAME rewrites trigger bodies on OTHER tables that name model_call_images;
    # recreate both verbatim so they target the rebuilt table again.
    "DROP TRIGGER IF EXISTS call_image_state",
    "CREATE TRIGGER call_image_state AFTER UPDATE OF state ON model_calls WHEN new.state IN ('settled','unknown','not_sent') BEGIN UPDATE model_call_images SET state=CASE new.state WHEN 'settled' THEN 'attached' WHEN 'unknown' THEN 'unknown' ELSE 'not_sent' END WHERE call_id=new.id AND agent_id=new.agent_id AND state IN ('prepared','dispatched','unknown'); END",
    "DROP TRIGGER IF EXISTS call_image_erasure",
    "CREATE TRIGGER call_image_erasure AFTER UPDATE OF deleted_at ON messages WHEN old.deleted_at IS NULL AND new.deleted_at IS NOT NULL BEGIN UPDATE model_call_images SET object_id=NULL,erased_at=new.deleted_at WHERE message_id=new.id AND agent_id=new.agent_id; END",
    # Message branch keeps the v0032 guard verbatim; tool branch (message_id IS NULL)
    # requires a registered agent-scoped object and a part_id that impersonates no message part.
    """CREATE TRIGGER call_image_scope BEFORE INSERT ON model_call_images WHEN
      (new.message_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id AND r.agent_id=c.agent_id JOIN context_snapshots x ON x.id=new.snapshot_id AND x.run_id=r.id AND x.agent_id=r.agent_id JOIN model_image_policies p ON p.id=new.policy_id AND p.revision_id=r.model_revision_id AND p.agent_id=r.agent_id JOIN context_messages cm ON cm.snapshot_id=x.id AND cm.agent_id=x.agent_id AND cm.message_id=new.message_id JOIN message_parts mp ON mp.id=new.part_id AND mp.agent_id=cm.agent_id AND mp.message_id=cm.message_id JOIN messages m ON m.id=mp.message_id AND m.agent_id=mp.agent_id JOIN objects o ON o.id=mp.object_id AND o.agent_id=mp.agent_id WHERE c.id=new.call_id AND c.agent_id=new.agent_id AND c.state='reserved' AND c.purpose='turn' AND p.protocol='openai_low' AND mp.kind='image' AND mp.object_id=new.object_id AND o.sha256=new.sha256 AND m.deleted_at IS NULL AND new.state='prepared' AND new.erased_at IS NULL))
      OR (new.message_id IS NULL AND NOT EXISTS(SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id AND r.agent_id=c.agent_id JOIN context_snapshots x ON x.id=new.snapshot_id AND x.run_id=r.id AND x.agent_id=r.agent_id JOIN model_image_policies p ON p.id=new.policy_id AND p.revision_id=r.model_revision_id AND p.agent_id=r.agent_id JOIN image_objects io ON io.object_id=new.object_id AND io.agent_id=new.agent_id JOIN objects o ON o.id=new.object_id AND o.agent_id=new.agent_id WHERE c.id=new.call_id AND c.agent_id=new.agent_id AND c.state='reserved' AND c.purpose='turn' AND p.protocol='openai_low' AND o.sha256=new.sha256 AND new.state='prepared' AND new.erased_at IS NULL AND NOT EXISTS(SELECT 1 FROM message_parts WHERE id=new.part_id)))
      BEGIN SELECT RAISE(ABORT,'image call source or policy scope mismatch'); END""",
    "CREATE TRIGGER call_image_budget BEFORE INSERT ON model_call_images WHEN (SELECT count(*) FROM model_call_images WHERE call_id=new.call_id)>= (SELECT per_call_limit FROM model_image_policies WHERE id=new.policy_id) OR (SELECT count(*) FROM model_call_images i JOIN model_calls c ON c.id=i.call_id WHERE c.run_id=(SELECT run_id FROM model_calls WHERE id=new.call_id) AND i.state!='not_sent') >= (SELECT run_budget FROM model_image_policies WHERE id=new.policy_id) BEGIN SELECT RAISE(ABORT,'cumulative image budget exhausted'); END",
    # Identity stays immutable; the only new permitted mutation is releasing a tool row's
    # object reference once its run is terminal (release is not erasure: erased_at stays NULL).
    """CREATE TRIGGER call_image_update BEFORE UPDATE ON model_call_images WHEN
      new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR new.call_id IS NOT old.call_id OR new.snapshot_id IS NOT old.snapshot_id OR new.policy_id IS NOT old.policy_id OR new.message_id IS NOT old.message_id OR new.part_id IS NOT old.part_id OR new.sha256 IS NOT old.sha256
      OR (new.object_id IS NOT old.object_id AND NOT((new.object_id IS NULL AND new.erased_at IS NOT NULL AND EXISTS(SELECT 1 FROM messages WHERE id=old.message_id AND agent_id=old.agent_id AND deleted_at=new.erased_at)) OR (old.message_id IS NULL AND new.message_id IS NULL AND new.object_id IS NULL AND new.erased_at IS old.erased_at AND EXISTS(SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id AND r.agent_id=c.agent_id WHERE c.id=old.call_id AND c.agent_id=old.agent_id AND r.state IN ('succeeded','failed','cancelled','interrupted')))))
      OR (new.erased_at IS NOT old.erased_at AND NOT(old.erased_at IS NULL AND new.erased_at IS NOT NULL AND new.object_id IS NULL AND EXISTS(SELECT 1 FROM messages WHERE id=old.message_id AND agent_id=old.agent_id AND deleted_at=new.erased_at)))
      OR (new.state IS NOT old.state AND NOT((new.state='dispatched' AND old.state='prepared' AND EXISTS(SELECT 1 FROM model_calls WHERE id=old.call_id AND state='reserved')) OR (new.state='attached' AND EXISTS(SELECT 1 FROM model_calls WHERE id=old.call_id AND state='settled')) OR (new.state='unknown' AND EXISTS(SELECT 1 FROM model_calls WHERE id=old.call_id AND state='unknown')) OR (new.state='not_sent' AND EXISTS(SELECT 1 FROM model_calls WHERE id=old.call_id AND state='not_sent'))))
      BEGIN SELECT RAISE(ABORT,'image call identity or transition is invalid'); END""",
    "CREATE TRIGGER call_image_delete BEFORE DELETE ON model_call_images BEGIN SELECT RAISE(ABORT,'image call accounting is immutable'); END",
    # In-run calls re-wire the same tool parts, so bytes must live until the run ends;
    # afterwards the transcript is gone and nothing can be re-sent, so release the reference
    # and let TTL collection proceed. Accounting (sha/part/state) is preserved.
    "CREATE TRIGGER call_image_tool_release AFTER UPDATE OF state ON runs WHEN new.state IN ('succeeded','failed','cancelled','interrupted') BEGIN UPDATE model_call_images SET object_id=NULL WHERE agent_id=new.agent_id AND message_id IS NULL AND object_id IS NOT NULL AND call_id IN (SELECT id FROM model_calls WHERE run_id=new.id AND agent_id=new.agent_id); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
