import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from refunddesk.application.services import payload_digest
from refunddesk.infrastructure.storage import ORDER_ADAPTER, SqlRepository


def test_repository_validates_database_values_before_returning_them():
    repo = SqlRepository(AsyncMock())
    order = {'tenant_id': 'tenant', 'order_no': 'order', 'amount_minor': 100,
             'currency': 'CNY', 'paid_at': datetime.now(UTC), 'shipped_at': None,
             'refund_state': 'none'}
    repo.one = AsyncMock(return_value=order)
    assert asyncio.run(repo.one_typed(ORDER_ADAPTER, 'unused', {})) == order
    repo.one = AsyncMock(return_value={**order, 'amount_minor': '100'})
    with pytest.raises(ValidationError):
        asyncio.run(repo.one_typed(ORDER_ADAPTER, 'unused', {}))
    repo.one = AsyncMock(return_value=None)
    assert asyncio.run(repo.one_typed(ORDER_ADAPTER, 'unused', {})) is None


def test_confirmation_digest_preserves_omitted_fields():
    snapshot = {'action': 'cancel', 'target': 'refund', 'reason': 'reason',
                'amount_minor': 100, 'currency': 'CNY'}
    assert payload_digest(snapshot) != payload_digest({**snapshot, 'policy_version': None})
