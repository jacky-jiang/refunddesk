"""CI-only checks, run after two calls to seed in a fresh disposable database."""
import asyncio

from sqlalchemy import text

from refunddesk.db import open_engine


async def verify() -> None:
    engine = open_engine()
    try:
        async with engine.connect() as conn:
            for table, expected in (("tenants", 2), ("users", 6), ("memberships", 6), ("orders", 8)):
                assert await conn.scalar(text(f"SELECT count(*) FROM {table}")) == expected
            assert await conn.scalar(text("SELECT count(*) FROM orders WHERE order_no='DEMO-1001'")) == 2
    finally:
        await engine.dispose()


asyncio.run(verify())
