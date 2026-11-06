"""Validate manually created tables before recording Alembic revision 0002."""
from importlib.resources import files
from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.pool import NullPool

from refunddesk.settings import database_options, database_schema


def signature(connection: Connection, schema: str) -> list[tuple[str, ...]]:
    """Compare PostgreSQL's canonical definitions, excluding generated object names."""
    rows = connection.execute(text("""
        SELECT 'column', c.relname, a.attname,
               format_type(a.atttypid, a.atttypmod), a.attnotnull::text,
               COALESCE(pg_get_expr(d.adbin, d.adrelid), '')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        WHERE n.nspname=:schema AND c.relkind='r' AND c.relname<>'alembic_version'
        UNION ALL
        SELECT 'constraint', c.relname, k.contype::text,
               pg_get_constraintdef(k.oid), k.convalidated::text, ''
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relname<>'alembic_version'
        UNION ALL
        SELECT 'index', c.relname, i.indisunique::text,
               pg_get_indexdef(i.indexrelid, 0, false), i.indisvalid::text, ''
        FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relname<>'alembic_version'
          AND NOT EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conindid=i.indexrelid)
    """), {"schema": schema})
    result = []
    for row in rows:
        values = tuple(str(value).replace(f'{schema}.', '').replace(f'"{schema}".', '')
                       for value in row)
        if values[0] == 'index':
            # Ignore the index's name, retain its entire definition after ON.
            values = (*values[:3], values[3].split(' ON ', 1)[1], *values[4:])
        result.append(values)
    return sorted(result)


def adopt() -> None:
    schema = database_schema()
    url, options = database_options(migration=True)
    engine = create_engine(url, connect_args=options, poolclass=NullPool, hide_parameters=True)
    reference = f"rd_check_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(84719, hashtext(:schema))"),
                               {"schema": schema})
            connection.execute(text("SET LOCAL search_path = pg_catalog"))
            tables = connection.execute(text("""SELECT tablename FROM pg_tables
                WHERE schemaname=:schema AND tablename<>'alembic_version'"""),
                {"schema": schema}).scalars().all()
            if not tables:
                raise RuntimeError("No existing tables found; use alembic upgrade head for a new DB")
            for table in tables:
                quoted = connection.dialect.identifier_preparer.quote_identifier(table)
                connection.execute(text(f'LOCK TABLE "{schema}".{quoted} IN ACCESS SHARE MODE'))
            connection.execute(text(f'CREATE SCHEMA "{reference}"'))
            connection.execute(text(f'SET LOCAL search_path = "{reference}", pg_catalog'))
            for statement in files("refunddesk").joinpath("schema.sql").read_text().split(';'):
                if statement.strip():
                    connection.execute(text(statement))
            connection.execute(text("SET LOCAL search_path = pg_catalog"))
            expected = signature(connection, reference)
            actual = signature(connection, schema)
            if actual != expected:
                differing = sorted({row[1] for row in set(actual) ^ set(expected)})
                raise RuntimeError("Schema mismatch; no version recorded. Check tables: "
                                   + ", ".join(differing))
            connection.execute(text(f'DROP SCHEMA "{reference}" CASCADE'))
            migration = MigrationContext.configure(connection, opts={"version_table_schema": schema})
            if migration.get_current_heads() not in {(), ('0001',), ('0002',)}:
                raise RuntimeError("Unexpected existing migration version; refusing to overwrite it")
            migration.stamp(ScriptDirectory.from_config(Config('alembic.ini')), '0002')
        print("Schema verified; Alembic revision 0002 recorded. Business data unchanged.")
    finally:
        engine.dispose()


if __name__ == '__main__':
    adopt()
