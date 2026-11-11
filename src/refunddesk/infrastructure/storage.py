import json
from datetime import datetime
from types import TracebackType
from typing import Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from refunddesk.application.ports import Repository, Row


class SqlRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.conn = connection

    async def one(self, sql: str, values: Row) -> Row | None:
        row = (await self.conn.execute(text(sql), values)).mappings().first()
        return dict(row) if row else None

    async def many(self, sql: str, values: Row) -> list[Row]:
        return [dict(row) for row in (await self.conn.execute(text(sql), values)).mappings()]

    async def execute(self, sql: str, values: Row) -> None:
        await self.conn.execute(text(sql), values)

    async def identity(self, user: str, tenant: str) -> Row | None:
        return await self.one('''SELECT u.id, u.display_name, array_agg(m.role ORDER BY m.role) roles
            FROM users u JOIN memberships m ON m.user_id=u.id
            WHERE u.id=:user AND m.tenant_id=:tenant AND u.enabled
            GROUP BY u.id, u.display_name''', {'user': user, 'tenant': tenant})

    async def credentials(self, user: str) -> str | None:
        row = await self.one('''SELECT c.password_hash FROM user_credentials c JOIN users u ON u.id=c.user_id
            WHERE c.user_id=:user FOR SHARE OF u''',
                             {'user': user})
        return str(row['password_hash']) if row else None

    async def session(self, token_hash: str) -> Row | None:
        return await self.one('SELECT * FROM auth_sessions WHERE token_hash=:token', {'token': token_hash})

    async def save_session(self, values: Row) -> None:
        await self.execute('''INSERT INTO auth_sessions
            (token_hash, user_id, tenant_id, csrf_hash, expires_at, created_at)
            VALUES (:token_hash,:user_id,:tenant_id,:csrf_hash,:expires_at,:created_at)''', values)

    async def delete_session(self, token_hash: str) -> None:
        await self.execute('DELETE FROM auth_sessions WHERE token_hash=:token', {'token': token_hash})

    async def order(self, tenant: str, order_no: str, lock: bool = False) -> Row | None:
        return await self.one('SELECT * FROM orders WHERE tenant_id=:t AND order_no=:n'
                              + (' FOR UPDATE' if lock else ''), {'t': tenant, 'n': order_no})

    async def policy(self, tenant: str, now: datetime, version: str | None = None) -> Row | None:
        return await self.one('''SELECT * FROM refund_policies WHERE tenant_id=:t AND '''
            + ('version=:version' if version is not None else 'effective_at<=:now')
            + ' ORDER BY effective_at DESC, version DESC LIMIT 1',
            {'t': tenant, 'now': now, 'version': version})

    async def request(self, tenant: str, request_id: str, lock: bool = False) -> Row | None:
        return await self.one('SELECT * FROM refund_requests WHERE tenant_id=:t AND id=:id'
                              + (' FOR UPDATE' if lock else ''), {'t': tenant, 'id': request_id})

    async def active(self, tenant: str, order_no: str) -> Row | None:
        return await self.one('''SELECT * FROM refund_requests WHERE tenant_id=:t AND order_no=:n
            AND state IN ('pending','approved','executing','reconciling','manual','succeeded')''',
            {'t': tenant, 'n': order_no})

    async def add_request(self, values: Row) -> None:
        await self.execute('''INSERT INTO refund_requests
            (tenant_id,id,order_no,applicant_id,reason,amount_minor,currency,policy_version,
             state,created_at,approve_by) VALUES
            (:tenant_id,:id,:order_no,:applicant_id,:reason,:amount_minor,:currency,:policy_version,
             'pending',:created_at,:approve_by)''', values)

    async def set_state(self, tenant: str, request_id: str, state: str,
                        reason: str | None, start_by: datetime | None = None) -> None:
        await self.execute('''UPDATE refund_requests SET state=:state,close_reason=:reason,
            start_by=COALESCE(:start_by,start_by) WHERE tenant_id=:t AND id=:id''',
            {'t': tenant, 'id': request_id, 'state': state, 'reason': reason, 'start_by': start_by})

    async def decision(self, values: Row) -> None:
        await self.execute('''INSERT INTO refund_decisions
            (tenant_id,request_id,applicant_id,reviewer_id,decision,note,decided_at)
            VALUES (:tenant_id,:request_id,:applicant_id,:reviewer_id,:decision,:note,:decided_at)''', values)

    async def job(self, values: Row) -> None:
        await self.execute('''INSERT INTO jobs
            (tenant_id,id,request_id,kind,state,run_at,attempts)
            VALUES (:tenant_id,:id,:request_id,'execute','ready',:run_at,0)''', values)

    async def close_jobs(self, tenant: str, request_id: str) -> None:
        await self.execute('''UPDATE jobs SET state='done', lease_until=NULL,lease_owner=NULL
            WHERE tenant_id=:t AND request_id=:id AND kind='execute' AND state='ready' ''',
            {'t': tenant, 'id': request_id})

    async def audit(self, values: Row) -> None:
        await self.execute('''INSERT INTO audit_events
            (tenant_id,id,occurred_at,actor,request_id,action,details,trace_id)
            VALUES (:tenant_id,:id,:occurred_at,:actor,:request_id,:action,CAST(:details AS jsonb),:trace_id)''',
            {**values, 'details': json.dumps(values['details'])})

    async def security(self, values: Row) -> None:
        await self.execute('''INSERT INTO security_events
            (id,occurred_at,actor,tenant_id,code,trace_id)
            VALUES (:id,:occurred_at,:actor,:tenant_id,:code,:trace_id)''', values)

    async def confirmation(self, tenant: str, confirmation_id: str) -> Row | None:
        return await self.one('''SELECT * FROM action_confirmations
            WHERE tenant_id=:t AND id=:id FOR UPDATE''', {'t': tenant, 'id': confirmation_id})

    async def add_confirmation(self, values: Row) -> None:
        await self.execute('''INSERT INTO action_confirmations
            (tenant_id,id,user_id,action,payload_digest,expires_at)
            VALUES (:tenant_id,:id,:user_id,:action,:payload_digest,:expires_at)''', values)

    async def consume(self, tenant: str, confirmation_id: str, now: datetime) -> None:
        await self.execute('''UPDATE action_confirmations SET consumed_at=:now
            WHERE tenant_id=:t AND id=:id''', {'t': tenant, 'id': confirmation_id, 'now': now})

    async def lock_key(self, key: str) -> None:
        await self.execute('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))', {'key': key})

    async def idempotency(self, tenant: str, user: str, action: str, key: str) -> Row | None:
        return await self.one('''SELECT * FROM idempotency_records WHERE tenant_id=:t
            AND user_id=:u AND action=:a AND key=:k''', {'t': tenant, 'u': user, 'a': action, 'k': key})

    async def add_idempotency(self, values: Row) -> None:
        await self.execute('''INSERT INTO idempotency_records
            (tenant_id,user_id,action,key,payload_digest,request_id)
            VALUES (:tenant_id,:user_id,:action,:key,:payload_digest,:request_id)''', values)

    async def approvals(self, tenant: str, now: datetime, limit: int, offset: int) -> list[Row]:
        return await self.many('''SELECT * FROM refund_requests WHERE tenant_id=:t
            AND state='pending' AND approve_by>:now ORDER BY created_at,id LIMIT :lim OFFSET :off''',
            {'t': tenant, 'now': now, 'lim': limit, 'off': offset})

    async def audits(self, tenant: str, filters: Row, limit: int, offset: int) -> list[Row]:
        clauses = ['a.tenant_id=:t']
        mapping = {'user_id': 'a.actor=:user_id', 'order_no': 'r.order_no=:order_no',
                   'request_id': 'a.request_id=:request_id', 'since': 'a.occurred_at>=:since',
                   'until': 'a.occurred_at<=:until'}
        clauses.extend(clause for key, clause in mapping.items() if filters.get(key) is not None)
        return await self.many('''SELECT a.* FROM audit_events a JOIN refund_requests r
            ON r.tenant_id=a.tenant_id AND r.id=a.request_id WHERE ''' + ' AND '.join(clauses)
            + ' ORDER BY a.occurred_at,a.id LIMIT :lim OFFSET :off',
            {**filters, 't': tenant, 'lim': limit, 'off': offset})

    async def due(self, now: datetime, limit: int) -> list[Row]:
        return await self.many('''SELECT tenant_id,id FROM refund_requests
            WHERE (state='pending' AND approve_by<=:now) OR (state='approved' AND start_by<=:now)
            ORDER BY created_at,tenant_id,id LIMIT :lim''', {'now': now, 'lim': limit})


class SqlUnitOfWork:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.repo: Repository
        self.conn: AsyncConnection
        self.tx: AsyncTransaction

    async def __aenter__(self) -> Self:
        self.conn = await self.engine.connect()
        self.tx = await self.conn.begin()
        self.repo = SqlRepository(self.conn)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        try:
            if self.tx.is_active:
                await self.tx.rollback()
        finally:
            await self.conn.close()

    async def commit(self) -> None:
        await self.tx.commit()
