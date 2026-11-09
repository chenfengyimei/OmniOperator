"""Captured static Python extension revisions and epoch-bound authorization; never migrates old placeholder data."""

from alembic import op

from omni.domain.contracts import BusinessError

revision = "v2_0019"
down_revision = "v2_0018"
branch_labels = None
depends_on = None

SQL = (
    "DROP TABLE grants",
    "DROP TABLE extensions",
    """CREATE TABLE extensions (
          sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
          id VARCHAR(36) NOT NULL CONSTRAINT ck_extensions_uuid_length CHECK (length(id)=36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          name VARCHAR(32) NOT NULL, 
          revision INTEGER NOT NULL, 
          authority_epoch INTEGER NOT NULL, 
          enabled BOOLEAN NOT NULL, 
          state VARCHAR(20) NOT NULL, 
          current_revision_id VARCHAR(36) NOT NULL, 
          updated_at VARCHAR(40) NOT NULL, 
          error_code VARCHAR(80) DEFAULT '' NOT NULL, 
          CONSTRAINT uq_extensions_id UNIQUE (id), 
          CONSTRAINT uq_extensions_id UNIQUE (id, agent_id), 
          CONSTRAINT uq_extensions_agent_id UNIQUE (agent_id, name), 
          CONSTRAINT fk_extensions_current_revision_id_extension_revisions FOREIGN KEY(current_revision_id, id, agent_id) REFERENCES extension_revisions (id, extension_id, agent_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED, 
          CONSTRAINT ck_extensions_identity CHECK (sequence>0 AND length(name) BETWEEN 1 AND 32 AND name GLOB '[a-z]*' AND name NOT GLOB '*[^a-z0-9_]*' AND length(error_code)<=80), 
          CONSTRAINT ck_extensions_revision CHECK (typeof(revision)='integer' AND revision BETWEEN 1 AND 9223372036854775807 AND typeof(authority_epoch)='integer' AND authority_epoch BETWEEN 1 AND 9223372036854775807), 
          CONSTRAINT ck_extensions_state CHECK (enabled IN (0,1) AND ((enabled=1 AND state='enabled') OR (enabled=0 AND state IN ('disabled','drifted','missing')))), 
          CONSTRAINT fk_extensions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_extensions_agent_id ON extensions (agent_id)",
    "CREATE INDEX ix_extensions_agent_sequence ON extensions (agent_id, sequence)",
    """CREATE TABLE extension_revisions (
          id VARCHAR(36) NOT NULL CONSTRAINT ck_extension_revisions_uuid_length CHECK (length(id) = 36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          extension_id VARCHAR(36) NOT NULL, 
          ordinal INTEGER NOT NULL, 
          version VARCHAR(64) NOT NULL, 
          protocol VARCHAR(40) NOT NULL, 
          object_id VARCHAR(36) NOT NULL, 
          package_sha256 VARCHAR(64) NOT NULL, 
          catalog_sha256 VARCHAR(64) NOT NULL, 
          manifest JSON NOT NULL, 
          files JSON NOT NULL, 
          entry VARCHAR(200) NOT NULL, 
          file_count INTEGER NOT NULL, 
          total_bytes INTEGER NOT NULL, 
          CONSTRAINT pk_extension_revisions PRIMARY KEY (id), 
          CONSTRAINT fk_extension_revisions_extension_id_extensions FOREIGN KEY(extension_id, agent_id) REFERENCES extensions (id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT fk_extension_revisions_object_id_objects FOREIGN KEY(object_id, agent_id) REFERENCES objects (id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT uq_extension_revisions_extension_id UNIQUE (extension_id, ordinal), 
          CONSTRAINT uq_extension_revisions_id UNIQUE (id, extension_id, agent_id), 
          CONSTRAINT ck_extension_revisions_identity CHECK (typeof(ordinal)='integer' AND ordinal BETWEEN 1 AND 9223372036854775807 AND length(version) BETWEEN 1 AND 64 AND protocol='omni-python-extension-ipc-1' AND length(entry) BETWEEN 1 AND 200), 
          CONSTRAINT ck_extension_revisions_fingerprints CHECK (length(package_sha256)=64 AND package_sha256 NOT GLOB '*[^0-9a-f]*' AND length(catalog_sha256)=64 AND catalog_sha256 NOT GLOB '*[^0-9a-f]*'), 
          CONSTRAINT ck_extension_revisions_bounds CHECK (typeof(file_count)='integer' AND file_count BETWEEN 1 AND 64 AND typeof(total_bytes)='integer' AND total_bytes BETWEEN 0 AND 8388608), 
          CONSTRAINT ck_extension_revisions_metadata CHECK (json_valid(manifest) AND json_type(manifest)='object' AND json_valid(files) AND json_type(files)='array' AND json_array_length(files)=file_count), 
          CONSTRAINT uq_extension_revisions_id UNIQUE (id, agent_id), 
          CONSTRAINT fk_extension_revisions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_extension_revisions_agent_id ON extension_revisions (agent_id)",
    "CREATE INDEX ix_extension_revisions_object ON extension_revisions (agent_id, object_id)",
    """CREATE TABLE extension_tools (
          id VARCHAR(36) NOT NULL CONSTRAINT ck_extension_tools_uuid_length CHECK (length(id) = 36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          extension_id VARCHAR(36) NOT NULL, 
          extension_revision_id VARCHAR(36) NOT NULL, 
          name VARCHAR(100) NOT NULL, 
          local_name VARCHAR(32) NOT NULL, 
          description VARCHAR(512) NOT NULL, 
          parameters JSON NOT NULL, 
          timeout_s INTEGER NOT NULL, 
          writes BOOLEAN NOT NULL, 
          spec_sha256 VARCHAR(64) NOT NULL, 
          CONSTRAINT pk_extension_tools PRIMARY KEY (id), 
          CONSTRAINT fk_extension_tools_extension_revision_id_extension_revisions FOREIGN KEY(extension_revision_id, extension_id, agent_id) REFERENCES extension_revisions (id, extension_id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT uq_extension_tools_extension_revision_id UNIQUE (extension_revision_id, name), 
          CONSTRAINT uq_extension_tools_extension_revision_id UNIQUE (extension_revision_id, local_name), 
          CONSTRAINT uq_extension_tools_id UNIQUE (id, extension_revision_id, extension_id, agent_id), 
          CONSTRAINT ck_extension_tools_identity CHECK (length(name) BETWEEN 1 AND 64 AND length(local_name) BETWEEN 1 AND 32 AND local_name GLOB '[a-z]*' AND local_name NOT GLOB '*[^a-z0-9_]*' AND length(description) BETWEEN 1 AND 512), 
          CONSTRAINT ck_extension_tools_behavior CHECK (typeof(timeout_s)='integer' AND timeout_s BETWEEN 1 AND 300 AND writes=1 AND length(spec_sha256)=64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'), 
          CONSTRAINT ck_extension_tools_parameters CHECK (json_valid(parameters) AND json_type(parameters)='object'), 
          CONSTRAINT uq_extension_tools_id UNIQUE (id, agent_id), 
          CONSTRAINT fk_extension_tools_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_extension_tools_agent_id ON extension_tools (agent_id)",
    """CREATE TABLE grants (
          id VARCHAR(36) NOT NULL CONSTRAINT ck_grants_uuid_length CHECK (length(id) = 36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          extension_id VARCHAR(36) NOT NULL, 
          extension_revision_id VARCHAR(36) NOT NULL, 
          tool_id VARCHAR(36) NOT NULL, 
          authority_epoch INTEGER NOT NULL, 
          history_epoch VARCHAR(36) NOT NULL, 
          revoked_at VARCHAR(40), 
          review_audit_id VARCHAR(36) NOT NULL, 
          CONSTRAINT pk_grants PRIMARY KEY (id), 
          CONSTRAINT fk_grants_tool_id_extension_tools FOREIGN KEY(tool_id, extension_revision_id, extension_id, agent_id) REFERENCES extension_tools (id, extension_revision_id, extension_id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT fk_grants_review_audit_id_audit FOREIGN KEY(review_audit_id, agent_id) REFERENCES audit (id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT uq_grants_extension_id UNIQUE (extension_id, authority_epoch, tool_id), 
          CONSTRAINT uq_grants_id UNIQUE (id, tool_id, authority_epoch, agent_id), 
          CONSTRAINT ck_grants_authority CHECK (typeof(authority_epoch)='integer' AND authority_epoch BETWEEN 1 AND 9223372036854775807 AND length(history_epoch)=36), 
          CONSTRAINT uq_grants_id UNIQUE (id, agent_id), 
          CONSTRAINT fk_grants_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_grants_agent_id ON grants (agent_id)",
    """CREATE TABLE run_extension_tools (
          id VARCHAR(36) NOT NULL CONSTRAINT ck_run_extension_tools_uuid_length CHECK (length(id) = 36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          run_id VARCHAR(36) NOT NULL, 
          extension_id VARCHAR(36) NOT NULL, 
          extension_revision_id VARCHAR(36) NOT NULL, 
          tool_id VARCHAR(36) NOT NULL, 
          grant_id VARCHAR(36) NOT NULL, 
          authority_epoch INTEGER NOT NULL, 
          history_epoch VARCHAR(36) NOT NULL, 
          name VARCHAR(100) NOT NULL, 
          CONSTRAINT pk_run_extension_tools PRIMARY KEY (id), 
          CONSTRAINT fk_run_extension_tools_run_id_runs FOREIGN KEY(run_id, agent_id) REFERENCES runs (id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT fk_run_extension_tools_tool_id_extension_tools FOREIGN KEY(tool_id, extension_revision_id, extension_id, agent_id) REFERENCES extension_tools (id, extension_revision_id, extension_id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT fk_run_extension_tools_grant_id_grants FOREIGN KEY(grant_id, tool_id, authority_epoch, agent_id) REFERENCES grants (id, tool_id, authority_epoch, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT uq_run_extension_tools_run_id UNIQUE (run_id, name), 
          CONSTRAINT uq_run_extension_tools_id UNIQUE (id, run_id, agent_id), 
          CONSTRAINT ck_run_extension_tools_identity CHECK (typeof(authority_epoch)='integer' AND authority_epoch BETWEEN 1 AND 9223372036854775807 AND length(history_epoch)=36 AND length(name) BETWEEN 1 AND 64), 
          CONSTRAINT uq_run_extension_tools_id UNIQUE (id, agent_id), 
          CONSTRAINT fk_run_extension_tools_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_run_extension_tools_agent_id ON run_extension_tools (agent_id)",
    """CREATE TABLE action_extension_bindings (
          id VARCHAR(36) NOT NULL CONSTRAINT ck_action_extension_bindings_uuid_length CHECK (length(id) = 36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          action_id VARCHAR(36) NOT NULL, 
          run_tool_id VARCHAR(36) NOT NULL, 
          CONSTRAINT pk_action_extension_bindings PRIMARY KEY (id), 
          CONSTRAINT fk_action_extension_bindings_action_id_actions FOREIGN KEY(action_id, agent_id) REFERENCES actions (id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT fk_action_extension_bindings_run_tool_id_run_extension_tools FOREIGN KEY(run_tool_id, agent_id) REFERENCES run_extension_tools (id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT uq_action_extension_bindings_action_id UNIQUE (action_id), 
          CONSTRAINT uq_action_extension_bindings_id UNIQUE (id, agent_id), 
          CONSTRAINT fk_action_extension_bindings_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_action_extension_bindings_agent_id ON action_extension_bindings (agent_id)",
    """CREATE TABLE extension_install_requests (
          id VARCHAR(36) NOT NULL CONSTRAINT ck_extension_install_requests_uuid_length CHECK (length(id) = 36), 
          agent_id VARCHAR(36) NOT NULL, 
          created_at VARCHAR(40) NOT NULL, 
          request_key VARCHAR(200) NOT NULL, 
          request_sha256 VARCHAR(64) NOT NULL, 
          extension_id VARCHAR(36) NOT NULL, 
          extension_revision_id VARCHAR(36) NOT NULL, 
          CONSTRAINT pk_extension_install_requests PRIMARY KEY (id), 
          CONSTRAINT fk_extension_install_requests_extension_revision_id_extension_revisions FOREIGN KEY(extension_revision_id, extension_id, agent_id) REFERENCES extension_revisions (id, extension_id, agent_id) ON DELETE RESTRICT, 
          CONSTRAINT uq_extension_install_requests_agent_id UNIQUE (agent_id, request_key), 
          CONSTRAINT ck_extension_install_requests_identity CHECK (length(request_key) BETWEEN 1 AND 200 AND length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'), 
          CONSTRAINT uq_extension_install_requests_id UNIQUE (id, agent_id), 
          CONSTRAINT fk_extension_install_requests_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT
        )""",
    "CREATE INDEX ix_extension_install_requests_agent_id ON extension_install_requests (agent_id)",
    "ALTER TABLE runs ADD COLUMN extension_capture_epoch VARCHAR(36) CHECK(extension_capture_epoch IS NULL OR length(extension_capture_epoch)=36)",
    "UPDATE runs SET extension_capture_epoch=(SELECT epoch FROM history_identity WHERE slot=1)",
    """CREATE TRIGGER extension_slot_identity BEFORE UPDATE ON extensions WHEN
              new.sequence IS NOT old.sequence OR new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR
              new.created_at IS NOT old.created_at OR new.name IS NOT old.name OR
              new.revision!=old.revision+1 OR new.authority_epoch!=old.authority_epoch+1
              BEGIN SELECT RAISE(ABORT,'extension slot identity and authority are immutable'); END""",
    """CREATE TRIGGER extension_grant_identity BEFORE UPDATE ON grants WHEN
              new.id IS NOT old.id OR new.agent_id IS NOT old.agent_id OR new.created_at IS NOT old.created_at OR
              new.extension_id IS NOT old.extension_id OR new.extension_revision_id IS NOT old.extension_revision_id OR
              new.tool_id IS NOT old.tool_id OR new.authority_epoch IS NOT old.authority_epoch OR
              new.history_epoch IS NOT old.history_epoch OR new.review_audit_id IS NOT old.review_audit_id OR
              old.revoked_at IS NOT NULL OR new.revoked_at IS NULL
              BEGIN SELECT RAISE(ABORT,'extension grant may only be revoked once'); END""",
    """CREATE TRIGGER extension_tool_name BEFORE INSERT ON extension_tools WHEN NOT EXISTS (
              SELECT 1 FROM extensions e WHERE e.id=new.extension_id AND e.agent_id=new.agent_id AND
              new.name='ext__'||e.name||'__'||new.local_name)
              BEGIN SELECT RAISE(ABORT,'extension tool namespace must match its immutable identity'); END""",
    """CREATE TRIGGER extension_run_capture_identity BEFORE UPDATE ON runs WHEN
              old.extension_capture_epoch IS NOT NULL AND new.extension_capture_epoch IS NOT old.extension_capture_epoch
              BEGIN SELECT RAISE(ABORT,'run extension capture is immutable'); END""",
    """CREATE TRIGGER extension_run_tool_authority BEFORE INSERT ON run_extension_tools WHEN NOT EXISTS (
              SELECT 1 FROM runs r JOIN extension_tools t ON t.id=new.tool_id AND t.agent_id=new.agent_id
              JOIN grants g ON g.id=new.grant_id AND g.tool_id=t.id AND g.agent_id=new.agent_id
              JOIN extensions e ON e.id=new.extension_id AND e.agent_id=new.agent_id
              JOIN history_identity h ON h.slot=1
              WHERE r.id=new.run_id AND r.agent_id=new.agent_id AND r.extension_capture_epoch IS NULL AND
                t.extension_id=new.extension_id AND t.extension_revision_id=new.extension_revision_id AND t.name=new.name AND
                g.extension_id=new.extension_id AND g.extension_revision_id=new.extension_revision_id AND
                g.authority_epoch=new.authority_epoch AND g.history_epoch=new.history_epoch AND g.revoked_at IS NULL AND
                e.enabled=1 AND e.state='enabled' AND e.current_revision_id=new.extension_revision_id AND
                e.authority_epoch=new.authority_epoch AND h.epoch=new.history_epoch)
              BEGIN SELECT RAISE(ABORT,'run extension tools require a live grant before capture closes'); END""",
    """CREATE TRIGGER extension_action_binding_identity BEFORE INSERT ON action_extension_bindings WHEN NOT EXISTS (
              SELECT 1 FROM actions a JOIN run_extension_tools t ON t.id=new.run_tool_id AND t.agent_id=new.agent_id
              WHERE a.id=new.action_id AND a.agent_id=new.agent_id AND a.run_id=t.run_id AND a.tool=t.name AND a.writes=1)
              BEGIN SELECT RAISE(ABORT,'extension action must bind its own run and exact tool'); END""",
    "CREATE TRIGGER extension_revisions_immutable BEFORE UPDATE ON extension_revisions BEGIN SELECT RAISE(ABORT,'extension identity and captured evidence are immutable'); END",
    "CREATE TRIGGER extension_tools_immutable BEFORE UPDATE ON extension_tools BEGIN SELECT RAISE(ABORT,'extension identity and captured evidence are immutable'); END",
    "CREATE TRIGGER run_extension_tools_immutable BEFORE UPDATE ON run_extension_tools BEGIN SELECT RAISE(ABORT,'extension identity and captured evidence are immutable'); END",
    "CREATE TRIGGER action_extension_bindings_immutable BEFORE UPDATE ON action_extension_bindings BEGIN SELECT RAISE(ABORT,'extension identity and captured evidence are immutable'); END",
    "CREATE TRIGGER extension_install_requests_immutable BEFORE UPDATE ON extension_install_requests BEGIN SELECT RAISE(ABORT,'extension identity and captured evidence are immutable'); END",
    "CREATE TRIGGER extensions_keep BEFORE DELETE ON extensions BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE TRIGGER extension_revisions_keep BEFORE DELETE ON extension_revisions BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE TRIGGER extension_tools_keep BEFORE DELETE ON extension_tools BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE TRIGGER grants_keep BEFORE DELETE ON grants BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE TRIGGER run_extension_tools_keep BEFORE DELETE ON run_extension_tools BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE TRIGGER action_extension_bindings_keep BEFORE DELETE ON action_extension_bindings BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE TRIGGER extension_install_requests_keep BEFORE DELETE ON extension_install_requests BEGIN SELECT RAISE(ABORT,'extension authorization evidence cannot be deleted'); END",
    "CREATE INDEX ix_grants_review_audit ON grants (agent_id, review_audit_id)",
    "CREATE INDEX ix_memory_organization_review_audit ON memory_organization_candidates (agent_id, review_audit_id)",
)


def upgrade() -> None:
    connection = op.get_bind()
    if any(
        connection.exec_driver_sql("SELECT COUNT(*) FROM " + table).scalar_one() for table in ("extensions", "grants")
    ):
        raise BusinessError(
            "extension_placeholder_not_empty",
            "Old extension placeholder data requires review; keep the verified schema backup and use a clean V2 instance",
        )
    if (
        connection.exec_driver_sql("SELECT COUNT(*) FROM runs").scalar_one()
        and connection.exec_driver_sql("SELECT epoch FROM history_identity WHERE slot=1").scalar_one_or_none() is None
    ):
        raise BusinessError(
            "extension_history_identity_missing",
            "Existing runs require a valid history identity before extension capture can be sealed",
            status=503,
        )
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
