import argparse
import asyncio

from sqlalchemy.exc import SQLAlchemyError

from refunddesk.db import check_schema, open_engine
from refunddesk.events import event
from refunddesk.workers.execution import Executor


async def run(check: bool, once: bool = False) -> None:
    engine = open_engine()
    try:
        await check_schema(engine)
        event('worker.ready')
        if check:
            return
        executor = Executor(engine)
        while True:
            try:
                worked = await executor.once()
            except (SQLAlchemyError, OSError):
                event('worker.database_unavailable')
                if once:
                    raise
                await asyncio.sleep(5)
                continue
            if once:
                return
            if not worked:
                await asyncio.sleep(1)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='verify schema and exit')
    parser.add_argument('--once', action='store_true', help='process at most one job')
    args = parser.parse_args()
    asyncio.run(run(args.check, args.once))
