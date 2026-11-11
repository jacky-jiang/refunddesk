from datetime import timedelta

from conftest import PASSWORD, confirmation, create, login
from sqlalchemy import text


def test_login_csrf_logout_and_disabled_user(environment):
    client, engine, _clock = environment
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/login', json={'user_id': 'a-support', 'tenant_id': 'a',
        'password': PASSWORD}).status_code == 403
    assert client.post('/api/auth/login', headers={'X-CSRF-Protection': '1'},
        json={'user_id': 'a-support', 'tenant_id': 'a', 'password': 'wrong'}).status_code == 401
    headers = login(client)
    assert client.get('/api/auth/me').json()['roles'] == ['support']
    assert client.post('/api/auth/logout').status_code == 403
    assert client.post('/api/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/auth/me').status_code == 401
    headers = login(client)
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET enabled=false WHERE id='a-support'"))
    assert client.get('/api/orders/ORDER-1').status_code == 401
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM security_events')) >= 5


def test_session_expiry_and_input_redaction(environment):
    client, _, clock = environment
    login(client)
    clock.value += timedelta(hours=8)
    assert client.get('/api/auth/me').status_code == 401
    secret = 'secret-that-must-not-appear'
    r = client.post('/api/auth/login', json={'password': secret})
    assert r.status_code == 422 and secret not in r.text
    assert client.post('/api/auth/login', headers={'X-CSRF-Protection': '1',
        'Origin': 'https://evil.example'}, json={'user_id': 'a-support', 'tenant_id': 'a',
        'password': PASSWORD}).status_code == 403


def test_full_workflow_and_audit(environment):
    client, engine, _ = environment
    headers = login(client)
    assert client.get('/api/orders/ORDER-1').json()['amount_minor'] == 12000
    assert client.get('/api/refund-policies/current').json()['version'] == 'v1'
    assert client.get('/api/refund-policies/v1').status_code == 200
    assert client.post('/api/orders/ORDER-1/refund-evaluation', headers=headers).json()['eligible']
    row = create(client, headers).json()
    assert row['state'] == 'pending'
    request_id = row['id']
    headers = login(client, 'a-supervisor')
    assert len(client.get('/api/approvals').json()['items']) == 1
    r = client.post(f'/api/approvals/{request_id}/decision', headers=headers,
                    json={'decision': 'approve', 'note': '已核对'})
    assert r.status_code == 200, r.text
    assert r.json()['state'] == 'approved'
    headers = login(client, 'a-auditor')
    assert create(client, headers, confirm='fake').status_code == 403
    detail = client.get(f'/api/refunds/{request_id}').json()
    assert {e['action'] for e in detail['timeline']} == {'submitted', 'approve'}
    assert len(client.get('/api/audit-events', params={'order_no': 'ORDER-1'}).json()['items']) == 2
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM refund_decisions')) == 1
        assert conn.scalar(text('SELECT count(*) FROM jobs')) == 1
        assert conn.scalar(text('SELECT count(*) FROM refund_operations')) == 0


def test_tenant_isolation_and_identity_forgery(environment):
    client, _, _ = environment
    headers = login(client)
    row = create(client, headers).json()
    r = client.post('/api/refunds', headers={**headers, 'Idempotency-Key': 'forge'},
        json={'order_no': 'ORDER-2', 'reason': 'x', 'confirmation_id': 'x', 'tenant_id': 'b'})
    assert r.status_code == 422
    login(client, 'b-support', 'b')
    assert client.get(f"/api/refunds/{row['id']}").status_code == 404
    assert client.get('/api/audit-events').json()['items'] == []
    assert client.get('/api/orders/ORDER-1').json()['tenant_id'] == 'b'
    assert client.post('/api/auth/login', headers={'X-CSRF-Protection': '1'},
        json={'user_id': 'a-support', 'tenant_id': 'b', 'password': PASSWORD}).status_code == 401


def test_confirmation_replay_tampering_and_idempotency(environment):
    client, engine, _ = environment
    headers = login(client)
    cid = confirmation(client, headers)
    assert create(client, headers, reason='tampered', confirm=cid).status_code == 409
    first = create(client, headers, confirm=cid)
    assert first.status_code == 200, first.text
    assert create(client, headers, confirm=cid).json()['id'] == first.json()['id']
    assert create(client, headers, reason='changed', confirm=cid).json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert create(client, headers, key='new-key', confirm=cid).status_code == 409
    second = create(client, headers, key='another')
    assert second.json()['id'] == first.json()['id']
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM refund_requests')) == 1


