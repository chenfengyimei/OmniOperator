"""Owner run retention pins, independent of run execution/configuration revisions."""

from alembic import op

revision = "v2_0011"
down_revision = "v2_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE runs ADD COLUMN retention_pinned BOOLEAN NOT NULL DEFAULT 0 CHECK(retention_pinned IN (0,1))"
    )
    op.execute("ALTER TABLE runs ADD COLUMN retention_revision INTEGER NOT NULL DEFAULT 1 CHECK(retention_revision>0)")
    op.execute("CREATE INDEX ix_runs_retention ON runs(ended_at,id)")
    op.execute(
        "CREATE TRIGGER run_retention_progress BEFORE UPDATE OF retention_pinned,retention_revision ON runs WHEN new.retention_revision<=old.retention_revision BEGIN SELECT RAISE(ABORT,'retention preference requires a new revision'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
