import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from refunddesk.application.services import Actor, BusinessError, RefundService
from refunddesk.db import open_engine
from refunddesk.infrastructure.storage import SqlRepository, SqlUnitOfWork

SUPPORT = Actor('a', 'a-support', frozenset({'support'}))
MANAGER = Actor('a', 'a-supervisor', frozenset({'supervisor'}))


async def new_request(service, number='ORDER-1'):
    confirm = await service.confirm(SUPPORT, 'create', number, 'refund')
    return await service.write(SUPPORT, 'create', number, 'refund', confirm['confirmation_id'],
                               str(uuid4()), 'trace-test')


def exercise(environment, scenario):
    _, _, clock = environment

    async def run():
        engine = open_engine()
        try:
            service = RefundService(lambda: SqlUnitOfWork(engine), clock)
            await scenario(service)
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_submissions(environment, same_key):
    _, engine, _ = environment

    async def scenario(service):
        confirmations = [await service.confirm(SUPPORT, 'create', 'ORDER-1', 'refund') for _ in range(6)]
        rows = await asyncio.gather(*[
            service.write(SUPPORT, 'create', 'ORDER-1', 'refund', c['confirmation_id'],
                          'same' if same_key else str(i), 'trace-test')
            for i, c in enumerate(confirmations)])
        assert len({row['id'] for row in rows}) == 1
    exercise(environment, scenario)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM refund_requests')) == 1
        assert conn.scalar(text("SELECT count(*) FROM audit_events WHERE action='submitted'")) == 1


def test_concurrent_final_decisions(environment):
    _, engine, _ = environment

    async def scenario(service):
        row = await new_request(service)
        second = Actor('a', 'a-dual', frozenset({'supervisor', 'support'}))
        results = await asyncio.gather(
            service.decide(MANAGER, row['id'], 'approve', 'yes', 'trace'),
            service.decide(second, row['id'], 'reject', 'no', 'trace'), return_exceptions=True)
        assert sum(isinstance(r, BusinessError) for r in results) == 1
        winner = next(r for r in results if isinstance(r, dict))
        assert winner['state'] in {'approved', 'closed'}
    exercise(environment, scenario)
    with engine.connect() as conn:
        decision = conn.scalar(text('SELECT decision FROM refund_decisions'))
        assert conn.scalar(text('SELECT count(*) FROM refund_decisions')) == 1
        assert conn.scalar(text('SELECT count(*) FROM jobs')) == (1 if decision == 'approve' else 0)
        assert conn.scalar(text('SELECT count(*) FROM audit_events')) == 2


def test_submission_failure_rolls_back_confirmation_and_idempotency(environment, monkeypatch):
    _, engine, _ = environment
    original = SqlRepository.audit

    async def fail(self, values):
        raise RuntimeError('injected audit write failure')

    async def scenario(service):
        confirm = await service.confirm(SUPPORT, 'create', 'ORDER-1', 'refund')
        with monkeypatch.context() as patch:
            patch.setattr(SqlRepository, 'audit', fail)
            with pytest.raises(RuntimeError, match='injected'):
                await service.write(SUPPORT, 'create', 'ORDER-1', 'refund',
                                    confirm['confirmation_id'], 'key', 'trace')
        assert SqlRepository.audit is original
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT count(*) FROM refund_requests')) == 0
            assert conn.scalar(text('SELECT count(*) FROM idempotency_records')) == 0
            assert conn.scalar(text('SELECT consumed_at FROM action_confirmations')) is None
        assert (await service.write(SUPPORT, 'create', 'ORDER-1', 'refund',
                                    confirm['confirmation_id'], 'key', 'trace'))['state'] == 'pending'
    exercise(environment, scenario)


