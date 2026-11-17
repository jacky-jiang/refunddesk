from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class IdentityOut(BaseModel):
    user_id: str
    tenant_id: str
    roles: list[str]


class LoginOut(BaseModel):
    csrf_token: str
    expires_at: datetime


class OrderOut(BaseModel):
    tenant_id: str
    order_no: str
    amount_minor: int
    currency: str
    paid_at: datetime | None
    shipped_at: datetime | None
    refund_state: str
    refundable_amount_minor: int


class PolicyOut(BaseModel):
    tenant_id: str
    version: str
    effective_at: datetime
    rules: dict[str, object]


class EvaluationOut(BaseModel):
    eligible: bool
    reason_codes: list[str]
    amount_minor: int
    currency: str
    policy_version: str
    evaluated_at: datetime


class ActionSnapshot(BaseModel):
    action: Literal['create', 'cancel']
    target: str
    reason: str
    amount_minor: int
    currency: str
    policy_version: str | None = None


class ConfirmationOut(BaseModel):
    confirmation_id: str
    expires_at: datetime
    confirmed_action: ActionSnapshot


class RefundOut(BaseModel):
    tenant_id: str
    id: str
    order_no: str
    applicant_id: str
    applicant_role: str
    reason: str
    amount_minor: int
    currency: str
    policy_version: str
    state: str
    close_reason: str | None
    created_at: datetime
    approve_by: datetime
    start_by: datetime | None


class AuditOut(BaseModel):
    tenant_id: str
    id: str
    occurred_at: datetime
    actor: str
    request_id: str
    action: str
    details: dict[str, object]
    trace_id: str


class DetailOut(BaseModel):
    request: RefundOut
    timeline: list[AuditOut]
    limit: int
    offset: int


class ApprovalPage(BaseModel):
    items: list[RefundOut]
    limit: int
    offset: int


class AuditPage(BaseModel):
    items: list[AuditOut]
    limit: int
    offset: int
