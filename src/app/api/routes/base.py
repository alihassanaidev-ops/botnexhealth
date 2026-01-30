"""Base router with security and health check."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Header, status

from src.app.config import Settings
from src.app.dependencies import get_settings

logger = logging.getLogger(__name__)


async def verify_admin_key(
    x_admin_api_key: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> str:
    """Verify the Admin API Key header."""
    if not settings.admin_api_key:
        logger.error("No Admin API Key configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration",
        )
        
    if x_admin_api_key != settings.admin_api_key:
        logger.warning("Invalid Admin API Key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Admin API Key",
        )
    return x_admin_api_key


# Apply security dependency to all routes in this router
router = APIRouter(dependencies=[Depends(verify_admin_key)])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
