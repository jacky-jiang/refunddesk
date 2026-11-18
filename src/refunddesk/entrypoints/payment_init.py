"""Initialize payment storage independently of the business database."""
from sqlalchemy import create_engine, text

from refunddesk.settings import payment_database_options, payment_database_schema


def main() -> None:
    url, options = payment_database_options()
    schema = payment_database_schema()
    engine = create_engine(url, connect_args=options, hide_parameters=True)
    try:
        with engine.begin() as conn:
            # Serialize initialization and move an existing pre-schema installation atomically.
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('refunddesk-payment-init'))"))
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            if schema != 'public':
                legacy = conn.scalar(text("SELECT to_regclass('public.payment_refunds')"))
                target = conn.scalar(text('SELECT to_regclass(:name)'),
                                     {'name': f'{schema}.payment_refunds'})
                if legacy and target:
                    raise RuntimeError('Both public.payment_refunds and payment schema table exist; '
                                       'stop payment processes and reconcile them before initialization')
                if legacy:
                    conn.execute(text(f'ALTER TABLE public.payment_refunds SET SCHEMA "{schema}"'))
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
