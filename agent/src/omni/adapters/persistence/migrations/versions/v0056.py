"""Give OWA and legacy delegation separate limits while preserving source bindings."""

from alembic import op

revision = "v2_0056"
down_revision = "v2_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER run_delegations_binding")
    op.execute(
        """CREATE TRIGGER run_delegations_binding BEFORE INSERT ON run_delegations
        WHEN EXISTS (SELECT 1 FROM run_delegations WHERE child_run_id=new.parent_run_id)
          OR (SELECT count(*) FROM run_delegations WHERE parent_run_id=new.parent_run_id) >=
             CASE WHEN EXISTS (
                SELECT 1 FROM owa_tickets t
                JOIN owa_executions e ON e.ticket_id=t.id AND e.agent_id=t.agent_id
                JOIN model_call_route_tools mt ON mt.snapshot_id=new.source_snapshot_id AND mt.name='owa_plan'
                WHERE t.run_id=new.parent_run_id AND t.agent_id=new.agent_id
                  AND e.plan_sha256 IS NOT NULL AND e.cancelled_at IS NULL
             ) THEN 8 ELSE 4 END
          OR NOT EXISTS (
              SELECT 1 FROM runs p
              JOIN runs c ON c.parent_id=p.id AND c.agent_id=p.agent_id
              JOIN sessions ps ON ps.id=p.session_id AND ps.agent_id=p.agent_id
              JOIN context_snapshots cs ON cs.id=new.parent_snapshot_id AND cs.run_id=p.id AND cs.agent_id=p.agent_id
              JOIN model_call_routes mr ON mr.id=new.source_snapshot_id AND mr.agent_id=p.agent_id
              JOIN model_calls mc ON mc.id=mr.call_id AND mc.run_id=p.id
              WHERE p.id=new.parent_run_id AND c.id=new.child_run_id AND p.agent_id=new.agent_id
                AND p.session_id=new.parent_session_id AND c.session_id=new.child_session_id
                AND p.state='running' AND c.state='queued' AND ps.content_epoch=new.parent_epoch
                AND ps.deleted_at IS NULL AND cs.session_epoch=new.parent_epoch
                AND c.settings_revision_id=p.settings_revision_id AND c.model_revision_id=p.model_revision_id
                AND c.model_permission_epoch=p.model_permission_epoch AND c.policy_epoch=p.policy_epoch
          )
        BEGIN SELECT RAISE(ABORT,'delegated source binding is invalid'); END"""
    )


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
