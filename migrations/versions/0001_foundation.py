"""Initial tenant, user, membership and order schema."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE tenants (id text PRIMARY KEY, name text NOT NULL)")
    op.execute("CREATE TABLE users (id text PRIMARY KEY, display_name text NOT NULL)")
    op.execute("""
        CREATE TABLE memberships (
            tenant_id text REFERENCES tenants(id), user_id text REFERENCES users(id),
            role text CHECK (role IN ('support','supervisor','auditor')),
            PRIMARY KEY (tenant_id,user_id,role)
        )
    """)
    op.execute("""
        CREATE TABLE orders (
            tenant_id text REFERENCES tenants(id), order_no text NOT NULL,
            amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
            currency text NOT NULL CHECK (currency = 'CNY'),
            paid_at timestamptz, shipped_at timestamptz, PRIMARY KEY (tenant_id,order_no)
        )
    """)


def downgrade() -> None:
    for table in ("orders", "memberships", "users", "tenants"):
        op.drop_table(table)
