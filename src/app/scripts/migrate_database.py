"""Run Alembic migrations against the configured database.

Thin wrapper around ``alembic upgrade head`` that:
  - Uses ``DATABASE_ADMIN_URL`` (privileged migration DSN) when set,
    falling back to ``DATABASE_URL``.
  - Auto-stamps a pre-existing database at a known baseline revision
    when ``ALEMBIC_BASELINE_REVISION`` is set — this is the cutover
    helper for an existing system that pre-dates the consolidated
    20260510_baseline migration.

For fresh deployments and most operations: just run this. No options
needed.
"""

from __future__ import annotations

import logging
import os

from src.app.config import settings
from src.app.scripts.bootstrap_database import upgrade_to_head

logger = logging.getLogger(__name__)

_BASELINE_ENV_VAR = "ALEMBIC_BASELINE_REVISION"


def main() -> int:
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    baseline = os.getenv(_BASELINE_ENV_VAR)
    if baseline:
        # Cutover path: stamp a pre-existing DB at the named revision before
        # running upgrade. Used when migrating an environment that was
        # provisioned before the consolidated baseline migration.
        from alembic import command
        from alembic.config import Config
        from src.app.scripts.bootstrap_database import _admin_database_url

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", _admin_database_url())
        logger.warning("Stamping existing schema at Alembic revision %s.", baseline)
        command.stamp(cfg, baseline)

    logger.warning("Running alembic upgrade head.")
    upgrade_to_head()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
