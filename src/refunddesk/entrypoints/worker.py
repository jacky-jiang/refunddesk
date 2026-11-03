import argparse
import asyncio
import signal

from refunddesk.db import check_schema, open_engine
from refunddesk.events import event


async def run(check: bool) -> None:
    engine = open_engine()
    try:
        await check_schema(engine)
        event("worker.ready")
        if not check:
            stop = asyncio.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            await stop.wait()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify schema and exit")
    asyncio.run(run(parser.parse_args().check))
