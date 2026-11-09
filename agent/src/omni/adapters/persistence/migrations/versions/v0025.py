"""Portal window frame provenance, retaining scoped references and immutability."""

from alembic import op

revision = "v2_0025"
down_revision = "v2_0024"
branch_labels = None
depends_on = None

TRIGGERS = (
    "CREATE TRIGGER capture_session_live BEFORE INSERT ON session_captures WHEN NOT EXISTS(SELECT 1 FROM sessions WHERE id=new.session_id AND agent_id=new.agent_id AND deleted_at IS NULL AND content_epoch=new.content_epoch) BEGIN SELECT RAISE(ABORT,'capture session changed'); END",
    "CREATE TRIGGER capture_identity BEFORE UPDATE ON session_captures BEGIN SELECT RAISE(ABORT,'capture references are immutable'); END",
    "CREATE TRIGGER capture_session_erased AFTER UPDATE OF deleted_at ON sessions WHEN new.deleted_at IS NOT NULL BEGIN DELETE FROM session_captures WHERE session_id=new.id AND agent_id=new.agent_id; END",
)


def upgrade() -> None:
    for name in ("capture_session_live", "capture_identity", "capture_session_erased"):
        op.execute("DROP TRIGGER " + name)
    with op.batch_alter_table("session_captures", recreate="always") as batch:
        batch.drop_constraint("ck_session_captures_method", type_="check")
        batch.create_check_constraint(
            op.f("ck_session_captures_method"),
            "method IN ('print_window','screen_capture_kit','x11_composite','pipewire_window')",
        )
    for statement in TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")
