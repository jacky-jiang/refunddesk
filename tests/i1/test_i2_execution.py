import asyncio
from uuid import uuid4

from sqlalchemy import text

from refunddesk.application.services import Actor, RefundService
from refunddesk.db import open_engine
from refunddesk.infrastructure.storage import SqlUnitOfWork
from refunddesk.workers.execution import Executor


async def approved(engine, clock):
    service = RefundService(lambda: SqlUnitOfWork(engine), clock)
    support = Actor('a', 'a-support', frozenset({'support'}))
    manager = Actor('a', 'a-supervisor', frozenset({'supervisor'}))
    confirmation = await service.confirm(support, 'create', 'ORDER-1', 'reason')
    row = await service.write(support, 'create', 'ORDER-1', 'reason',
                              confirmation['confirmation_id'], str(uuid4()), 'trace')
    await service.decide(manager, row['id'], 'approve', 'yes', 'trace')
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE refund_requests SET start_by=now()+interval '1 hour'"))
        await conn.execute(text("UPDATE jobs SET run_at=now()-interval '1 second'"))
    return row


def test_unknown_then_recovered_and_old_lease_fenced(environment, monkeypatch):
    _, sync_engine, clock = environment
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')

    async def run():
        engine = open_engine()
        try:
            await approved(engine, clock)
            first, second = Executor(engine), Executor(engine)
            job = await first.claim()
            payload = await first.prepare(job)
            assert payload is not None
            await first.finish(job, None)
            async with engine.begin() as conn:
                assert (await conn.execute(text('SELECT state FROM refund_requests'))).scalar() == 'reconciling'
                await conn.execute(text("UPDATE jobs SET run_at=now()-interval '1 second'"))
            next_job = await second.claim()
            assert (await second.prepare(next_job)) == payload
            await second.finish(next_job, {**payload, 'state': 'succeeded', 'channel_ref': 'channel'})
            await first.finish(job, {**payload, 'state': 'failed'})
        finally:
            await engine.dispose()
    asyncio.run(run())
    with sync_engine.connect() as conn:
        assert conn.scalar(text('SELECT state FROM refund_requests')) == 'succeeded'
        assert conn.scalar(text('SELECT refund_state FROM orders WHERE order_no=\'ORDER-1\' AND tenant_id=\'a\'')) == 'refunded'
        assert conn.scalar(text('SELECT count(*) FROM refund_operations')) == 1


def test_payment_persistence_and_key_conflict(monkeypatch):
    import os

    import pytest
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from refunddesk.entrypoints.payment import app
    from refunddesk.entrypoints.payment_init import main

    url = os.getenv('PAYMENT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PAYMENT_TEST_DATABASE_URL points to a disposable payment database')
    monkeypatch.setenv('PAYMENT_DATABASE_URL', url)
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')
    engine = create_engine(url)
    main()
    key = str(uuid4())
    payload = {'payment_key': key, 'tenant_id': 'a', 'order_no': 'ORDER-1',
               'amount_minor': 100, 'currency': 'CNY'}
    headers = {'Authorization': 'Bearer test-token'}
    try:
        with TestClient(app) as client:
            assert client.post('/payments/refunds', json=payload).status_code == 401
            first = client.post('/payments/refunds', json=payload, headers=headers)
            assert first.status_code == 200
            assert client.post('/payments/refunds', json=payload, headers=headers).json() == first.json()
            assert client.post('/payments/refunds', json={**payload, 'amount_minor': 101}, headers=headers).status_code == 409
        with TestClient(app) as client:
            assert client.get('/payments/refunds/' + key, headers=headers).json() == first.json()
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT count(*) FROM payment_refunds WHERE payment_key=:key'), {'key': key}) == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM payment_refunds WHERE payment_key=:key'), {'key': key})
        engine.dispose()


