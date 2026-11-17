import asyncio
from uuid import uuid4

from refunddesk.application.services import RefundService


async def run(service: RefundService, once: bool, interval: float = 30) -> None:
    while True:
        await service.sweep(str(uuid4()))
        if once:
            return
        await asyncio.sleep(interval)
