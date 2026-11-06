from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from refunddesk.settings import database_options


def open_engine() -> AsyncEngine:
    url, options = database_options()
    return create_async_engine(url, connect_args=options, pool_size=2, max_overflow=1,
                               pool_pre_ping=True, echo=False, hide_parameters=True)


async def check_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0002":
            raise RuntimeError("Unexpected database revision")
