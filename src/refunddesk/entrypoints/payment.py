"""Independently persisted, authenticated mock payment service.

Initialize its database with ``python -m refunddesk.entrypoints.payment_init``.
"""
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from refunddesk.settings import payment_database_options


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    token = os.environ.get('PAYMENT_SERVICE_TOKEN', '')
    configured = any(os.getenv(name) for name in ('PAYMENT_DATABASE_URL', 'PAYMENT_DB_HOST',
                                                     'PAYMENT_DB_PASSWORD', 'DB_HOST', 'DB_PASSWORD'))
    if configured:
        url, options = payment_database_options()
        app.state.engine = create_async_engine(url, connect_args=options, pool_pre_ping=True,
                                               hide_parameters=True)
    else:
        app.state.engine = None
    app.state.token = token
    try:
        yield
    finally:
        if app.state.engine is not None:
            await app.state.engine.dispose()


app = FastAPI(title='RefundDesk Mock Payment', lifespan=lifespan)


async def authorize(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    if not request.app.state.token or request.app.state.engine is None:
        raise HTTPException(status_code=503, detail='PAYMENT_NOT_CONFIGURED')
    if not authorization or not hmac.compare_digest(authorization, 'Bearer ' + request.app.state.token):
        raise HTTPException(status_code=401, detail='UNAUTHORIZED')


class RefundIn(BaseModel):
    payment_key: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=128)
    amount_minor: int = Field(gt=0)
    currency: Literal['CNY']


@app.get('/health/live')
async def live() -> dict[str, str]:
    return {'status': 'alive', 'service': 'mock-payment'}


@app.post('/payments/refunds', dependencies=[Depends(authorize)])
async def refund(data: RefundIn, request: Request) -> dict[str, object]:
    values = data.model_dump()
    values['channel_ref'] = str(uuid4())
    mode = os.getenv('PAYMENT_RESULT_MODE', 'succeeded')
    if os.getenv('APP_ENV') not in {'development', 'test'}:
        mode = 'succeeded'
    if mode not in {'succeeded', 'failed', 'pending'}:
        raise HTTPException(status_code=503, detail='PAYMENT_INVALID_CONFIGURATION')
    values['state'] = mode
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text('''INSERT INTO payment_refunds
            (payment_key,tenant_id,order_no,amount_minor,currency,state,channel_ref)
            VALUES (:payment_key,:tenant_id,:order_no,:amount_minor,:currency,:state,:channel_ref)
            ON CONFLICT (payment_key) DO NOTHING'''), values)
        row = (await conn.execute(text('SELECT * FROM payment_refunds WHERE payment_key=:payment_key'),
                                  values)).mappings().one()
        if any(row[key] != values[key] for key in
               ('tenant_id', 'order_no', 'amount_minor', 'currency')):
            raise HTTPException(status_code=409, detail='PAYMENT_KEY_CONFLICT')
        result = dict(row)
    if (os.getenv('APP_ENV') in {'development', 'test'}
            and os.getenv('PAYMENT_FAULT_MODE') == 'drop_response'):
        raise HTTPException(status_code=503, detail='SIMULATED_RESPONSE_LOSS')
    return result


@app.get('/payments/refunds/{payment_key}', dependencies=[Depends(authorize)])
async def lookup(payment_key: str, request: Request) -> dict[str, object]:
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text('SELECT * FROM payment_refunds WHERE payment_key=:key'),
                                  {'key': payment_key})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    return dict(row)
