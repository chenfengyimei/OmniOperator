"""Persist an explicit owner selection independently of historical Router admissions."""

from alembic import op

revision = "v2_0057"
down_revision = "v2_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE owa_threads ADD COLUMN main_selected_at VARCHAR(40)")
    op.execute(
        "CREATE UNIQUE INDEX uq_owa_threads_selected_main ON owa_threads(agent_id) WHERE main_selected_at IS NOT NULL"
    )
    op.execute(
        """CREATE TRIGGER owa_main_session_select BEFORE UPDATE OF main_selected_at ON owa_threads
        WHEN new.main_selected_at IS NOT NULL AND (
            NOT EXISTS (SELECT 1 FROM sessions s JOIN instances i ON i.slot=1
                        WHERE s.id=new.main_session_id AND s.agent_id=new.agent_id
                          AND s.deleted_at IS NULL AND i.owner_id=new.owner_id)
            OR EXISTS (SELECT 1 FROM run_delegations d WHERE d.child_session_id=new.main_session_id)
            OR EXISTS (SELECT 1 FROM qq_bindings q WHERE q.session_id=new.main_session_id)
        )
        BEGIN SELECT RAISE(ABORT, 'invalid OWA main session selection'); END"""
    )


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
