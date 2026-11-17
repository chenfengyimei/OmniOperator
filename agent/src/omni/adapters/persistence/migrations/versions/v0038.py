"""Model custom request headers (e.g. OpenCode Go session routing); frozen SQL, no current schema imports."""

from alembic import op

revision = "v2_0038"
down_revision = "v2_0037"
branch_labels = None
depends_on = None

_ADD = (
    "ALTER TABLE model_options ADD COLUMN extra_headers JSON NOT NULL DEFAULT '{}' "
    "CONSTRAINT ck_model_options_headers CHECK (json_valid(extra_headers) AND json_type(extra_headers)='object')"
)


def upgrade() -> None:
    # Idempotent: a frozen pre-v38 baseline may already carry the column when a test
    # seeds it with the current repository; adding it twice must not fail the upgrade.
    bind = op.get_bind()
    cols = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(model_options)")}
    if "extra_headers" not in cols:
        op.execute(_ADD)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
