import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from refunddesk.entrypoints.api import app
from refunddesk.infrastructure.identity import ScryptPasswords
from refunddesk.settings import database_options

PASSWORD = 'Test-only-long-passphrase!'


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 11, 12, tzinfo=UTC)

    def now(self):
        return self.value


@pytest.fixture
def environment(monkeypatch):
    schema = 'i1_' + uuid4().hex
    monkeypatch.setenv('DB_SCHEMA', schema)
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    command.upgrade(Config('alembic.ini'), 'head')
    url, options = database_options(migration=True)
    engine = create_engine(url, connect_args=options)
    clock = Clock()
    password = ScryptPasswords().encode(PASSWORD)
    with engine.begin() as conn:
        for tenant in ['a', 'b']:
            conn.execute(text('INSERT INTO tenants VALUES (:t,:t)'), {'t': tenant})
            for role in ['support', 'supervisor', 'auditor', 'dual']:
                user = tenant + '-' + role
                conn.execute(text('INSERT INTO users(id,display_name) VALUES (:u,:u)'), {'u': user})
                conn.execute(text('INSERT INTO user_credentials VALUES (:u,:p)'), {'u': user, 'p': password})
                for member_role in (['support', 'supervisor'] if role == 'dual' else [role]):
                    conn.execute(text('INSERT INTO memberships VALUES (:t,:u,:r)'),
                                 {'t': tenant, 'u': user, 'r': member_role})
            rules = {'apply_hours': 168, 'approval_hours': 24, 'execution_start_minutes': 30,
                     'full_refund_only': True, 'unshipped_only': True, 'supervisor_required': True}
            conn.execute(text('INSERT INTO refund_policies VALUES (:t,\'v1\',:now,CAST(:rules AS jsonb))'),
                         {'t': tenant, 'now': clock.now() - timedelta(days=1), 'rules': json.dumps(rules)})
            for number in ['ORDER-1', 'ORDER-2']:
                conn.execute(text('''INSERT INTO orders
                    (tenant_id,order_no,amount_minor,currency,paid_at) VALUES (:t,:n,12000,'CNY',:paid)'''),
                    {'t': tenant, 'n': number, 'paid': clock.now() - timedelta(days=1)})
    app.state.clock = clock
    try:
        with TestClient(app) as client:
            yield client, engine, clock
    finally:
        del app.state.clock
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def login(client, user='a-support', tenant='a'):
    response = client.post('/api/auth/login', json={'user_id': user, 'tenant_id': tenant,
        'password': PASSWORD}, headers={'X-CSRF-Protection': '1'})
    assert response.status_code == 200, response.text
    return {'X-CSRF-Token': response.json()['csrf_token']}


def confirmation(client, headers, target='ORDER-1', action='create', reason='重复购买'):
    response = client.post('/api/action-confirmations', headers=headers,
        json={'target': target, 'action': action, 'reason': reason})
    assert response.status_code == 201, response.text
    return response.json()['confirmation_id']


def create(client, headers, target='ORDER-1', key='first', reason='重复购买', confirm=None):
    confirm = confirm or confirmation(client, headers, target, reason=reason)
    return client.post('/api/refunds', headers={**headers, 'Idempotency-Key': key},
        json={'order_no': target, 'reason': reason, 'confirmation_id': confirm})
