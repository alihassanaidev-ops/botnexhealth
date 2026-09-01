"""Print the current TOTP (MFA) code for a local dev demo account.

Local dev only: reads the enrolled TOTP secret straight out of the local
Postgres and derives the 6-digit code the login form is asking for, so a
seeded demo account can get past MFA without an authenticator app.

    python scripts/local_totp_code.py [email]
"""

from __future__ import annotations

import asyncio
import datetime
import os
import pathlib
import sys
import time

import asyncpg
import pyotp

# The local stack runs on .env.ui-local, which carries the ENCRYPTION_KEY the
# local database was actually seeded with. Settings otherwise loads .env, whose
# key does not match this data (and whose DATABASE_URL points at a hosted DB).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_ENV = pathlib.Path(__file__).resolve().parent.parent / ".env.ui-local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from src.app.config import settings
from src.app.models.institution import decrypt_value

DEFAULT_EMAIL = "inst.admin@bright-smile-dental.dev"


def _dsn() -> str:
    # asyncpg speaks plain postgresql://, not SQLAlchemy's +asyncpg dialect URL.
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAIL

    conn = await asyncpg.connect(_dsn())
    try:
        encrypted = await conn.fetchval(
            "SELECT t.secret_encrypted FROM user_totp_factors t"
            " JOIN users u ON u.id = t.user_id WHERE u.email = $1",
            email,
        )
    finally:
        await conn.close()

    if not encrypted:
        print(f"No TOTP factor enrolled for {email}.")
        print("Enrolled accounts:")
        conn = await asyncpg.connect(_dsn())
        try:
            for row in await conn.fetch(
                "SELECT u.email FROM user_totp_factors t"
                " JOIN users u ON u.id = t.user_id ORDER BY u.email"
            ):
                print(f"  - {row['email']}")
        finally:
            await conn.close()
        return 1

    secret = decrypt_value(encrypted)
    totp = pyotp.TOTP(secret)
    print(f"account: {email}")
    print(f"code:    {totp.now()}")
    print(f"window:  {30 - int(time.time()) % 30}s left")
    print(f"next:    {totp.at(datetime.datetime.now() + datetime.timedelta(seconds=30))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
