"""FastAPI application factory."""

import logging

from fastapi import FastAPI

from src.app.api.routes import router as api_router
from src.app.api.routes import public_router
from src.app.api.routes.tenants import router as tenants_router
from src.app.config import settings
from src.app.retell.functions import router as retell_router
from src.app.retell.webhooks import router as retell_webhook_router

logger = logging.getLogger(__name__)


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
    )

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
    app.include_router(tenants_router)

    @app.on_event("startup")
    async def startup() -> None:
        """Application startup event."""
        logger.info(f"Starting application in {settings.app_env} environment")
        
        # Initialize database if configured
        if settings.database_url:
            from src.app.database import init_database, create_tables
            logger.info("Initializing database connection...")
            init_database(settings.database_url)
            await create_tables()
            logger.info("Database initialized and tables created")
        
        # Initialize API clients
        from src.app.dependencies import init_nexhealth_client, init_sikka_client
        await init_nexhealth_client()
        await init_sikka_client()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        """Application shutdown event."""
        logger.info("Shutting down application")
        
        # Close database
        if settings.database_url:
            from src.app.database import close_database
            await close_database()
        
        # Cleanup API clients
        from src.app.dependencies import cleanup_nexhealth_client, cleanup_sikka_client
        await cleanup_nexhealth_client()
        await cleanup_sikka_client()

    return app


app = create_app()

