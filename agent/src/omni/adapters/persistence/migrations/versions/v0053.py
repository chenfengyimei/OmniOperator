"""Capture the owner's kernel policy, fallback grants and per-call model identity."""

from alembic import op

revision = "v2_0053"
down_revision = "v2_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_route_policies ADD COLUMN kernel JSON NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE run_routes ADD COLUMN kernel JSON NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE model_call_routes ADD COLUMN model_revision_id VARCHAR(36)")


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
