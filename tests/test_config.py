import os
import unittest
from unittest.mock import patch
from uuid import UUID

from refunddesk.events import request_id
from refunddesk.settings import database_options


class FoundationTests(unittest.TestCase):
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://u:p@db.example/dev"}, clear=True)
    def test_cloud_requires_ca(self) -> None:
        with self.assertRaisesRegex(ValueError, "DB_SSL_ROOT_CERT"):
            database_options()

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://u:p@db.example/dev",
                             "DB_SSLMODE": "disable", "APP_ENV": "development"}, clear=True)
    def test_remote_plaintext_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unencrypted"):
            database_options()

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://u:p@localhost/dev",
                             "DB_SSLMODE": "disable", "APP_ENV": "test"}, clear=True)
    def test_local_ci_connection(self) -> None:
        _, options = database_options()
        self.assertEqual(options["sslmode"], "disable")

    def test_request_id_rejects_arbitrary_log_text(self) -> None:
        actual = request_id("invalid\nforged")
        self.assertEqual(actual, str(UUID(actual)))
        self.assertEqual(request_id(actual), actual)
