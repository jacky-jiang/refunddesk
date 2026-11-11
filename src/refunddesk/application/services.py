import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from uuid import uuid4

from refunddesk.application.ports import Clock, Passwords, Repository, Row, UowFactory
from refunddesk.domain.refunds import Actor, BusinessError, eligibility, expiration

__all__ = ["Actor", "BusinessError", "IdentityService", "RefundService"]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def payload_digest(values: Row) -> str:
    return digest(json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=False))


def required(row: Row | None) -> Row:
    if row is None:
        raise BusinessError('NOT_FOUND')
    return row


class IdentityService:
    def __init__(self, factory: UowFactory, clock: Clock, passwords: Passwords) -> None:
        self.factory, self.clock, self.passwords = factory, clock, passwords

    async def login(self, user: str, password: str, tenant: str) -> Row:
        async with self.factory() as uow:
            encoded = await uow.repo.credentials(user)
            # Password hashing runs in a thread, never on the ASGI event loop.
            import asyncio
            valid = await asyncio.to_thread(self.passwords.verify, password, encoded)
            identity = await uow.repo.identity(user, tenant)
            if not valid or identity is None:
                raise BusinessError('INVALID_CREDENTIALS')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            now = self.clock.now()
            await uow.repo.save_session({'token_hash': digest(token), 'csrf_hash': digest(csrf),
                'user_id': user, 'tenant_id': tenant, 'created_at': now,
                'expires_at': now + timedelta(hours=8)})
            await uow.commit()
            return {'token': token, 'csrf_token': csrf, 'expires_at': now + timedelta(hours=8)}

    async def authenticate(self, token: str | None, csrf: str | None = None,
                           write: bool = False) -> Actor:
        if not token:
            raise BusinessError('UNAUTHENTICATED')
        async with self.factory() as uow:
            session = await uow.repo.session(digest(token))
            if session is None or self.clock.now() >= session['expires_at']:
                raise BusinessError('UNAUTHENTICATED')
            identity = await uow.repo.identity(session['user_id'], session['tenant_id'])
            if identity is None:
                raise BusinessError('UNAUTHENTICATED')
            if write and (not csrf or not hmac.compare_digest(digest(csrf), session['csrf_hash'])):
                raise BusinessError('CSRF_INVALID')
            return Actor(session['tenant_id'], session['user_id'], frozenset(identity['roles']))

    async def logout(self, token: str) -> None:
        async with self.factory() as uow:
            await uow.repo.delete_session(digest(token))
            await uow.commit()


