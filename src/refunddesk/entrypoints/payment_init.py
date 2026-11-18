"""Initialize the separate mock payment database, independently of business migrations."""
import os

from sqlalchemy import create_engine, text


def main() -> None:
    url = os.environ.get('PAYMENT_DATABASE_URL', '')
    if not url.startswith('postgresql+psycopg://'):
        raise RuntimeError('PAYMENT_DATABASE_URL is required')
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as conn:
            conn.execute(text('''CREATE TABLE IF NOT EXISTS payment_refunds (
                payment_key text PRIMARY KEY, tenant_id text NOT NULL, order_no text NOT NULL,
                amount_minor bigint NOT NULL CHECK (amount_minor > 0),
                currency text NOT NULL CHECK (currency = 'CNY'),
                state text NOT NULL CHECK (state IN ('pending','succeeded','failed')),
                channel_ref text, created_at timestamptz NOT NULL DEFAULT now()
            )'''))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
