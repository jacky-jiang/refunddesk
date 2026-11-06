from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from refunddesk.settings import database_options, database_schema

url, options = database_options(migration=True)
schema = database_schema()
engine = create_engine(url, connect_args=options, poolclass=NullPool, hide_parameters=True)
try:
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        context.configure(connection=connection, version_table_schema=schema)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
