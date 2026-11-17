import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ScryptPasswords:
    def encode(self, password: str) -> str:
        if not 12 <= len(password) <= 128:
            raise ValueError('Password must contain 12 to 128 characters')
        salt = secrets.token_hex(16)
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return f'scrypt${salt}${key.hex()}'

    def verify(self, password: str, encoded: str | None) -> bool:
        # Do comparable work for nonexistent users, never log supplied credentials.
        try:
            method, salt, expected = (encoded or ('scrypt$' + '00' * 16 + '$' + '00' * 64)).split('$')
            if method != 'scrypt' or len(salt) != 32 or len(expected) != 128:
                return False
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
            return hmac.compare_digest(actual.hex(), expected) and encoded is not None
        except (ValueError, TypeError):
            return False


async def provision_password(engine: AsyncEngine, user_id: str, encoded: str) -> None:
    """Administrative bootstrap via privileged DB access, not an application endpoint."""
    async with engine.begin() as conn:
        user = await conn.scalar(text('SELECT id FROM users WHERE id=:id FOR UPDATE'), {'id': user_id})
        if user is None:
            raise ValueError('User does not exist')
        await conn.execute(text('''INSERT INTO user_credentials(user_id,password_hash)
            VALUES (:id,:hash) ON CONFLICT(user_id) DO UPDATE SET password_hash=EXCLUDED.password_hash'''),
            {'id': user_id, 'hash': encoded})
        await conn.execute(text('DELETE FROM auth_sessions WHERE user_id=:id'), {'id': user_id})
