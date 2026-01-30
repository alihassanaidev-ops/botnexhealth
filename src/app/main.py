"""FastAPI application factory."""

import logging

from fastapi import FastAPI

from src.app.api.routes import router as api_router
from src.app.config import settings
from src.app.retell.functions import router as retell_router

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

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(retell_router, prefix="/api/v1")

    @app.on_event("startup")
    async def startup() -> None:
        """Application startup event."""
        logger.info(f"Starting application in {settings.app_env} environment")
        from src.app.dependencies import init_nexhealth_client
        await init_nexhealth_client()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        """Application shutdown event."""
        logger.info("Shutting down application")
        from src.app.dependencies import cleanup_nexhealth_client
        await cleanup_nexhealth_client()

    return app


app = create_app()