def test_real_http_response_loss_and_worker_restart(environment, monkeypatch):
    import os
    import socket
    import subprocess
    import sys
    import time

    import httpx
    import pytest
    from sqlalchemy import create_engine

    from refunddesk.entrypoints.payment_init import main

    url = os.getenv('PAYMENT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PAYMENT_TEST_DATABASE_URL points to a disposable payment database')
    monkeypatch.setenv('PAYMENT_DATABASE_URL', url)
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')
    monkeypatch.setenv('PAYMENT_FAULT_MODE', 'drop_response')
    main()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    monkeypatch.setenv('PAYMENT_BASE_URL', base)
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'refunddesk.entrypoints.payment:app',
                             '--host', '127.0.0.1', '--port', str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, business, clock = environment
    payment_engine = create_engine(url)
    payment_key = None
    try:
        for _ in range(100):
            try:
                if httpx.get(base + '/health/live', timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            pytest.fail('payment service failed to start')

        async def run():
            engine = open_engine()
            try:
                await approved(engine, clock)
                worker = Executor(engine)
                assert await worker.once()
                async with engine.begin() as conn:
                    assert (await conn.execute(text('SELECT state FROM refund_requests'))).scalar() == 'reconciling'
                    key = (await conn.execute(text('SELECT payment_key FROM refund_operations'))).scalar()
                    await conn.execute(text("UPDATE jobs SET run_at=now()-interval '1 second'"))
                restarted = Executor(engine)
                assert await restarted.once()
                return key
            finally:
                await engine.dispose()
        payment_key = asyncio.run(run())
        with business.connect() as conn:
            assert conn.scalar(text('SELECT state FROM refund_requests')) == 'succeeded'
        with payment_engine.connect() as conn:
            assert conn.scalar(text('SELECT count(*) FROM payment_refunds WHERE payment_key=:key'),
                               {'key': payment_key}) == 1
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if payment_key:
            with payment_engine.begin() as conn:
                conn.execute(text('DELETE FROM payment_refunds WHERE payment_key=:key'), {'key': payment_key})
        payment_engine.dispose()


def test_manual_query_preserves_reservation_and_requires_supervisor(environment, monkeypatch):
    import pytest

    from refunddesk.domain.refunds import BusinessError

    _, sync_engine, clock = environment
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')

    async def run():
        engine = open_engine()
        try:
            row = await approved(engine, clock)
            worker = Executor(engine)
            job = await worker.claim()
            await worker.prepare(job)
            async with engine.begin() as conn:
                await conn.execute(text('UPDATE jobs SET reconcile_attempts=7'))
            await worker.finish(job, None)
            service = RefundService(lambda: SqlUnitOfWork(engine), clock)
            support = Actor('a', 'a-support', frozenset({'support'}))
            manager = Actor('a', 'a-supervisor', frozenset({'supervisor'}))
            with pytest.raises(BusinessError, match='FORBIDDEN'):
                await service.reconcile(support, row['id'], 'key', 'trace')
            with pytest.raises(BusinessError, match='STATE_CONFLICT'):
                await service.ship(manager, 'ORDER-1')
            assert (await service.reconcile(manager, row['id'], 'key', 'trace'))['state'] == 'reconciling'
            assert (await service.reconcile(manager, row['id'], 'key', 'trace'))['state'] == 'reconciling'
            job = await worker.claim()
            assert job['manual_query'] is True
            await worker.prepare(job)
            await worker.finish(job, None)
        finally:
            await engine.dispose()
    asyncio.run(run())
    with sync_engine.connect() as conn:
        assert conn.scalar(text('SELECT state FROM refund_requests')) == 'manual'
        assert conn.scalar(text("SELECT refund_state FROM orders WHERE tenant_id='a' AND order_no='ORDER-1'")) == 'reserved'
        assert conn.scalar(text("SELECT count(*) FROM audit_events WHERE action='manual_reconcile_requested'")) == 1


def test_expired_approval_never_starts_payment(environment, monkeypatch):
    _, sync_engine, clock = environment
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')

    async def run():
        engine = open_engine()
        try:
            await approved(engine, clock)
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE refund_requests SET start_by=now()-interval '1 second'"))
            worker = Executor(engine)
            job = await worker.claim()
            assert await worker.prepare(job) is None
        finally:
            await engine.dispose()
    asyncio.run(run())
    with sync_engine.connect() as conn:
        assert conn.scalar(text('SELECT close_reason FROM refund_requests')) == 'EXECUTION_EXPIRED'
        assert conn.scalar(text('SELECT count(*) FROM refund_operations')) == 0


def test_concurrent_claim_takeover_and_prepared_recovery(environment, monkeypatch):
    _, sync_engine, clock = environment
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')

    async def run():
        engine = open_engine()
        try:
            await approved(engine, clock)
            workers = [Executor(engine), Executor(engine)]
            claims = await asyncio.gather(*(worker.claim() for worker in workers))
            assert sum(job is not None for job in claims) == 1
            owner = next(i for i, job in enumerate(claims) if job is not None)
            first = claims[owner]
            payload = await workers[owner].prepare(first)
            # Simulate a process dying after preparation and the lease expiring.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE jobs SET lease_until=now()-interval '1 second'"))
            replacement = workers[1 - owner]
            next_job = await replacement.claim()
            assert next_job['lease_generation'] > first['lease_generation']
            assert await replacement.prepare(next_job) == payload
            await replacement.finish(next_job, {**payload, 'state': 'succeeded', 'channel_ref': 'ok'})
            await workers[owner].finish(first, {**payload, 'state': 'failed'})
        finally:
            await engine.dispose()
    asyncio.run(run())
    with sync_engine.connect() as conn:
        assert conn.scalar(text('SELECT state FROM refund_requests')) == 'succeeded'
        assert conn.scalar(text('SELECT count(*) FROM refund_operations')) == 1


def test_ship_and_preparation_are_mutually_exclusive(environment, monkeypatch):
    from refunddesk.domain.refunds import BusinessError

    _, sync_engine, clock = environment
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')

    async def run():
        engine = open_engine()
        try:
            await approved(engine, clock)
            worker = Executor(engine)
            job = await worker.claim()
            service = RefundService(lambda: SqlUnitOfWork(engine), clock)
            supervisor = Actor('a', 'a-supervisor', frozenset({'supervisor'}))
            results = await asyncio.gather(worker.prepare(job), service.ship(supervisor, 'ORDER-1'),
                                           return_exceptions=True)
            assert any(result is None or isinstance(result, BusinessError) for result in results)
        finally:
            await engine.dispose()
    asyncio.run(run())
    with sync_engine.connect() as conn:
        row = conn.execute(text("SELECT shipped_at,refund_state FROM orders WHERE tenant_id='a' AND order_no='ORDER-1'" )).one()
        assert (row.shipped_at is not None and row.refund_state == 'none') or (
            row.shipped_at is None and row.refund_state == 'reserved')
