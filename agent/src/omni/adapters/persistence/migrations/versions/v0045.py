"""Heal actions.started_at for databases whose earlier add-column did not land; idempotent."""

from alembic import op

revision = "v2_0045"
down_revision = "v2_0044"
branch_labels = None
depends_on = None

_ADD = "ALTER TABLE actions ADD COLUMN started_at VARCHAR(40)"


def upgrade() -> None:
    # Some already-stamped databases reached head without the v0041 add-column taking
    # effect (branch reorder during parallel development). Re-assert the column here so
    # settle_action's started_at write never hits "no such column". Idempotent.
    bind = op.get_bind()
    cols = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(actions)")}
    if "started_at" not in cols:
        op.execute(_ADD)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
