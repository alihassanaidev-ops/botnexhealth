"""API Routes Package - Combines all resource routers."""

from fastapi import APIRouter

from src.app.api.routes.base import router as base_router

# ============================================================================
# Voice Agent - Keep These Routes
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

# Main router that combines all sub-routers
router = APIRouter()

router.include_router(base_router)

# ============================================================================
# Active Routes for Voice Agent
# ============================================================================
router.include_router(institutions_router, prefix="/nexhealth", tags=["Institutions"])
router.include_router(availabilities_router, prefix="/nexhealth", tags=["Availabilities"])
router.include_router(operatories_router, prefix="/nexhealth", tags=["Operatories"])
router.include_router(appointments_router, prefix="/nexhealth", tags=["Appointments"])
router.include_router(appointment_slots_router, prefix="/nexhealth", tags=["Appointment Slots"])
router.include_router(locations_router, prefix="/nexhealth", tags=["Locations"])
router.include_router(patients_router, prefix="/nexhealth", tags=["Patients"])
router.include_router(providers_router, prefix="/nexhealth", tags=["Providers"])
router.include_router(appointment_types_router, prefix="/nexhealth", tags=["Appointment Types"])
