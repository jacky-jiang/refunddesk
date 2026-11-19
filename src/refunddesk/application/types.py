"""Typed data exchanged between the I1 use cases and their adapters."""
from datetime import datetime
from typing import NotRequired, TypedDict

from refunddesk.domain.refunds import EligibilityOrder


class Identity(TypedDict):
    id: str
    display_name: str
    roles: list[str]


class Session(TypedDict):
    token_hash: str
    user_id: str
    tenant_id: str
    csrf_hash: str
    expires_at: datetime
    created_at: datetime


class LoginResult(TypedDict):
    token: str
    csrf_token: str
    expires_at: datetime


class Order(EligibilityOrder):
    tenant_id: str
    order_no: str


class OrderResult(Order):
    refundable_amount_minor: int


class Policy(TypedDict):
    tenant_id: str
    version: str
    effective_at: datetime
    rules: dict[str, object]


class NewRefund(TypedDict):
    tenant_id: str
    id: str
    order_no: str
    applicant_id: str
    reason: str
    amount_minor: int
    currency: str
    policy_version: str
    created_at: datetime
    approve_by: datetime


class Refund(NewRefund):
    applicant_role: str
    state: str
    start_by: datetime | None
    close_reason: str | None


class Decision(TypedDict):
    tenant_id: str
    request_id: str
    applicant_id: str
    reviewer_id: str
    decision: str
    note: str
    decided_at: datetime


class Job(TypedDict):
    tenant_id: str
    id: str
    request_id: str
    run_at: datetime


class Audit(TypedDict):
    tenant_id: str
    id: str
    occurred_at: datetime
    actor: str
    request_id: str
    action: str
    details: dict[str, object]
    trace_id: str


class SecurityEvent(TypedDict):
    id: str
    occurred_at: datetime
    actor: str | None
    tenant_id: str | None
    code: str
    trace_id: str


class NewConfirmation(TypedDict):
    tenant_id: str
    user_id: str
    id: str
    action: str
    payload_digest: str
    expires_at: datetime


class Confirmation(NewConfirmation):
    consumed_at: datetime | None


class Idempotency(TypedDict):
    tenant_id: str
    user_id: str
    action: str
    key: str
    payload_digest: str
    request_id: str


class PaymentProgress(TypedDict):
    payment_key: str
    state: str
    channel_ref: str | None
    job_state: str | None
    reconcile_attempts: int | None
    run_at: datetime | None


class ManualKey(TypedDict):
    request_id: str


class DueRefund(TypedDict):
    tenant_id: str
    id: str


class AuditFilters(TypedDict, total=False):
    user_id: str | None
    order_no: str | None
    request_id: str | None
    since: datetime | None
    until: datetime | None


class Snapshot(TypedDict):
    action: str
    target: str
    reason: str
    amount_minor: int
    currency: str
    policy_version: NotRequired[str]


class ConfirmationResult(TypedDict):
    confirmation_id: str
    expires_at: datetime
    confirmed_action: Snapshot


class Evaluation(TypedDict):
    eligible: bool
    reason_codes: list[str]
    amount_minor: int
    currency: str
    policy_version: str
    evaluated_at: datetime


class Detail(TypedDict):
    request: Refund
    timeline: list[Audit]
    payment: PaymentProgress | None
    limit: int
    offset: int
