"""Bootstrap an empty database by running ``alembic upgrade head``.

This is a thin wrapper. The single source of truth for schema is the
alembic migrations under ``alembic/versions/`` — no
``Base.metadata.create_all`` here. ``alembic upgrade head`` against an
empty DB lands a fully-correct schema (tables, indexes, constraints,
audit triggers, RLS policies, SECURITY DEFINER helpers, BYPASSRLS
role).

Requires ``DATABASE_ADMIN_URL`` (or ``DATABASE_URL`` as fallback) to
point at a connection that can:
  - ``CREATE`` on the public schema
  - ``CREATE ROLE ... BYPASSRLS`` (i.e. SUPERUSER or rds_superuser)
  - ``ALTER FUNCTION ... OWNER TO`` arbitrary roles

After deployment, application traffic uses the runtime DSN
(``DATABASE_URL``) which should NOT have those privileges — RLS gives
real defense-in-depth only when the runtime role lacks BYPASSRLS.
"""

from __future__ import annotations

import logging
import os

from alembic import command
from alembic.config import Config

from src.app.config import settings

logger = logging.getLogger(__name__)


def _admin_database_url() -> str:
    """Return the DSN bootstrap uses for migrations.

    Prefers ``DATABASE_ADMIN_URL`` so deployments can keep the runtime
    DSN (no BYPASSRLS) separate from the migration DSN. Falls back to
    ``DATABASE_URL`` for the simple-local case where one role does both.
    """
    return os.environ.get("DATABASE_ADMIN_URL") or settings.database_url


def upgrade_to_head() -> None:
    """Run ``alembic upgrade head`` using the admin DSN.

    Idempotent — alembic's stamping makes this safe to re-run on a DB
    that's already at head (no-op).
    """
    admin_url = _admin_database_url()
    if not admin_url:
        raise RuntimeError(
            "DATABASE_URL (or DATABASE_ADMIN_URL) is not set; cannot bootstrap"
        )

    alembic_cfg = Config("alembic.ini")
    # Double `%` so configparser's BasicInterpolation passes the literal
    # character through — RDS-generated passwords containing URL-encoded
    # special chars (e.g. `%5E`) otherwise blow up at set time.
    alembic_cfg.set_main_option("sqlalchemy.url", admin_url.replace("%", "%%"))
    if admin_url != settings.database_url:
        logger.warning(
            "Bootstrap will run alembic upgrade head with DATABASE_ADMIN_URL "
            "(separate from runtime DATABASE_URL)."
        )
    else:
        logger.warning("Bootstrap will run alembic upgrade head with DATABASE_URL.")
    command.upgrade(alembic_cfg, "head")


def main() -> int:
    if not settings.database_url:
        logger.info("DATABASE_URL is not set; skipping bootstrap.")
        return 0
    upgrade_to_head()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
