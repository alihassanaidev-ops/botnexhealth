"""API Routes Package - Combines all resource routers."""

from fastapi import APIRouter

from src.app.api.routes.base import router as base_router
from src.app.api.routes.base import public_router

# ============================================================================
# NexHealth Routes (Voice Agent)
# ============================================================================
from src.app.api.routes.institutions import router as institutions_router
from src.app.api.routes.appointments import router as appointments_router
from src.app.api.routes.appointment_slots import router as appointment_slots_router
from src.app.api.routes.availabilities import router as availabilities_router
from src.app.api.routes.locations import router as locations_router
from src.app.api.routes.operatories import router as operatories_router
from src.app.api.routes.patients import router as patients_router
from src.app.api.routes.providers import router as providers_router
from src.app.api.routes.appointment_types import router as appointment_types_router

# ============================================================================
# Sikka Routes
# ============================================================================
from src.app.api.routes.sikka import router as sikka_router
from src.app.api.routes.sikka import public_router as sikka_public_router

# Main router that combines all sub-routers
router = APIRouter()

router.include_router(base_router)

# ============================================================================
# NexHealth Routes
# ============================================================================
router.include_router(institutions_router, prefix="/nexhealth", tags=["NexHealth - Institutions"])
router.include_router(availabilities_router, prefix="/nexhealth", tags=["NexHealth - Availabilities"])
router.include_router(operatories_router, prefix="/nexhealth", tags=["NexHealth - Operatories"])
router.include_router(appointments_router, prefix="/nexhealth", tags=["NexHealth - Appointments"])
router.include_router(appointment_slots_router, prefix="/nexhealth", tags=["NexHealth - Appointment Slots"])
router.include_router(locations_router, prefix="/nexhealth", tags=["NexHealth - Locations"])
router.include_router(patients_router, prefix="/nexhealth", tags=["NexHealth - Patients"])
router.include_router(providers_router, prefix="/nexhealth", tags=["NexHealth - Providers"])
router.include_router(appointment_types_router, prefix="/nexhealth", tags=["NexHealth - Appointment Types"])

# ============================================================================
# Sikka Routes
# ============================================================================
router.include_router(sikka_router, prefix="/sikka", tags=["Sikka"])
router.include_router(sikka_public_router, prefix="/sikka", tags=["Sikka - OAuth"])
