from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


class BusinessError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    user_id: str
    roles: frozenset[str]

    def require(self, role: str) -> None:
        if role not in self.roles:
            raise BusinessError('FORBIDDEN')


def eligibility(order: dict[str, Any], now: datetime) -> list[str]:
    reasons = []
    paid = order['paid_at']
    if paid is None:
        reasons.append('ORDER_UNPAID')
    elif paid > now:
        reasons.append('PAYMENT_TIME_INVALID')
    elif now > paid + timedelta(hours=168):
        reasons.append('REFUND_WINDOW_EXPIRED')
    if order['shipped_at'] is not None:
        reasons.append('ORDER_SHIPPED')
    if order['refund_state'] != 'none':
        reasons.append('ORDER_ALREADY_REFUNDED' if order['refund_state'] == 'refunded'
                       else 'REFUND_RESERVED')
    if order['amount_minor'] <= 0 or order['currency'] != 'CNY':
        reasons.append('AMOUNT_INVALID')
    return reasons


def expiration(request: dict[str, Any], now: datetime) -> str | None:
    if request['state'] == 'pending' and now >= request['approve_by']:
        return 'APPROVAL_EXPIRED'
    if (request['state'] == 'approved' and request['start_by'] is not None
            and now >= request['start_by']):
        return 'EXECUTION_EXPIRED'
    return None
