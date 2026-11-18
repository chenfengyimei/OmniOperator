"""Provider-reported prompt cache usage on model calls; frozen SQL, idempotent add-column."""

from alembic import op

revision = "v2_0040"
down_revision = "v2_0039"
branch_labels = None
depends_on = None

_ADD = (
    "ALTER TABLE model_calls ADD COLUMN cached_prompt_tokens INTEGER "
    "CONSTRAINT ck_model_calls_cached_tokens CHECK (cached_prompt_tokens IS NULL OR cached_prompt_tokens>=0)",
    "ALTER TABLE model_calls ADD COLUMN cache_creation_tokens INTEGER "
    "CONSTRAINT ck_model_calls_cache_created_tokens CHECK (cache_creation_tokens IS NULL OR cache_creation_tokens>=0)",
)


def upgrade() -> None:
    # Idempotent: a frozen pre-v40 baseline may already carry the columns when a test
    # seeds it with the current repository; adding them twice must not fail the upgrade.
    bind = op.get_bind()
    cols = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(model_calls)")}
    for statement, column in zip(_ADD, ("cached_prompt_tokens", "cache_creation_tokens")):
        if column not in cols:
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a validated snapshot into a new home instead of downgrading")
