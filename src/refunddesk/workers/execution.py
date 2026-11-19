"""Durable execution loop. Payment I/O happens outside business transactions."""
import asyncio
import os
from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

import httpx
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from refunddesk.application.services import payload_digest


class JobRow(TypedDict):
    tenant_id: str
    id: str
    request_id: str
    lease_generation: int
    manual_query: bool


class OrderRow(TypedDict):
    order_no: str
    paid_at: datetime | None
    shipped_at: datetime | None
    refund_state: str
    amount_minor: int
    currency: str


class RequestRow(TypedDict):
    state: str
    start_by: datetime | None
    order_no: str
    amount_minor: int
    currency: str


class LeaseRow(TypedDict):
    manual_query: bool
    reconcile_attempts: int
    timed_out: bool | None


class OperationRow(TypedDict):
    payment_key: str


class PaymentRequest(TypedDict):
    payment_key: str
    tenant_id: str
    order_no: str
    amount_minor: int
    currency: str


class PaymentResult(PaymentRequest):
    state: Literal['pending', 'succeeded', 'failed']
    channel_ref: NotRequired[str | None]


JOB_ADAPTER = TypeAdapter(JobRow)
ORDER_ADAPTER = TypeAdapter(OrderRow)
REQUEST_ADAPTER = TypeAdapter(RequestRow)
LEASE_ADAPTER = TypeAdapter(LeaseRow)
OPERATION_ADAPTER = TypeAdapter(OperationRow)
PAYMENT_ADAPTER = TypeAdapter(PaymentResult)


def settings() -> tuple[str, str, int, int, float]:
    url, token = os.getenv('PAYMENT_BASE_URL', ''), os.getenv('PAYMENT_SERVICE_TOKEN', '')
    lease = int(os.getenv('WORKER_LEASE_SECONDS', '30'))
    renew = int(os.getenv('WORKER_RENEW_SECONDS', '10'))
    timeout = float(os.getenv('PAYMENT_TIMEOUT_SECONDS', '5'))
    if not url or not token or lease <= 0 or renew <= 0 or renew >= lease or timeout <= 0:
        raise ValueError('Invalid payment or worker configuration')
    return url.rstrip('/'), token, lease, renew, timeout


async def audit(conn: AsyncConnection, job: JobRow, action: str, details: str = '{}') -> None:
    await conn.execute(text('''INSERT INTO audit_events
        (tenant_id,id,occurred_at,actor,request_id,action,details,trace_id)
        VALUES (:tenant_id,:audit,now(),'system:worker',:request_id,:action,CAST(:details AS jsonb),:trace)'''),
        {**job, 'audit': str(uuid4()), 'action': action, 'details': details, 'trace': str(uuid4())})


