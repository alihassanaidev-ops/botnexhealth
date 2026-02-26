"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.app.api.routes import router as api_router
from src.app.api.routes import public_router
from src.app.api.routes.tenants import router as tenants_router
from src.app.config import settings
from src.app.retell.functions import router as retell_router
from src.app.retell.webhooks import router as retell_webhook_router
from src.app.api.routes.auth import router as auth_router
from src.app.api.routes.tenant_portal import router as tenant_portal_router
from src.app.api.routes.tenant_setup import router as tenant_setup_router
from src.app.api.routes.calls import router as calls_router
from src.app.api.routes.dashboard import router as dashboard_router
from src.app.api.routes.custom_fields import router as custom_fields_router
from src.app.api.routes.twilio import router as twilio_router

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
    
    # Initialize API clients
    from src.app.dependencies import init_nexhealth_client
    await init_nexhealth_client()
    
    yield  # Application runs here
    
    # === SHUTDOWN ===
    logger.info("Shutting down application")
    
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
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers (HSTS, X-Frame-Options, Cache-Control, etc.)
    from src.app.middleware.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting
    from src.app.api.rate_limit import limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Add tenant middleware (after CORS, before routes)
    if settings.database_url:
        from src.app.middleware.tenant import TenantMiddleware
        app.add_middleware(TenantMiddleware)

    # Public health check endpoints (no auth, for container probes)
    app.include_router(public_router, tags=["Health"])

    # API routes
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(retell_router, prefix="/api/v1")
    app.include_router(retell_webhook_router, prefix="/api/v1")
    
    # Admin routes
    app.include_router(auth_router)
    app.include_router(tenants_router)

    # Tenant portal routes (authenticated tenant users)
    app.include_router(tenant_portal_router)
    app.include_router(tenant_setup_router)
    app.include_router(calls_router)
    app.include_router(dashboard_router)
    app.include_router(custom_fields_router)
    app.include_router(twilio_router)

    return app


app = create_app()
