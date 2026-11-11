from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from refunddesk.adapters.http.schemas import (
    ApprovalPage,
    AuditPage,
    ConfirmationOut,
    DetailOut,
    EvaluationOut,
    IdentityOut,
    LoginOut,
    OrderOut,
    PolicyOut,
    RefundOut,
)
from refunddesk.application.ports import Row
from refunddesk.application.services import Actor, BusinessError, IdentityService, RefundService

router = APIRouter(prefix='/api')


def error_status(code: str) -> int:
    return {
        'INVALID_CREDENTIALS': 401, 'UNAUTHENTICATED': 401,
        'CSRF_INVALID': 403, 'FORBIDDEN': 403, 'SELF_APPROVAL_FORBIDDEN': 403,
        'NOT_FOUND': 404, 'INVALID_ACTION': 422, 'INVALID_DECISION': 422,
        'INVALID_TIME_RANGE': 422, 'IDEMPOTENCY_KEY_REQUIRED': 422,
    }.get(code, 409)

Name = Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=True)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Input):
    user_id: Name
    tenant_id: Name
    password: str = Field(min_length=1, max_length=128)


class Confirmation(Input):
    action: Literal['create', 'cancel']
    target: Name
    reason: Reason


class Create(Input):
    order_no: Name
    reason: Reason
    confirmation_id: Name


class Cancel(Input):
    reason: Reason
    confirmation_id: Name


class Decision(Input):
    decision: Literal['approve', 'reject']
    note: Reason


def refunds(request: Request) -> RefundService:
    service: RefundService = request.app.state.refunds
    return service


def identities(request: Request) -> IdentityService:
    service: IdentityService = request.app.state.identities
    return service


async def current(request: Request) -> Actor:
    actor = await identities(request).authenticate(request.cookies.get('rd_session'),
        request.headers.get('X-CSRF-Token'), request.method not in {'GET', 'HEAD', 'OPTIONS'})
    request.state.actor = actor
    return actor


Current = Annotated[Actor, Depends(current)]


@router.post('/auth/login', response_model=LoginOut)
async def login(data: Login, request: Request, response: Response,
                x_csrf_protection: Annotated[str | None, Header()] = None) -> Row:
    # A custom header forces cross-origin browsers to preflight; no CORS credentials allowed.
    if x_csrf_protection != '1':
        raise BusinessError('CSRF_INVALID')
    origin = request.headers.get('Origin')
    expected: str = request.app.state.public_origin
    if origin and origin.rstrip('/') != (expected or str(request.base_url).rstrip('/')):
        raise BusinessError('CSRF_INVALID')
    result = await identities(request).login(data.user_id, data.password, data.tenant_id)
    old_token = request.cookies.get('rd_session')
    if old_token:
        await identities(request).logout(old_token)
    response.set_cookie('rd_session', result['token'], httponly=True, samesite='strict',
                        secure=request.app.state.cookie_secure, max_age=28800, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'csrf_token': result['csrf_token'], 'expires_at': result['expires_at']}


@router.post('/auth/logout', status_code=204)
async def logout(request: Request, actor: Current) -> Response:
    await identities(request).logout(request.cookies['rd_session'])
    response = Response(status_code=204)
    response.delete_cookie('rd_session', path='/', httponly=True, samesite='strict',
                           secure=request.app.state.cookie_secure)
    return response


@router.get('/auth/me', response_model=IdentityOut)
async def me(actor: Current) -> Row:
    return {'user_id': actor.user_id, 'tenant_id': actor.tenant_id, 'roles': sorted(actor.roles)}


@router.get('/orders/{order_no}', response_model=OrderOut)
async def order(order_no: Name, request: Request, actor: Current) -> Row:
    return await refunds(request).order(actor, order_no)


@router.get('/refund-policies/current', response_model=PolicyOut)
async def current_policy(request: Request, actor: Current) -> Row:
    return await refunds(request).policy(actor)


@router.get('/refund-policies/{version}', response_model=PolicyOut)
async def version_policy(version: Name, request: Request, actor: Current) -> Row:
    return await refunds(request).policy(actor, version)


@router.post('/orders/{order_no}/refund-evaluation', response_model=EvaluationOut)
async def evaluate(order_no: Name, request: Request, actor: Current) -> Row:
    return await refunds(request).evaluate(actor, order_no)


@router.post('/action-confirmations', status_code=201, response_model=ConfirmationOut)
async def confirm(data: Confirmation, request: Request, actor: Current) -> Row:
    return await refunds(request).confirm(actor, data.action, data.target, data.reason)


@router.post('/refunds', response_model=RefundOut)
async def create(data: Create, request: Request, actor: Current,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]) -> Row:
    return await refunds(request).write(actor, 'create', data.order_no, data.reason,
        data.confirmation_id, idempotency_key, request.state.trace)


@router.post('/refunds/{request_id}/cancel', response_model=RefundOut)
async def cancel(request_id: Name, data: Cancel, request: Request, actor: Current,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]) -> Row:
    return await refunds(request).write(actor, 'cancel', request_id, data.reason,
        data.confirmation_id, idempotency_key, request.state.trace)


@router.get('/refunds/{request_id}', response_model=DetailOut)
async def detail(request_id: Name, request: Request, actor: Current,
                 limit: Limit = 100, offset: Offset = 0) -> Row:
    return await refunds(request).detail(actor, request_id, limit, offset)


@router.get('/approvals', response_model=ApprovalPage)
async def approvals(request: Request, actor: Current, limit: Limit = 20, offset: Offset = 0) -> Row:
    return {'items': await refunds(request).approvals(actor, limit, offset),
            'limit': limit, 'offset': offset}


@router.post('/approvals/{request_id}/decision', response_model=RefundOut)
async def decision(request_id: Name, data: Decision, request: Request, actor: Current) -> Row:
    return await refunds(request).decide(actor, request_id, data.decision, data.note, request.state.trace)


@router.get('/audit-events', response_model=AuditPage)
async def audits(request: Request, actor: Current, limit: Limit = 20, offset: Offset = 0,
                 user_id: Name | None = None, order_no: Name | None = None,
                 request_id: Name | None = None, since: AwareDatetime | None = None,
                 until: AwareDatetime | None = None) -> Row:
    if since and until and since > until:
        raise BusinessError('INVALID_TIME_RANGE')
    return {'items': await refunds(request).audits(actor,
        {'user_id': user_id, 'order_no': order_no, 'request_id': request_id,
         'since': since, 'until': until}, limit, offset), 'limit': limit, 'offset': offset}
