"""Immutable single-level child-run authority; frozen SQL, no current schema imports."""

from alembic import op

revision = "v2_0042"
down_revision = "v2_0041"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE run_delegations (id VARCHAR(36) NOT NULL, agent_id VARCHAR(36) NOT NULL, created_at VARCHAR(40) NOT NULL, parent_run_id VARCHAR(36) NOT NULL, child_run_id VARCHAR(36) NOT NULL, parent_session_id VARCHAR(36) NOT NULL, child_session_id VARCHAR(36) NOT NULL, parent_epoch INTEGER NOT NULL, parent_snapshot_id VARCHAR(36) NOT NULL, source_snapshot_id VARCHAR(36) NOT NULL, max_calls INTEGER NOT NULL, budget_micro INTEGER NOT NULL, tree_budget_micro INTEGER NOT NULL, tools JSON NOT NULL, boot_id VARCHAR(36) NOT NULL, CONSTRAINT pk_run_delegations PRIMARY KEY(id), CONSTRAINT ck_run_delegations_uuid_length CHECK(length(id)=36), CONSTRAINT ck_run_delegations_bounds CHECK(parent_run_id!=child_run_id AND parent_session_id!=child_session_id AND typeof(parent_epoch)='integer' AND parent_epoch>0 AND typeof(max_calls)='integer' AND max_calls BETWEEN 1 AND 8 AND typeof(budget_micro)='integer' AND budget_micro BETWEEN 1 AND 1000000 AND typeof(tree_budget_micro)='integer' AND tree_budget_micro BETWEEN 1 AND 1000000 AND json_valid(tools) AND json_type(tools)='object' AND length(CAST(tools AS BLOB))<=131072 AND length(boot_id)=36), CONSTRAINT fk_run_delegations_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE RESTRICT, CONSTRAINT fk_run_delegations_parent_run_id_runs FOREIGN KEY(parent_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT, CONSTRAINT fk_run_delegations_child_run_id_runs FOREIGN KEY(child_run_id,agent_id) REFERENCES runs(id,agent_id) ON DELETE RESTRICT, CONSTRAINT fk_run_delegations_parent_session_id_sessions FOREIGN KEY(parent_session_id,agent_id) REFERENCES sessions(id,agent_id) ON DELETE RESTRICT, CONSTRAINT fk_run_delegations_child_session_id_sessions FOREIGN KEY(child_session_id,agent_id) REFERENCES sessions(id,agent_id) ON DELETE RESTRICT, CONSTRAINT fk_run_delegations_parent_snapshot_id_context_snapshots FOREIGN KEY(parent_snapshot_id,agent_id) REFERENCES context_snapshots(id,agent_id) ON DELETE RESTRICT, CONSTRAINT fk_run_delegations_source_snapshot_id_model_call_routes FOREIGN KEY(source_snapshot_id,agent_id) REFERENCES model_call_routes(id,agent_id) ON DELETE RESTRICT, CONSTRAINT uq_run_delegations_id_agent_id UNIQUE(id,agent_id), CONSTRAINT uq_run_delegations_child_run_id UNIQUE(child_run_id), CONSTRAINT uq_run_delegations_child_session_id UNIQUE(child_session_id))",
    "CREATE INDEX ix_run_delegations_agent_id ON run_delegations(agent_id)",
    "CREATE INDEX ix_run_delegations_parent ON run_delegations(agent_id,parent_run_id)",
    "CREATE TRIGGER run_delegations_immutable BEFORE UPDATE ON run_delegations BEGIN SELECT RAISE(ABORT,'delegated authority is immutable'); END",
    "CREATE TRIGGER run_delegations_binding BEFORE INSERT ON run_delegations WHEN EXISTS(SELECT 1 FROM run_delegations WHERE child_run_id=new.parent_run_id) OR (SELECT count(*) FROM run_delegations WHERE parent_run_id=new.parent_run_id)>=4 OR NOT EXISTS(SELECT 1 FROM runs p JOIN runs c ON c.parent_id=p.id AND c.agent_id=p.agent_id JOIN sessions ps ON ps.id=p.session_id AND ps.agent_id=p.agent_id JOIN context_snapshots cs ON cs.id=new.parent_snapshot_id AND cs.run_id=p.id AND cs.agent_id=p.agent_id JOIN model_call_routes mr ON mr.id=new.source_snapshot_id AND mr.agent_id=p.agent_id JOIN model_calls mc ON mc.id=mr.call_id AND mc.run_id=p.id WHERE p.id=new.parent_run_id AND c.id=new.child_run_id AND p.agent_id=new.agent_id AND p.session_id=new.parent_session_id AND c.session_id=new.child_session_id AND p.state='running' AND c.state='queued' AND ps.content_epoch=new.parent_epoch AND ps.deleted_at IS NULL AND cs.session_epoch=new.parent_epoch AND c.settings_revision_id=p.settings_revision_id AND c.model_revision_id=p.model_revision_id AND c.model_permission_epoch=p.model_permission_epoch AND c.policy_epoch=p.policy_epoch) BEGIN SELECT RAISE(ABORT,'delegated source binding is invalid'); END",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
