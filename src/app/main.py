"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.app.api.routes import router as api_router
from src.app.api.routes import public_router
from src.app.api.routes.admin_institutions import router as institutions_router
from src.app.config import settings
from src.app.retell.functions import router as retell_router
from src.app.retell.webhooks import router as retell_webhook_router
from src.app.api.routes.auth import router as auth_router
from src.app.api.routes.institution_portal import router as institution_portal_router
from src.app.api.routes.institution_setup import router as institution_setup_router
from src.app.api.routes.calls import router as calls_router
from src.app.api.routes.dashboard import router as dashboard_router
from src.app.api.routes.custom_fields import router as custom_fields_router
from src.app.api.routes.notifications import router as notifications_router
from src.app.api.routes.callbacks import router as callbacks_router
from src.app.api.routes.email_templates import router as email_templates_router
from src.app.api.routes.notification_preferences import router as notification_preferences_router
from src.app.api.routes.notification_recipients import router as notification_recipients_router
from src.app.api.routes.sse import router as sse_router
from src.app.api.routes.twilio import router as twilio_router
from src.app.api.routes.twilio_webhooks import router as twilio_webhooks_router
from src.app.api.routes.sms import admin_router as admin_sms_router
from src.app.api.routes.sms import institution_router as institution_sms_router
from src.app.api.routes.dead_letter import router as dead_letter_router

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # === STARTUP ===
    logger.info(f"Starting application in {settings.app_env} environment")

    # Initialize database if configured
    if settings.database_url:
        from src.app.database import init_database, create_tables
        logger.info("Initializing database connection...")
        init_database(settings.database_url)
        # Only auto-create tables in local/dev — production should use migrations
        if settings.app_env in ("local", "dev", "test"):
            await create_tables()
            logger.info("Database initialized and dev tables created")
        else:
            logger.info("Database initialized (production — tables managed by migrations)")

        # Verify every tenant-scoped table has RLS enabled. A table with an
        # institution_id column but relrowsecurity=false is a tenant-isolation
        # bug that lets cross-tenant data leak — log CRITICAL so it is loud
        # in production. Skipped under the test env so unit tests don't hit
        # a real DB; broad except handles fresh DBs that haven't migrated yet.
        if settings.app_env != "test":
            from sqlalchemy import text as _text
            from src.app.database import get_system_db_session

            try:
                async with get_system_db_session(
                    "user",
                    role="SUPER_ADMIN",
                    user_id="00000000-0000-0000-0000-000000000000",
                ) as _verify_session:
                    result = await _verify_session.execute(
                        _text(
                            """
                            SELECT c.relname
                            FROM pg_class c
                            JOIN pg_namespace n ON c.relnamespace = n.oid
                            WHERE n.nspname = 'public'
                              AND c.relkind = 'r'
                              AND c.relrowsecurity = false
                              AND EXISTS (
                                  SELECT 1 FROM information_schema.columns
                                  WHERE table_schema = 'public'
                                    AND table_name = c.relname
                                    AND column_name = 'institution_id'
                              )
                            """
                        )
                    )
                    missing = [row[0] for row in result.fetchall()]
                    if missing:
                        logger.critical(
                            "RLS NOT ENABLED on tenant-scoped tables: %s — "
                            "cross-tenant data exposure possible. Run RLS "
                            "migration immediately.",
                            ", ".join(sorted(missing)),
                        )
                    else:
                        logger.info("RLS verification passed on all tenant-scoped tables")
            except Exception as exc:  # noqa: BLE001
                # Fresh DB before migrations, or transient catalog issue —
                # do not crash startup.
                logger.info(
                    "Skipping RLS startup verification (DB not ready or "
                    "catalog query failed): %s",
                    exc,
                )

    # Initialize API clients
    from src.app.dependencies import init_nexhealth_client
    await init_nexhealth_client()

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Shutting down application")

    # Drain in-flight best-effort audit writes BEFORE closing the database —
    # otherwise SIGTERM / rolling-deploys silently lose audit rows for
    # actions that already committed (login, dashboard view, callback
    # resolve, etc). Bounded by the service's own timeout so a wedged audit
    # DB cannot block shutdown indefinitely.
    if settings.database_url:
        from src.app.services.audit import AuditService
        await AuditService.drain_background_tasks()

    # Close database
    if settings.database_url:
        from src.app.database import close_database
        await close_database()

    # Cleanup API clients
    from src.app.dependencies import cleanup_nexhealth_client
    await cleanup_nexhealth_client()


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI app instance
    """
    app = FastAPI(
        title="NexHealth Voice Agent Backend",
        description="HIPAA-minded backend for voice agent integration with NexHealth",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add CORS middleware (must be first)
    from fastapi.middleware.cors import CORSMiddleware

    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "x-request-id",
            "x-institution-slug",
            "x-location-slug",
        ],
        expose_headers=["x-request-id"],
        max_age=600,
    )

    # Security headers (HSTS, X-Frame-Options, Cache-Control, etc.)
    from src.app.middleware.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)

    # Starlette runs middleware in REVERSE order of registration: the LAST
    # add_middleware call wraps the others and runs FIRST on inbound requests.
    # The chain below is registered inner-to-outer so the runtime order is:
    #   RequestID → SlowAPI (rate limit) → Institution (DB lookup) → security
    #   headers → CORS → app
    # Rate limiting must run BEFORE the institution DB lookup so a flood of
    # unauthenticated requests with random X-Institution-Slug headers cannot
    # amplify into per-request DB queries.

    # Institution middleware — innermost of the request-shaping middlewares.
    if settings.database_url:
        from src.app.middleware.institution import InstitutionMiddleware
        app.add_middleware(InstitutionMiddleware)

    # Rate limiting — registered AFTER InstitutionMiddleware so it wraps it
    # and runs first on inbound requests.
    from src.app.api.rate_limit import limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Durable audit-write failures must not surface as 500 — the action
    # itself usually committed; the audit row is what's missing. Map to 503
    # so clients understand "retry safe-ish" semantics, and emit a CRITICAL
    # log line that operators can pivot off to reconcile via request_id.
    from fastapi.responses import JSONResponse
    from src.app.services.audit import AuditPersistenceError

    @app.exception_handler(AuditPersistenceError)
    async def _audit_persistence_error_handler(request, exc):  # type: ignore[no-redef]
        logger.critical(
            "AUDIT PERSISTENCE FAILURE on %s %s: %s",
            request.method, request.url.path, exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Audit log unavailable; the action could not be safely "
                    "recorded. Please retry."
                )
            },
        )

    # Request ID — outermost, so every response (including 429s and CORS
    # preflight rejections) carries a correlation id for log triage.
    from src.app.middleware.request_id import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)

    # Public health check endpoints (no auth, for container probes)
    app.include_router(public_router, tags=["Health"])

    # API routes
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(retell_router, prefix="/api/v1")
    app.include_router(retell_webhook_router, prefix="/api/v1")
    app.include_router(twilio_webhooks_router, prefix="/api/v1")

    # Admin routes
    app.include_router(auth_router, prefix="/api")
    app.include_router(institutions_router, prefix="/api")

    # Institution portal routes (authenticated institution users)
    app.include_router(institution_portal_router, prefix="/api")
    app.include_router(institution_setup_router, prefix="/api")
    app.include_router(calls_router, prefix="/api")
    app.include_router(dashboard_router, prefix="/api")
    app.include_router(custom_fields_router, prefix="/api")
    app.include_router(notifications_router, prefix="/api")
    app.include_router(callbacks_router, prefix="/api")
    app.include_router(email_templates_router, prefix="/api")
    app.include_router(notification_preferences_router, prefix="/api")
    app.include_router(notification_recipients_router, prefix="/api")
    app.include_router(sse_router, prefix="/api")
    app.include_router(twilio_router, prefix="/api")
    app.include_router(admin_sms_router, prefix="/api")
    app.include_router(institution_sms_router, prefix="/api")
    app.include_router(dead_letter_router, prefix="/api")

    return app


app = create_app()
