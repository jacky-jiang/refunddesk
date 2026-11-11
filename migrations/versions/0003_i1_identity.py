"""Add credentials, revocable server sessions and independent security events."""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''CREATE TABLE user_credentials (
        user_id text PRIMARY KEY REFERENCES users(id), password_hash text NOT NULL
    )''')
    op.execute('''CREATE TABLE auth_sessions (
        token_hash text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id),
        tenant_id text NOT NULL REFERENCES tenants(id), csrf_hash text NOT NULL,
        expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL
    )''')
    op.execute('CREATE INDEX sessions_user ON auth_sessions(user_id)')
    op.execute('''CREATE TABLE security_events (
        id text PRIMARY KEY, occurred_at timestamptz NOT NULL,
        actor text, tenant_id text, code text NOT NULL, trace_id text NOT NULL
    )''')
    op.execute('CREATE INDEX security_events_time ON security_events(occurred_at)')


def downgrade() -> None:
    raise RuntimeError('Identity rollback requires reviewed backup/forward repair; no destructive downgrade')