def test_cancel_reapply_and_self_approval(environment):
    client, _, _ = environment
    headers = login(client, 'a-dual')
    rid = create(client, headers).json()['id']
    r = client.post(f'/api/approvals/{rid}/decision', headers=headers,
                    json={'decision': 'approve', 'note': 'self'})
    assert r.json()['error']['code'] == 'SELF_APPROVAL_FORBIDDEN'
    cid = confirmation(client, headers, rid, 'cancel', '取消')
    response = client.post(f'/api/refunds/{rid}/cancel', headers={**headers, 'Idempotency-Key': 'cancel'},
                           json={'reason': '取消', 'confirmation_id': cid})
    assert response.json()['close_reason'] == 'CANCELLED'
    assert create(client, headers, key='second').json()['id'] != rid


def test_confirmation_expiry_other_user_and_cancel_restrictions(environment):
    client, _, clock = environment
    headers = login(client)
    cid = confirmation(client, headers)
    clock.value += timedelta(minutes=5)
    assert create(client, headers, confirm=cid).status_code == 409
    cid = confirmation(client, headers)
    other_headers = login(client, 'a-dual')
    assert create(client, other_headers, confirm=cid).status_code == 409
    headers = login(client)
    rid = create(client, headers, confirm=cid).json()['id']
    other_headers = login(client, 'a-dual')
    assert client.post('/api/action-confirmations', headers=other_headers,
        json={'target': rid, 'action': 'cancel', 'reason': 'cancel'}).status_code == 403
    headers = login(client, 'a-supervisor')
    assert client.post(f'/api/approvals/{rid}/decision', headers=headers,
        json={'decision': 'approve', 'note': 'ok'}).status_code == 200
    headers = login(client)
    cid = confirmation(client, headers, rid, 'cancel', 'cancel')
    assert client.post(f'/api/refunds/{rid}/cancel', headers={**headers, 'Idempotency-Key': 'c'},
        json={'reason': 'cancel', 'confirmation_id': cid}).status_code == 409


def test_approval_expiry_boundary(environment):
    client, engine, clock = environment
    headers = login(client)
    rid = create(client, headers).json()['id']
    clock.value += timedelta(hours=24)
    headers = login(client, 'a-supervisor')
    response = client.post(f'/api/approvals/{rid}/decision', headers=headers,
        json={'decision': 'approve', 'note': 'late'})
    assert response.json()['error']['code'] == 'REQUEST_EXPIRED'
    assert client.get(f'/api/refunds/{rid}').json()['request']['close_reason'] == 'APPROVAL_EXPIRED'
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM jobs')) == 0


def test_rejection_can_reapply_and_pagination(environment):
    client, _, _ = environment
    headers = login(client)
    rid = create(client, headers).json()['id']
    headers = login(client, 'a-supervisor')
    response = client.post(f'/api/approvals/{rid}/decision', headers=headers,
        json={'decision': 'reject', 'note': 'need new reason'})
    assert response.json()['close_reason'] == 'REJECTED'
    assert client.get('/api/audit-events?limit=101').status_code == 422
    assert client.get('/api/audit-events?since=2026-11-11T12:00:00').status_code == 422
    headers = login(client)
    assert create(client, headers, key='again', reason='new reason').json()['id'] != rid


def test_password_reset_revokes_sessions_and_membership_removal(environment):
    import asyncio

    from refunddesk.entrypoints.set_password import provision
    from refunddesk.infrastructure.identity import ScryptPasswords

    client, engine, _ = environment
    login(client)
    asyncio.run(provision('a-support', ScryptPasswords().encode(PASSWORD)))
    assert client.get('/api/auth/me').status_code == 401
    login(client)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM memberships WHERE user_id='a-support'"))
    assert client.get('/api/orders/ORDER-1').status_code == 401


def test_creation_rechecks_eligibility_and_rejects_authoritative_fields(environment):
    client, engine, clock = environment
    headers = login(client)
    cid = confirmation(client, headers)
    with engine.begin() as conn:
        conn.execute(text("UPDATE orders SET shipped_at=:now WHERE tenant_id='a' AND order_no='ORDER-1'"),
                     {'now': clock.now()})
    assert create(client, headers, confirm=cid).json()['error']['code'] == 'ORDER_NOT_ELIGIBLE'
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM refund_requests')) == 0
        assert conn.scalar(text('SELECT consumed_at FROM action_confirmations')) is None
    assert client.get('/api/orders/ORDER-1').json()['refundable_amount_minor'] == 0
    assert client.post('/api/refunds', headers={**headers, 'Idempotency-Key': 'forge-amount'},
        json={'order_no': 'ORDER-2', 'reason': 'refund', 'amount_minor': 1,
              'confirmation_id': 'x'}).status_code == 422


def test_security_event_storage_failure_is_safe_503(environment, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from refunddesk.infrastructure.storage import SqlRepository

    client, _, _ = environment

    async def fail(self, values):
        raise OperationalError('security event insert', {}, RuntimeError('unavailable'))

    monkeypatch.setattr(SqlRepository, 'security', fail)
    response = client.get('/api/auth/me')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'DATABASE_NOT_READY'
    assert 'security event insert' not in response.text