class RefundService:
    def __init__(self, factory: UowFactory, clock: Clock) -> None:
        self.factory, self.clock = factory, clock

    async def security(self, code: str, trace: str, actor: Actor | None = None) -> None:
        async with self.factory() as uow:
            await uow.repo.security({'id': str(uuid4()), 'occurred_at': self.clock.now(),
                'actor': actor.user_id if actor else None,
                'tenant_id': actor.tenant_id if actor else None, 'code': code, 'trace_id': trace})
            await uow.commit()

    async def audit(self, repo: Repository, actor: Actor, request_id: str, action: str,
                    trace: str, details: Row) -> None:
        await repo.audit({'tenant_id': actor.tenant_id, 'id': str(uuid4()),
            'occurred_at': self.clock.now(), 'actor': actor.user_id, 'request_id': request_id,
            'action': action, 'details': details, 'trace_id': trace})

    async def expire(self, repo: Repository, row: Row, trace: str) -> bool:
        reason = expiration(row, self.clock.now())
        if reason is None:
            return False
        await repo.set_state(row['tenant_id'], row['id'], 'closed', reason)
        await repo.close_jobs(row['tenant_id'], row['id'])
        await self.audit(repo, Actor(row['tenant_id'], 'system:expiry', frozenset()), row['id'],
                         'expired', trace, {'from': row['state'], 'state': 'closed', 'reason': reason})
        row.update(state='closed', close_reason=reason)
        return True

    async def order(self, actor: Actor, number: str) -> Row:
        async with self.factory() as uow:
            row = required(await uow.repo.order(actor.tenant_id, number))
            active = await uow.repo.active(actor.tenant_id, number)
            blocked = bool(eligibility(row, self.clock.now())) or (
                active is not None and expiration(active, self.clock.now()) is None)
            return {**row, 'refundable_amount_minor': 0 if blocked else row['amount_minor']}

    async def policy(self, actor: Actor, version: str | None = None) -> Row:
        async with self.factory() as uow:
            return required(await uow.repo.policy(actor.tenant_id, self.clock.now(), version))

    def validate_policy(self, policy: Row) -> None:
        expected = {'apply_hours': 168, 'approval_hours': 24, 'execution_start_minutes': 30,
                    'full_refund_only': True, 'unshipped_only': True, 'supervisor_required': True}
        if any(policy['rules'].get(k) != v for k, v in expected.items()):
            raise BusinessError('POLICY_UNSUPPORTED')

    async def evaluate(self, actor: Actor, number: str) -> Row:
        async with self.factory() as uow:
            order = required(await uow.repo.order(actor.tenant_id, number))
            policy = required(await uow.repo.policy(actor.tenant_id, self.clock.now()))
            self.validate_policy(policy)
            reasons = eligibility(order, self.clock.now())
            active = await uow.repo.active(actor.tenant_id, number)
            if active and expiration(active, self.clock.now()) is None:
                reasons.append('EXISTING_REFUND_REQUEST')
            return {'eligible': not reasons, 'reason_codes': reasons,
                    'amount_minor': order['amount_minor'], 'currency': order['currency'],
                    'policy_version': policy['version'], 'evaluated_at': self.clock.now()}

    async def snapshot(self, repo: Repository, actor: Actor, action: str, target: str,
                       reason: str) -> Row:
        if action == 'create':
            order = required(await repo.order(actor.tenant_id, target, lock=True))
            policy = required(await repo.policy(actor.tenant_id, self.clock.now()))
            self.validate_policy(policy)
            return {'action': action, 'target': target, 'reason': reason,
                    'amount_minor': order['amount_minor'], 'currency': order['currency'],
                    'policy_version': policy['version']}
        if action != 'cancel':
            raise BusinessError('INVALID_ACTION')
        row = required(await repo.request(actor.tenant_id, target))
        required(await repo.order(actor.tenant_id, row['order_no'], lock=True))
        row = required(await repo.request(actor.tenant_id, target, lock=True))
        if row['applicant_id'] != actor.user_id:
            raise BusinessError('FORBIDDEN')
        return {'action': action, 'target': target, 'reason': reason,
                'amount_minor': row['amount_minor'], 'currency': row['currency']}

    async def confirm(self, actor: Actor, action: str, target: str, reason: str) -> Row:
        actor.require('support')
        async with self.factory() as uow:
            snapshot = await self.snapshot(uow.repo, actor, action, target, reason)
            now = self.clock.now()
            confirmation_id = secrets.token_urlsafe(32)
            await uow.repo.add_confirmation({'tenant_id': actor.tenant_id, 'user_id': actor.user_id,
                'id': confirmation_id, 'action': action, 'payload_digest': payload_digest(snapshot),
                'expires_at': now + timedelta(minutes=5)})
            await uow.commit()
            return {'confirmation_id': confirmation_id, 'expires_at': now + timedelta(minutes=5),
                    'confirmed_action': snapshot}

    async def use_confirmation(self, repo: Repository, actor: Actor, confirmation_id: str,
                               snapshot: Row) -> None:
        row = await repo.confirmation(actor.tenant_id, confirmation_id)
        now = self.clock.now()
        if (row is None or row['user_id'] != actor.user_id or row['action'] != snapshot['action']
                or row['payload_digest'] != payload_digest(snapshot)
                or row['consumed_at'] is not None or now >= row['expires_at']):
            raise BusinessError('CONFIRMATION_INVALID')
        await repo.consume(actor.tenant_id, confirmation_id, now)

    async def write(self, actor: Actor, action: str, target: str, reason: str,
                    confirmation_id: str, key: str, trace: str) -> Row:
        actor.require('support')
        if not key or len(key) > 128:
            raise BusinessError('IDEMPOTENCY_KEY_REQUIRED')
        fingerprint = payload_digest({'action': action, 'target': target, 'reason': reason})
        async with self.factory() as uow:
            repo = uow.repo
            await repo.lock_key(json.dumps([actor.tenant_id, actor.user_id, action, key]))
            previous = await repo.idempotency(actor.tenant_id, actor.user_id, action, key)
            if previous:
                if previous['payload_digest'] != fingerprint:
                    raise BusinessError('IDEMPOTENCY_CONFLICT')
                return required(await repo.request(actor.tenant_id, previous['request_id']))
            snapshot = await self.snapshot(repo, actor, action, target, reason)
            await self.use_confirmation(repo, actor, confirmation_id, snapshot)
            if action == 'create':
                row = await repo.active(actor.tenant_id, target)
                if row:
                    # Order lock serializes creation, approval, cancellation and expiry.
                    row = required(await repo.request(actor.tenant_id, row['id'], lock=True))
                    if await self.expire(repo, row, trace):
                        row = None
                if row and row['state'] == 'succeeded':
                    raise BusinessError('ORDER_NOT_ELIGIBLE')
                if row is None:
                    order = required(await repo.order(actor.tenant_id, target))
                    if eligibility(order, self.clock.now()):
                        # A failed write rolls back confirmation consumption and lazy expiry.
                        # The independent scanner persists expiry in its own transaction.
                        raise BusinessError('ORDER_NOT_ELIGIBLE')
                    now = self.clock.now()
                    row = {'tenant_id': actor.tenant_id, 'id': str(uuid4()), 'order_no': target,
                           'applicant_id': actor.user_id, 'reason': reason,
                           'amount_minor': snapshot['amount_minor'], 'currency': snapshot['currency'],
                           'policy_version': snapshot['policy_version'], 'created_at': now,
                           'approve_by': now + timedelta(hours=24)}
                    await repo.add_request(row)
                    await self.audit(repo, actor, row['id'], 'submitted', trace, {'state': 'pending'})
            else:
                row = required(await repo.request(actor.tenant_id, target, lock=True))
                if await self.expire(repo, row, trace):
                    # No cancellation was performed; expiry remains a scanner responsibility on rollback.
                    raise BusinessError('REQUEST_EXPIRED')
                if row['state'] != 'pending':
                    raise BusinessError('STATE_CONFLICT')
                await repo.set_state(actor.tenant_id, target, 'closed', 'CANCELLED')
                await self.audit(repo, actor, target, 'cancelled', trace,
                                 {'state': 'closed', 'reason': reason})
            await repo.add_idempotency({'tenant_id': actor.tenant_id, 'user_id': actor.user_id,
                'action': action, 'key': key, 'payload_digest': fingerprint, 'request_id': row['id']})
            result = required(await repo.request(actor.tenant_id, row['id']))
            await uow.commit()
            return result

    async def decide(self, actor: Actor, request_id: str, decision: str, note: str, trace: str) -> Row:
        actor.require('supervisor')
        if decision not in {'approve', 'reject'}:
            raise BusinessError('INVALID_DECISION')
        async with self.factory() as uow:
            repo = uow.repo
            row = required(await repo.request(actor.tenant_id, request_id))
            required(await repo.order(actor.tenant_id, row['order_no'], lock=True))
            row = required(await repo.request(actor.tenant_id, request_id, lock=True))
            if row['applicant_id'] == actor.user_id:
                raise BusinessError('SELF_APPROVAL_FORBIDDEN')
            if await self.expire(repo, row, trace):
                await uow.commit()
                raise BusinessError('REQUEST_EXPIRED')
            if row['state'] != 'pending':
                raise BusinessError('STATE_CONFLICT')
            now = self.clock.now()
            state, close_reason = ('approved', None) if decision == 'approve' else ('closed', 'REJECTED')
            await repo.decision({'tenant_id': actor.tenant_id, 'request_id': request_id,
                'applicant_id': row['applicant_id'], 'reviewer_id': actor.user_id,
                'decision': decision, 'note': note, 'decided_at': now})
            await repo.set_state(actor.tenant_id, request_id, state, close_reason,
                                 now + timedelta(minutes=30) if decision == 'approve' else None)
            if decision == 'approve':
                await repo.job({'tenant_id': actor.tenant_id, 'id': str(uuid4()),
                                'request_id': request_id, 'run_at': now})
            await self.audit(repo, actor, request_id, decision, trace,
                             {'state': state, 'note': note})
            result = required(await repo.request(actor.tenant_id, request_id))
            await uow.commit()
            return result

    async def detail(self, actor: Actor, request_id: str, limit: int = 100, offset: int = 0) -> Row:
        async with self.factory() as uow:
            row = required(await uow.repo.request(actor.tenant_id, request_id))
            timeline = await uow.repo.audits(actor.tenant_id, {'request_id': request_id}, limit, offset)
            return {'request': row, 'timeline': timeline, 'limit': limit, 'offset': offset}

    async def approvals(self, actor: Actor, limit: int, offset: int) -> list[Row]:
        actor.require('supervisor')
        async with self.factory() as uow:
            return await uow.repo.approvals(actor.tenant_id, self.clock.now(), limit, offset)

    async def audits(self, actor: Actor, filters: Row, limit: int, offset: int) -> list[Row]:
        async with self.factory() as uow:
            return await uow.repo.audits(actor.tenant_id, filters, limit, offset)

    async def sweep(self, trace: str, limit: int = 100) -> int:
        async with self.factory() as uow:
            candidates = await uow.repo.due(self.clock.now(), limit)
        count = 0
        for candidate in candidates:
            async with self.factory() as uow:
                repo = uow.repo
                row = required(await repo.request(candidate['tenant_id'], candidate['id']))
                required(await repo.order(row['tenant_id'], row['order_no'], lock=True))
                row = required(await repo.request(row['tenant_id'], row['id'], lock=True))
                if await self.expire(repo, row, trace):
                    count += 1
                await uow.commit()
        return count
