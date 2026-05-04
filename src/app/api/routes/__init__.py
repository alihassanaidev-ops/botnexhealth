"""API Routes Package - Combines all resource routers."""

from fastapi import APIRouter

from src.app.api.routes.base import router as base_router
from src.app.api.routes.base import public_router as public_router

# ============================================================================
# NexHealth Routes (Super Admin only)
# ============================================================================
from src.app.api.routes.institutions import router as institutions_router
from src.app.api.routes.locations import router as locations_router


# ============================================================================
# Universal PMS Routes (adapter-based, PMS-agnostic)
# ============================================================================
from src.app.api.routes.universal import universal_router

# Main router that combines all sub-routers
router = APIRouter()

router.include_router(base_router)

# ============================================================================
# NexHealth Routes
# ============================================================================
router.include_router(institutions_router, prefix="/nexhealth", tags=["NexHealth - Institutions"])
router.include_router(locations_router, prefix="/nexhealth", tags=["NexHealth - Locations"])


# ============================================================================
# Universal PMS Routes
# ============================================================================
router.include_router(universal_router)
