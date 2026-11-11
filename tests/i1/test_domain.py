from datetime import UTC, datetime, timedelta

import pytest

from refunddesk.domain.refunds import eligibility, expiration

NOW = datetime(2026, 11, 11, tzinfo=UTC)


@pytest.mark.parametrize(('changes', 'expected'), [
    ({}, []),
    ({'paid_at': None}, ['ORDER_UNPAID']),
    ({'paid_at': NOW + timedelta(microseconds=1)}, ['PAYMENT_TIME_INVALID']),
    ({'paid_at': NOW - timedelta(hours=168)}, []),
    ({'paid_at': NOW - timedelta(hours=168, microseconds=1)}, ['REFUND_WINDOW_EXPIRED']),
    ({'shipped_at': NOW}, ['ORDER_SHIPPED']),
    ({'refund_state': 'refunded'}, ['ORDER_ALREADY_REFUNDED']),
    ({'refund_state': 'reserved'}, ['REFUND_RESERVED']),
    ({'amount_minor': 0}, ['AMOUNT_INVALID']),
])
def test_eligibility(changes, expected):
    order = {'paid_at': NOW - timedelta(days=1), 'shipped_at': None,
             'amount_minor': 12000, 'currency': 'CNY', 'refund_state': 'none', **changes}
    assert eligibility(order, NOW) == expected


@pytest.mark.parametrize(('state', 'reason'), [
    ('pending', 'APPROVAL_EXPIRED'), ('approved', 'EXECUTION_EXPIRED'),
    ('executing', None), ('reconciling', None), ('manual', None), ('succeeded', None),
])
def test_expiry_boundary(state, reason):
    row = {'state': state, 'approve_by': NOW, 'start_by': NOW}
    assert expiration(row, NOW) == reason
    assert expiration(row, NOW - timedelta(microseconds=1)) is None
