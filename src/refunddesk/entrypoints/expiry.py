import argparse
import asyncio

from refunddesk.application.services import RefundService
from refunddesk.db import check_schema, open_engine
from refunddesk.infrastructure.identity import SystemClock
from refunddesk.infrastructure.storage import SqlUnitOfWork
from refunddesk.workers.expiry import run


async def main(once: bool) -> None:
    engine = open_engine()
    try:
        await check_schema(engine)
        await run(RefundService(lambda: SqlUnitOfWork(engine), SystemClock()), once)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Close expired I1 requests; never execute payments')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(main(args.once))
    except KeyboardInterrupt:
        pass
