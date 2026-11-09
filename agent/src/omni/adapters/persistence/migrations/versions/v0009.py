"""Instance-wide credential intent and outcome audit; no arbitrary Agent attribution."""

from alembic import op

revision = "v2_0009"
down_revision = "v2_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE instance_audit (
      sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      id VARCHAR(36) NOT NULL UNIQUE,
      created_at VARCHAR(40) NOT NULL,
      owner_id VARCHAR(36) NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
      operation_id VARCHAR(36) NOT NULL,
      kind VARCHAR(40) NOT NULL,
      outcome VARCHAR(20) NOT NULL,
      data JSON NOT NULL,
      UNIQUE(operation_id,kind),
      CHECK(length(id)=36 AND length(operation_id)=36),
      CHECK((kind='credential.requested' AND outcome='requested') OR (kind='credential.resolved' AND outcome IN ('applied','not_applied','unknown')))
    )
    """)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
