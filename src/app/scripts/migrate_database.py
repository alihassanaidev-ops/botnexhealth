"""Run Alembic migrations and provision the runtime DB role.

Order of operations (each step exits non-zero on failure):
  1. Optional baseline stamp for cutover from a pre-baseline DB
     (``ALEMBIC_BASELINE_REVISION`` env var).
  2. ``alembic upgrade head`` against the admin DSN.
  3. Idempotent provisioning of the ``nexhealth_app`` runtime role +
     least-privilege grants, when ``APP_ROLE_SECRET_ARN`` is set.

Step 3 must run AFTER alembic so any new tables/sequences/functions
created by this deploy are covered by the GRANT statements (existing
objects) and ALTER DEFAULT PRIVILEGES (objects future migrations
will create as the master).

Designed for the AWS deploy flow: the migration ECS task runs as
master credentials and bootstraps the runtime role; API + worker
tasks then connect using the runtime role only.

For local dev: run with ``DATABASE_URL`` pointing at master.
APP_ROLE_SECRET_ARN is unset, so step 3 is skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urlparse

from src.app.config import settings
from src.app.scripts.bootstrap_database import upgrade_to_head

logger = logging.getLogger(__name__)

_BASELINE_ENV_VAR = "ALEMBIC_BASELINE_REVISION"
_APP_ROLE_SECRET_ENV = "APP_ROLE_SECRET_ARN"
_APP_ROLE_NAME = "nexhealth_app"


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Strip the SQLAlchemy driver prefix so asyncpg.connect accepts the URL."""
    if sqlalchemy_url.startswith("postgresql+asyncpg://"):
        return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if sqlalchemy_url.startswith("postgresql+psycopg2://"):
        return sqlalchemy_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return sqlalchemy_url


def _read_app_role_credentials(secret_arn: str) -> tuple[str, str]:
    """Fetch ``{username, password}`` for the runtime role from Secrets Manager."""
    import boto3  # imported lazily so local dev without boto3 still works

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_arn)
    payload = json.loads(response["SecretString"])
    username = payload.get("username") or _APP_ROLE_NAME
    password = payload["password"]
    return username, password


async def _provision_app_role(secret_arn: str) -> None:
    """Idempotently create + grant on the least-privilege runtime role.

    Runs as the RDS master (the migration task's credentials). The
    runtime role is created NOBYPASSRLS so the row-level-security
    policies in the consolidated baseline are actually enforced for
    application traffic — see the policy DDL in
    alembic/versions/20260510_consolidated_baseline.py.
    """
    import asyncpg

    username, password = _read_app_role_credentials(secret_arn)
    if username != _APP_ROLE_NAME:
        # The CDK-provisioned secret hardcodes the username. Diverging
        # from the convention would silently slip through GRANT calls
        # below; better to refuse than provision the wrong role.
        raise RuntimeError(
            f"App role secret has unexpected username={username!r}; "
            f"expected {_APP_ROLE_NAME!r}."
        )

    admin_url = os.environ.get("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise RuntimeError(
            "DATABASE_URL (or DATABASE_ADMIN_URL) is not set; cannot provision app role"
        )
    dsn = _asyncpg_dsn(admin_url)
    parsed = urlparse(dsn)
    master_role = parsed.username

    conn = await asyncpg.connect(dsn=dsn)
    try:
        # Existence check uses a real parameter; CREATE/ALTER ROLE can't
        # parameterise role/password (DDL), so we escape single quotes
        # defensively. The Secrets Manager generator runs with
        # exclude_punctuation=True so the password contains no quote
        # characters in practice — the replace() is belt-and-braces.
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", _APP_ROLE_NAME
        )
        escaped_password = password.replace("'", "''")
        if exists:
            await conn.execute(
                f"ALTER ROLE {_APP_ROLE_NAME} "
                f"WITH LOGIN NOBYPASSRLS PASSWORD '{escaped_password}'"
            )
        else:
            await conn.execute(
                f"CREATE ROLE {_APP_ROLE_NAME} "
                f"LOGIN NOBYPASSRLS PASSWORD '{escaped_password}'"
            )

        # Existing objects.
        await conn.execute("GRANT USAGE ON SCHEMA public TO nexhealth_app;")
        await conn.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexhealth_app;"
        )
        await conn.execute(
            "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO nexhealth_app;"
        )
        await conn.execute(
            "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO nexhealth_app;"
        )

        # Default privileges for objects future migrations create as
        # master. ALTER DEFAULT PRIVILEGES is owner-scoped — without
        # FOR ROLE master_role, future tables created by master would
        # land inaccessible to nexhealth_app and the next deploy would
        # silently break runtime queries.
        if master_role:
            for grant_sql in (
                "ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexhealth_app;",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public "
                "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO nexhealth_app;",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public "
                "GRANT EXECUTE ON FUNCTIONS TO nexhealth_app;",
            ):
                await conn.execute(grant_sql.format(role=master_role))
        else:
            logger.warning(
                "Could not derive master role from admin DSN — skipping "
                "ALTER DEFAULT PRIVILEGES. Future tables created by master "
                "may be inaccessible to nexhealth_app."
            )
    finally:
        await conn.close()


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

    app_role_secret_arn = os.getenv(_APP_ROLE_SECRET_ENV)
    if app_role_secret_arn:
        logger.warning(
            "Provisioning %s runtime role + grants from %s.",
            _APP_ROLE_NAME,
            app_role_secret_arn,
        )
        asyncio.run(_provision_app_role(app_role_secret_arn))
    else:
        logger.info(
            "%s not set — skipping runtime role provisioning. "
            "API/worker will connect with whatever DATABASE_USER they're handed.",
            _APP_ROLE_SECRET_ENV,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
