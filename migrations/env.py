from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from refunddesk.settings import database_options

url, options = database_options(migration=True)
engine = create_engine(url, connect_args=options, poolclass=NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
