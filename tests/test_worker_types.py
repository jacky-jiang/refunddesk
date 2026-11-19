import asyncio

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from refunddesk.workers.execution import JOB_ADAPTER, Executor


@pytest.mark.parametrize('changes', [
    {'state': 'invalid'},
    {'amount_minor': '100'},
    {'amount_minor': True},
    {'amount_minor': 101},
    {'tenant_id': 'different'},
    {'channel_ref': 123},
])
def test_payment_invalid_response_stays_unknown(monkeypatch, changes):
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')
    engine = create_async_engine('postgresql+psycopg://u:p@localhost/test')
    worker = Executor(engine)
    payload = {'payment_key': 'key', 'tenant_id': 'tenant', 'order_no': 'order',
               'amount_minor': 100, 'currency': 'CNY'}
    data = {**payload, 'state': 'succeeded', 'channel_ref': 'ref', **changes}
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(transport=transport, **kwargs))
    assert asyncio.run(worker.payment(payload, False)) is None
    asyncio.run(engine.dispose())


def test_payment_accepts_valid_response_and_preserves_reference(monkeypatch):
    monkeypatch.setenv('PAYMENT_BASE_URL', 'http://mock')
    monkeypatch.setenv('PAYMENT_SERVICE_TOKEN', 'test-token')
    engine = create_async_engine('postgresql+psycopg://u:p@localhost/test')
    worker = Executor(engine)
    payload = {'payment_key': 'key', 'tenant_id': 'tenant', 'order_no': 'order',
               'amount_minor': 100, 'currency': 'CNY'}
    result = {**payload, 'state': 'succeeded', 'channel_ref': 'ref'}
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=result))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(transport=transport, **kwargs))
    assert asyncio.run(worker.payment(payload, False)) == result
    asyncio.run(engine.dispose())


def test_job_adapter_rejects_invalid_lease_generation():
    with pytest.raises(ValidationError):
        JOB_ADAPTER.validate_python({'tenant_id': 't', 'id': 'i', 'request_id': 'r',
                                     'lease_generation': '1', 'manual_query': False}, strict=True)
