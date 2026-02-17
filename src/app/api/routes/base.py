"""Base router with security and health check."""

from fastapi import APIRouter, Depends

from src.app.api.deps import get_current_user

# Public router for health checks (no auth required)
public_router = APIRouter()


@public_router.get("/livez")
async def liveness_probe() -> dict[str, str]:
    """Liveness probe for container health checks (no auth required)."""
    return {"status": "alive"}


@public_router.get("/readyz")
async def readiness_probe() -> dict[str, str]:
    """Readiness probe for load balancer health checks (no auth required)."""
    return {"status": "ready"}


# Secured router for authenticated endpoints
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint (requires authentication)."""
    return {"status": "ok"}
