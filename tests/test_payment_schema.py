"""Schema adoption tests run only against an explicitly disposable payment database."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from refunddesk.entrypoints.payment_init import main
from refunddesk.settings import payment_database_options


def test_payment_init_moves_legacy_records_and_refuses_two_tables(monkeypatch):
    url = os.getenv('PAYMENT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PAYMENT_TEST_DATABASE_URL must identify a disposable database')
    schema = 'pay_test_' + uuid4().hex
    monkeypatch.setenv('PAYMENT_DATABASE_URL', url)
    monkeypatch.setenv('PAYMENT_DB_SCHEMA', schema)
    url, options = payment_database_options()
    engine = create_engine(url, connect_args=options)
    created_legacy = False
    try:
        with engine.begin() as conn:
            if conn.scalar(text("SELECT to_regclass('public.payment_refunds')")):
                pytest.skip('public.payment_refunds already exists')
            created_legacy = True
            conn.execute(text('''CREATE TABLE public.payment_refunds (
                payment_key text PRIMARY KEY, tenant_id text NOT NULL, order_no text NOT NULL,
                amount_minor bigint NOT NULL, currency text NOT NULL, state text NOT NULL,
                channel_ref text, created_at timestamptz NOT NULL DEFAULT now())'''))
            conn.execute(text("""INSERT INTO public.payment_refunds
                (payment_key,tenant_id,order_no,amount_minor,currency,state)
                VALUES ('old-key','tenant','order',100,'CNY','succeeded')"""))
        main()
        main()
        with engine.begin() as conn:
            assert conn.scalar(text(f'SELECT count(*) FROM "{schema}".payment_refunds')) == 1
            assert conn.scalar(text("SELECT to_regclass('public.payment_refunds')")) is None
            conn.execute(text(f'CREATE TABLE public.payment_refunds (LIKE "{schema}".payment_refunds)'))
        with pytest.raises(RuntimeError, match='Both public'):
            main()
        with engine.connect() as conn:
            assert conn.scalar(text(f'SELECT count(*) FROM "{schema}".payment_refunds')) == 1
    finally:
        with engine.begin() as conn:
            if created_legacy:
                conn.execute(text('DROP TABLE IF EXISTS public.payment_refunds'))
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
