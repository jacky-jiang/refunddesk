"""Track lease generations and reconciliation budget."""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE jobs ADD COLUMN lease_generation bigint NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE jobs ADD COLUMN manual_query boolean NOT NULL DEFAULT false')
    op.execute('''CREATE TABLE manual_reconcile_keys (
        tenant_id text NOT NULL, user_id text NOT NULL, key text NOT NULL, request_id text NOT NULL,
        PRIMARY KEY (tenant_id,user_id,key),
        FOREIGN KEY (tenant_id,request_id) REFERENCES refund_requests(tenant_id,id))''')
    op.execute('ALTER TABLE jobs ADD COLUMN reconcile_started_at timestamptz')
    op.execute('ALTER TABLE jobs ADD COLUMN reconcile_attempts integer NOT NULL DEFAULT 0')
    op.execute("CREATE INDEX jobs_expired_lease ON jobs(lease_until) WHERE state='leased'")


def downgrade() -> None:
    raise RuntimeError('Execution rollback requires a reviewed recovery plan')