def test_approval_job_failure_rolls_back_decision(environment, monkeypatch):
    _, engine, _ = environment

    async def fail(self, values):
        raise RuntimeError('injected job failure')

    async def scenario(service):
        row = await new_request(service)
        with monkeypatch.context() as patch:
            patch.setattr(SqlRepository, 'job', fail)
            with pytest.raises(RuntimeError, match='injected'):
                await service.decide(MANAGER, row['id'], 'approve', 'ok', 'trace')
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT state FROM refund_requests')) == 'pending'
            assert conn.scalar(text('SELECT count(*) FROM refund_decisions')) == 0
            assert conn.scalar(text('SELECT count(*) FROM audit_events')) == 1
        assert (await service.decide(MANAGER, row['id'], 'approve', 'ok', 'trace'))['state'] == 'approved'
    exercise(environment, scenario)


def test_expiry_restart_and_started_request_not_revoked(environment):
    _, engine, clock = environment

    async def scenario(service):
        row = await new_request(service)
        await service.decide(MANAGER, row['id'], 'approve', 'ok', 'trace')
        clock.value += timedelta(minutes=30)
        assert await service.sweep('expiry') == 1
        assert await service.sweep('again') == 0
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT close_reason FROM refund_requests')) == 'EXECUTION_EXPIRED'
            assert conn.scalar(text('SELECT state FROM jobs')) == 'done'
        second = await new_request(service, 'ORDER-2')
        await service.decide(MANAGER, second['id'], 'approve', 'ok', 'trace')
        with engine.begin() as conn:
            conn.execute(text("UPDATE refund_requests SET state='executing' WHERE id=:id"),
                         {'id': second['id']})
        clock.value += timedelta(days=2)
        assert await service.sweep('later') == 0
    exercise(environment, scenario)


def test_expiry_and_approval_compete(environment):
    _, engine, clock = environment

    async def scenario(service):
        row = await new_request(service)
        clock.value += timedelta(hours=24)
        results = await asyncio.gather(service.sweep('expiry'),
            service.decide(MANAGER, row['id'], 'approve', 'late', 'trace'), return_exceptions=True)
        assert isinstance(results[1], BusinessError)
    exercise(environment, scenario)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT close_reason FROM refund_requests')) == 'APPROVAL_EXPIRED'
        assert conn.scalar(text('SELECT count(*) FROM refund_decisions')) == 0
        assert conn.scalar(text('SELECT count(*) FROM jobs')) == 0
        assert conn.scalar(text("SELECT count(*) FROM audit_events WHERE action='expired'")) == 1


def test_confirmation_binds_policy_version(environment):
    _, engine, clock = environment

    async def scenario(service):
        confirm = await service.confirm(SUPPORT, 'create', 'ORDER-1', 'refund')
        with engine.begin() as conn:
            policy = conn.execute(text('SELECT rules FROM refund_policies WHERE tenant_id=\'a\'')).scalar()
            conn.execute(text('INSERT INTO refund_policies VALUES (\'a\',\'v2\',:now,CAST(:rules AS jsonb))'),
                         {'now': clock.now(), 'rules': json.dumps(policy)})
        with pytest.raises(BusinessError, match='CONFIRMATION_INVALID'):
            await service.write(SUPPORT, 'create', 'ORDER-1', 'refund', confirm['confirmation_id'], 'k', 't')
        row = await new_request(service)
        assert row['policy_version'] == 'v2'
        assert (await service.policy(SUPPORT, 'v1'))['version'] == 'v1'
    exercise(environment, scenario)


def test_cancel_and_approval_compete(environment):
    _, engine, _ = environment

    async def scenario(service):
        row = await new_request(service)
        confirm = await service.confirm(SUPPORT, 'cancel', row['id'], 'cancel')
        results = await asyncio.gather(
            service.write(SUPPORT, 'cancel', row['id'], 'cancel', confirm['confirmation_id'], 'c', 't'),
            service.decide(MANAGER, row['id'], 'approve', 'ok', 't'), return_exceptions=True)
        assert sum(isinstance(r, BusinessError) for r in results) == 1
    exercise(environment, scenario)
    with engine.connect() as conn:
        state = conn.scalar(text('SELECT state FROM refund_requests'))
        assert state in {'closed', 'approved'}
        assert conn.scalar(text('SELECT count(*) FROM jobs')) == (1 if state == 'approved' else 0)
        assert conn.scalar(text('SELECT count(*) FROM audit_events')) == 2