class Executor:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.url, self.token, self.lease, self.renew, self.timeout = settings()
        self.owner = str(uuid4())

    async def claim(self) -> JobRow | None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(text('''WITH candidate AS (
                SELECT tenant_id,id FROM jobs WHERE
                (state='ready' AND run_at<=now()) OR (state='leased' AND lease_until<=now())
                ORDER BY run_at,tenant_id,id FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE jobs j SET state='leased',lease_owner=:owner,
                lease_until=now()+make_interval(secs => :lease),lease_generation=lease_generation+1,
                attempts=attempts+1 FROM candidate c
                WHERE j.tenant_id=c.tenant_id AND j.id=c.id RETURNING j.*'''),
                {'owner': self.owner, 'lease': self.lease})).mappings().first()
            return JOB_ADAPTER.validate_python(dict(row), strict=True) if row else None

    async def prepare(self, job: JobRow) -> PaymentRequest | None:
        async with self.engine.begin() as conn:
            params = {'t': job['tenant_id'], 'r': job['request_id'], 'j': job['id'],
                      'owner': self.owner, 'generation': job['lease_generation']}
            order_raw = (await conn.execute(text('''SELECT o.* FROM orders o JOIN refund_requests r
                ON r.tenant_id=o.tenant_id AND r.order_no=o.order_no
                WHERE r.tenant_id=:t AND r.id=:r FOR UPDATE OF o'''), params)).mappings().one()
            order = ORDER_ADAPTER.validate_python(dict(order_raw), strict=True)
            req_raw = (await conn.execute(text('SELECT * FROM refund_requests WHERE tenant_id=:t AND id=:r FOR UPDATE'),
                                          params)).mappings().one()
            req = REQUEST_ADAPTER.validate_python(dict(req_raw), strict=True)
            owned_raw = (await conn.execute(text('''SELECT *, (reconcile_started_at <= now() - interval '15 minutes') AS timed_out FROM jobs WHERE tenant_id=:t AND id=:j
                AND state='leased' AND lease_owner=:owner AND lease_generation=:generation
                AND lease_until>now() FOR UPDATE'''), params)).mappings().first()
            if not owned_raw:
                return None
            owned = LEASE_ADAPTER.validate_python(dict(owned_raw), strict=True)
            if req['state'] == 'reconciling' and not owned['manual_query'] and (
                    owned['reconcile_attempts'] >= 8 or owned['timed_out']):
                await conn.execute(text("UPDATE refund_requests SET state='manual' WHERE tenant_id=:t AND id=:r"), params)
                await conn.execute(text("UPDATE jobs SET state='manual',lease_owner=NULL,lease_until=NULL WHERE tenant_id=:t AND id=:j"), params)
                await audit(conn, job, 'manual_required')
                return None
            if req['state'] == 'approved':
                valid = (await conn.execute(text('''SELECT 1 FROM refund_decisions WHERE tenant_id=:t
                    AND request_id=:r AND decision='approve' '''), params)).first()
                expired = (await conn.execute(text('SELECT :deadline <= now()'),
                                              {'deadline': req['start_by']})).scalar()
                if (not valid or expired or order['paid_at'] is None or order['shipped_at'] is not None
                        or order['refund_state'] != 'none' or order['amount_minor'] != req['amount_minor']
                        or order['currency'] != req['currency']):
                    reason = 'EXECUTION_EXPIRED' if expired else 'EXECUTION_CONDITIONS_FAILED'
                    await conn.execute(text("UPDATE refund_requests SET state='closed',close_reason=:reason WHERE tenant_id=:t AND id=:r"), {**params, 'reason': reason})
                    await conn.execute(text("UPDATE jobs SET state='done',lease_owner=NULL,lease_until=NULL WHERE tenant_id=:t AND id=:j"), params)
                    await audit(conn, job, 'execution_blocked')
                    return None
                key = str(uuid4())
                payload: PaymentRequest = {'payment_key': key, 'tenant_id': job['tenant_id'],
                           'order_no': req['order_no'], 'amount_minor': req['amount_minor'],
                           'currency': req['currency']}
                await conn.execute(text("UPDATE orders SET refund_state='reserved' WHERE tenant_id=:t AND order_no=:n"), {**params, 'n': req['order_no']})
                await conn.execute(text('''INSERT INTO refund_operations
                    (tenant_id,id,request_id,payment_key,payload_digest,state,created_at)
                    VALUES (:t,:id,:r,:key,:digest,'prepared',now())'''),
                    {**params, 'id': str(uuid4()), 'key': key, 'digest': payload_digest(dict(payload))})
                await conn.execute(text("UPDATE refund_requests SET state='executing' WHERE tenant_id=:t AND id=:r"), params)
                await audit(conn, job, 'execution_prepared')
            elif req['state'] not in ('executing', 'reconciling'):
                await conn.execute(text("UPDATE jobs SET state='done',lease_owner=NULL,lease_until=NULL WHERE tenant_id=:t AND id=:j"), params)
                return None
            operation_raw = (await conn.execute(text('SELECT * FROM refund_operations WHERE tenant_id=:t AND request_id=:r'), params)).mappings().one()
            operation = OPERATION_ADAPTER.validate_python(dict(operation_raw), strict=True)
            return {'payment_key': operation['payment_key'], 'tenant_id': job['tenant_id'],
                    'order_no': req['order_no'], 'amount_minor': req['amount_minor'],
                    'currency': req['currency']}

    async def heartbeat(self, job: JobRow) -> None:
        while True:
            await asyncio.sleep(self.renew)
            async with self.engine.begin() as conn:
                result = await conn.execute(text('''UPDATE jobs SET lease_until=now()+make_interval(secs => :lease)
                    WHERE tenant_id=:t AND id=:id AND state='leased' AND lease_owner=:owner
                    AND lease_generation=:generation AND lease_until>now()'''),
                    {'t': job['tenant_id'], 'id': job['id'], 'owner': self.owner,
                     'generation': job['lease_generation'], 'lease': self.lease})
                if result.rowcount != 1:
                    return

    async def payment(self, payload: PaymentRequest, manual: bool) -> PaymentResult | None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers={'Authorization': 'Bearer ' + self.token}) as client:
                response = await client.get(self.url + '/payments/refunds/' + payload['payment_key'])
                if response.status_code == 404 and not manual:
                    response = await client.post(self.url + '/payments/refunds', json=payload)
                response.raise_for_status()
                data = PAYMENT_ADAPTER.validate_python(response.json(), strict=True)
                received: PaymentRequest = {
                    'payment_key': data['payment_key'], 'tenant_id': data['tenant_id'],
                    'order_no': data['order_no'], 'amount_minor': data['amount_minor'],
                    'currency': data['currency'],
                }
                if received != payload:
                    return None
                return data
        except (httpx.HTTPError, ValueError, ValidationError):
            return None

    async def finish(self, job: JobRow, result: PaymentResult | None) -> None:
        async with self.engine.begin() as conn:
            p = {'t': job['tenant_id'], 'r': job['request_id'], 'j': job['id'],
                 'owner': self.owner, 'generation': job['lease_generation']}
            order_raw = (await conn.execute(text('''SELECT o.* FROM orders o JOIN refund_requests r
                ON r.tenant_id=o.tenant_id AND r.order_no=o.order_no
                WHERE r.tenant_id=:t AND r.id=:r FOR UPDATE OF o'''), p)).mappings().one()
            order = ORDER_ADAPTER.validate_python(dict(order_raw), strict=True)
            req_raw = (await conn.execute(text('SELECT * FROM refund_requests WHERE tenant_id=:t AND id=:r FOR UPDATE'), p)).mappings().one()
            req = REQUEST_ADAPTER.validate_python(dict(req_raw), strict=True)
            owned = (await conn.execute(text('''SELECT * FROM jobs WHERE tenant_id=:t AND id=:j
                AND state='leased' AND lease_owner=:owner AND lease_generation=:generation
                AND lease_until>now() FOR UPDATE'''), p)).mappings().first()
            if not owned or req['state'] not in ('executing', 'reconciling'):
                return
            manual_query = JOB_ADAPTER.validate_python(dict(owned), strict=True)['manual_query']
            state = result.get('state') if result else None
            if state in ('succeeded', 'failed'):
                await conn.execute(text('UPDATE refund_operations SET state=:state,channel_ref=:ref WHERE tenant_id=:t AND request_id=:r AND state<>\'succeeded\''), {**p, 'state': state, 'ref': result.get('channel_ref') if result else None})
                await conn.execute(text('UPDATE refund_requests SET state=:state WHERE tenant_id=:t AND id=:r'), {**p, 'state': state})
                await conn.execute(text('UPDATE orders SET refund_state=:state WHERE tenant_id=:t AND order_no=:n'), {**p, 'n': order['order_no'], 'state': 'refunded' if state == 'succeeded' else 'none'})
                await conn.execute(text("UPDATE jobs SET state='done',lease_owner=NULL,lease_until=NULL WHERE tenant_id=:t AND id=:j"), p)
                await audit(conn, job, 'payment_' + state)
            else:
                await conn.execute(text("UPDATE refund_operations SET state='unknown' WHERE tenant_id=:t AND request_id=:r AND state<>'succeeded'"), p)
                # Both attempt and elapsed-time budgets survive process restarts.
                await conn.execute(text('''UPDATE jobs SET reconcile_started_at=COALESCE(reconcile_started_at,now()),
                    reconcile_attempts=reconcile_attempts+1 WHERE tenant_id=:t AND id=:j'''), p)
                budget = manual_query or (await conn.execute(text('''SELECT reconcile_attempts>=8 OR
                    reconcile_started_at<=now()-interval '15 minutes' FROM jobs WHERE tenant_id=:t AND id=:j'''), p)).scalar()
                await conn.execute(text('UPDATE refund_requests SET state=:state WHERE tenant_id=:t AND id=:r'), {**p, 'state': 'manual' if budget else 'reconciling'})
                await conn.execute(text('''UPDATE jobs SET state=:state,lease_owner=NULL,lease_until=NULL,
                    run_at=now()+make_interval(secs => LEAST(60,5*power(2,LEAST(reconcile_attempts-1,4))::int))
                    WHERE tenant_id=:t AND id=:j'''), {**p, 'state': 'manual' if budget else 'ready'})
                await audit(conn, job, 'manual_required' if budget else 'payment_unknown')

    async def once(self) -> bool:
        job = await self.claim()
        if job is None:
            return False
        payload = await self.prepare(job)
        if payload is None:
            return True
        heartbeat = asyncio.create_task(self.heartbeat(job))
        try:
            result = await self.payment(payload, job['manual_query'])
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await self.finish(job, result)
        return True
