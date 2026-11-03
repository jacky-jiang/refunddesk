from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from refunddesk.entrypoints.api import app
from refunddesk.entrypoints.payment import app as payment


def test_api_with_migrated_db() -> None:
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").json() == {"status": "ready"}
        with patch("refunddesk.entrypoints.api.check_schema", AsyncMock(side_effect=OSError)):
            response = client.get("/health/ready")
            assert response.status_code == 503
            error = response.json()["error"]
            assert error["request_id"] == response.headers["X-Request-ID"]
            assert error["code"] == "DATABASE_NOT_READY"
            assert client.get("/health/live").status_code == 200


def test_payment_liveness() -> None:
    with TestClient(payment) as client:
        assert client.get("/health/live").json()["service"] == "mock-payment"
