import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from refunddesk.db import open_engine


async def seed() -> None:
    if os.getenv("APP_ENV") != "development":
        raise SystemExit("Seed requires APP_ENV=development")
    engine = open_engine()
    try:
        async with engine.begin() as conn:
            if await conn.scalar(text("SELECT current_database()")) != "refunddesk_dev":
                raise SystemExit("Seed allowed only in refunddesk_dev")
            now = datetime.now(UTC)
            for tenant in ("acme", "beta"):
                await conn.execute(text("INSERT INTO tenants VALUES (:t,:t) ON CONFLICT DO NOTHING"),
                                   {"t": tenant})
                for role in ("support", "supervisor", "auditor"):
                    user = f"{tenant}-{role}"
                    await conn.execute(text("INSERT INTO users VALUES (:u,:u) ON CONFLICT DO NOTHING"),
                                       {"u": user})
                    await conn.execute(text("INSERT INTO memberships VALUES (:t,:u,:r) ON CONFLICT DO NOTHING"),
                                       {"t": tenant, "u": user, "r": role})
                for no, paid, shipped in [
                    ("DEMO-1001", now, None), ("DEMO-1002", now, now),
                    ("DEMO-1003", None, None), ("DEMO-1004", now - timedelta(days=8), None),
                ]:
                    await conn.execute(text("""
                        INSERT INTO orders (tenant_id,order_no,amount_minor,currency,paid_at,shipped_at)
                        VALUES (:t,:n,12500,'CNY',:paid,:shipped) ON CONFLICT DO NOTHING
                    """), {"t": tenant, "n": no, "paid": paid, "shipped": shipped})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
