"""Validated at point of use so a liveness probe never opens the database."""
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import load_dotenv


def load_environment() -> None:
    """Load the working directory's .env once at startup; process values win."""
    load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)


load_environment()


def database_schema() -> str:
    schema = os.getenv("DB_SCHEMA", "refunddesk")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) or schema.startswith("pg_"):
        raise ValueError("DB_SCHEMA must be a lowercase PostgreSQL identifier outside pg_ schemas")
    return schema


def database_options(*, migration: bool = False) -> tuple[str, dict[str, str | int]]:
    url = (os.getenv("MIGRATION_DATABASE_URL") if migration else None) or os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("DB_HOST")
        password = os.getenv("DB_PASSWORD")
        if not host or not password:
            raise ValueError("Set DB_HOST and DB_PASSWORD, or DATABASE_URL")
        if any(char in host for char in "/@?#[]") or any(char.isspace() for char in host):
            raise ValueError("DB_HOST must be an IP address or hostname without a port or URL")
        try:
            port = int(os.getenv("DB_PORT", "5432"))
        except ValueError:
            raise ValueError("DB_PORT must be an integer between 1 and 65535") from None
        if not 1 <= port <= 65535:
            raise ValueError("DB_PORT must be an integer between 1 and 65535")
        user = os.getenv("DB_USER", "yijiang")
        database = os.getenv("DB_NAME", "helloagents")
        if not user or not database:
            raise ValueError("DB_USER and DB_NAME must not be empty")
        authority = f"[{host}]" if ":" in host else host
        url = (f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}"
               f"@{authority}:{port}/{quote(database, safe='')}")
    parts = urlsplit(url)
    if parts.scheme != "postgresql+psycopg" or not parts.hostname or not parts.path.strip("/"):
        raise ValueError("Expected a PostgreSQL psycopg URL with hostname and database")
    if parts.query or parts.fragment:
        raise ValueError("Use DB_SSLMODE and DB_SSL_ROOT_CERT; percent-encode password characters")
    mode = os.getenv("DB_SSLMODE", "verify-full")
    options: dict[str, str | int] = {"sslmode": mode, "connect_timeout": 4,
                                     "options": f"-csearch_path={database_schema()},pg_catalog"}
    if mode == "verify-full":
        ca = os.getenv("DB_SSL_ROOT_CERT")
        if not ca or not Path(ca).is_file():
            raise ValueError("Set DB_SSL_ROOT_CERT to a readable provider CA file")
        options["sslrootcert"] = ca
    elif mode not in {"disable", "require"}:
        raise ValueError("Choose verify-full, require, or disable")
    return url, options
