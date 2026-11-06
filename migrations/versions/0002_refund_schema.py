"""Align the foundation with the supplied RefundDesk SQL schema."""
from importlib.resources import files

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN enabled boolean NOT NULL DEFAULT true")
    op.execute("""ALTER TABLE orders
        ADD COLUMN refund_state text NOT NULL DEFAULT 'none'
            CHECK(refund_state IN ('none','reserved','refunded')),
        ADD CHECK(shipped_at IS NULL OR
            (paid_at IS NOT NULL AND shipped_at>=paid_at AND refund_state='none')),
        ADD CHECK(refund_state='none' OR paid_at IS NOT NULL)""")
    ddl = files("refunddesk").joinpath("schema.sql").read_text()
    for statement in ddl[ddl.index("CREATE TABLE refund_policies"):].split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("0002 downgrade is unsupported; restore a reviewed backup")
