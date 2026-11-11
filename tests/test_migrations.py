from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from refunddesk.entrypoints.adopt_schema import adopt, signature
from refunddesk.settings import database_options


@pytest.fixture
def isolated_schema(monkeypatch):
    schema = f"rd_test_{uuid4().hex}"
    monkeypatch.setenv('DB_SCHEMA', schema)
    url, options = database_options(migration=True)
    engine = create_engine(url, connect_args=options)
    try:
        yield engine, schema
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def manual_schema(engine, schema):
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET LOCAL search_path = "{schema}", pg_catalog'))
        for statement in files('refunddesk').joinpath('schema.sql').read_text().split(';'):
            if statement.strip():
                conn.execute(text(statement))
        conn.execute(text("INSERT INTO tenants VALUES ('existing','Preserve me')"))


def test_adopt_preserves_data_and_repeats(isolated_schema):
    engine, schema = isolated_schema
    manual_schema(engine, schema)
    adopt()
    adopt()
    command.upgrade(Config('alembic.ini'), 'head')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT name FROM tenants')) == 'Preserve me'
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0003'


def test_adopt_rejects_constraint_drift(isolated_schema):
    engine, schema = isolated_schema
    manual_schema(engine, schema)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE orders DROP CONSTRAINT orders_amount_minor_check'))
    with pytest.raises(RuntimeError, match='orders'):
        adopt()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT to_regclass('alembic_version')")) is None
        assert conn.scalar(text('SELECT count(*) FROM tenants')) == 1


def test_upgrade_from_foundation_matches_manual_schema(isolated_schema):
    engine, schema = isolated_schema
    command.upgrade(Config('alembic.ini'), '0001')
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES ('existing','Preserve me')"))
    command.upgrade(Config('alembic.ini'), '0002')
    # Adoption compares every column, constraint and non-constraint index to the SQL.
    adopt()
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT name FROM tenants')) == 'Preserve me'
        assert signature(conn, schema)


def test_adopt_preserves_complete_demo_batch(isolated_schema):
    engine, schema = isolated_schema
    manual_schema(engine, schema)
    sql = Path(__file__).with_name('fixtures').joinpath('helloagents_seed.sql').read_text()
    # Keep the supplied fixture unchanged; target only this test's isolated schema/database.
    sql = sql.replace("'helloagents'", 'current_database()').replace('refunddesk', schema)
    sql = sql.replace('\nBEGIN;\n', '\n', 1).replace('\nCOMMIT;', '')
    with engine.begin() as conn:
        conn.exec_driver_sql(sql, execution_options={"no_parameters": True})
        assert conn.scalar(text('SELECT count(*) FROM orders')) == 200
        assert conn.scalar(text('SELECT count(*) FROM refund_requests')) == 10
        assert conn.scalar(text('SELECT count(*) FROM audit_events')) == 26
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"),
                              {'schema': schema}).scalars().all()

    def snapshot():
        with engine.connect() as conn:
            return {table: conn.scalar(text(
                f'SELECT jsonb_agg(row_data ORDER BY row_data::text) '
                f'FROM (SELECT to_jsonb(t) AS row_data FROM "{schema}"."{table}" t) s'
            )) for table in tables}

    before = snapshot()
    adopt()
    adopt()
    command.upgrade(Config('alembic.ini'), 'head')
    assert snapshot() == before
    # Repeating the original batch must fail atomically, without changing existing rows.
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.exec_driver_sql(sql, execution_options={"no_parameters": True})
    assert snapshot() == before
