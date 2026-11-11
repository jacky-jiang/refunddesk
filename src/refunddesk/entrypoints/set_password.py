"""Provision a password for an existing user; never create default credentials."""
import argparse
import asyncio
import getpass

from sqlalchemy.ext.asyncio import create_async_engine

from refunddesk.infrastructure.identity import ScryptPasswords, provision_password
from refunddesk.settings import database_options


async def provision(user_id: str, encoded: str) -> None:
    url, options = database_options(migration=True)
    engine = create_async_engine(url, connect_args=options, hide_parameters=True)
    try:
        await provision_password(engine, user_id, encoded)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('user_id')
    args = parser.parse_args()
    first = getpass.getpass('New password (12–128 characters): ')
    if first != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match')
    asyncio.run(provision(args.user_id, ScryptPasswords().encode(first)))
    print('Password updated; existing sessions revoked.')
