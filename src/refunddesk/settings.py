"""Validated at point of use so a liveness probe never opens the database."""
import os
from pathlib import Path
from urllib.parse import urlsplit


def database_options(*, migration: bool = False) -> tuple[str, dict[str, str | int]]:
    url = (os.getenv("MIGRATION_DATABASE_URL") if migration else None) or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL is required")
    parts = urlsplit(url)
    if parts.scheme != "postgresql+psycopg" or not parts.hostname or not parts.path.strip("/"):
        raise ValueError("Expected a PostgreSQL psycopg URL with hostname and database")
    if parts.query or parts.fragment:
        raise ValueError("Use DB_SSLMODE and DB_SSL_ROOT_CERT; percent-encode password characters")
    mode = os.getenv("DB_SSLMODE", "verify-full")
    options: dict[str, str | int] = {"sslmode": mode, "connect_timeout": 4}
    if mode == "disable":
        if (os.getenv("APP_ENV") not in {"development", "test"}
                or parts.hostname not in {"localhost", "127.0.0.1", "::1", "db"}):
            raise ValueError("Unencrypted connection permitted only for local development/test")
    elif mode == "verify-full":
        ca = os.getenv("DB_SSL_ROOT_CERT")
        if not ca or not Path(ca).is_file():
            raise ValueError("Set DB_SSL_ROOT_CERT to a readable provider CA file")
        options["sslrootcert"] = ca
    else:
        raise ValueError("Choose verify-full or local-only disable")
    return url, options
