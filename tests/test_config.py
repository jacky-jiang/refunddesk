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
    def test_remote_plaintext_is_explicitly_supported(self) -> None:
        _, options = database_options()
        self.assertEqual(options, {"sslmode": "disable", "connect_timeout": 4})

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://u:p@localhost/dev",
                             "DB_SSLMODE": "disable", "APP_ENV": "test"}, clear=True)
    def test_local_ci_connection(self) -> None:
        _, options = database_options()
        self.assertEqual(options["sslmode"], "disable")

    def test_request_id_rejects_arbitrary_log_text(self) -> None:
        actual = request_id("invalid\nforged")
        self.assertEqual(actual, str(UUID(actual)))
        self.assertEqual(request_id(actual), actual)


class LightsailTests(unittest.TestCase):
    def config(self, **overrides: str) -> dict[str, str]:
        return {"DB_HOST": "192.0.2.10", "DB_PASSWORD": "p@ss:/?#% word",
                "DB_SSLMODE": "disable", **overrides}

    def test_components_and_special_password(self) -> None:
        from sqlalchemy.engine import make_url

        with patch.dict(os.environ, self.config(DB_PORT="55432"), clear=True):
            url, options = database_options()
        parsed = make_url(url)
        self.assertEqual(parsed.host, "192.0.2.10")
        self.assertEqual(parsed.port, 55432)
        self.assertEqual(parsed.database, "helloagents")
        self.assertEqual(parsed.username, "yijiang")
        self.assertEqual(parsed.password, "p@ss:/?#% word")
        self.assertNotIn("sslrootcert", options)

    def test_require_without_ca(self) -> None:
        with patch.dict(os.environ, self.config(DB_SSLMODE="require"), clear=True):
            _, options = database_options()
        self.assertEqual(options["sslmode"], "require")
        self.assertNotIn("sslrootcert", options)

    def test_missing_required_components(self) -> None:
        for missing in ("DB_HOST", "DB_PASSWORD"):
            config = self.config()
            del config[missing]
            with (self.subTest(missing=missing), patch.dict(os.environ, config, clear=True),
                  self.assertRaisesRegex(ValueError, "DB_HOST and DB_PASSWORD")):
                database_options()

    def test_invalid_port(self) -> None:
        for port in ("abc", "0", "65536"):
            with (self.subTest(port=port),
                  patch.dict(os.environ, self.config(DB_PORT=port), clear=True),
                  self.assertRaisesRegex(ValueError, "DB_PORT")):
                database_options()

    def test_url_and_migration_precedence(self) -> None:
        app = "postgresql+psycopg://app:password@localhost/appdb"
        ddl = "postgresql+psycopg://ddl:password@localhost/ddldb"
        with patch.dict(os.environ, self.config(DATABASE_URL=app), clear=True):
            self.assertEqual(database_options()[0], app)
            self.assertEqual(database_options(migration=True)[0], app)
            with patch.dict(os.environ, {"MIGRATION_DATABASE_URL": ddl}):
                self.assertEqual(database_options(migration=True)[0], ddl)
                self.assertEqual(database_options()[0], app)

    def test_invalid_sslmode(self) -> None:
        with (patch.dict(os.environ, self.config(DB_SSLMODE="prefer"), clear=True),
              self.assertRaisesRegex(ValueError, "Choose")):
            database_options()
