"""Universal PMS-agnostic routes."""

from fastapi import APIRouter

from src.app.api.routes.universal.patients import router as patients_router
from src.app.api.routes.universal.slots import router as slots_router
from src.app.api.routes.universal.appointments import router as appointments_router
from src.app.api.routes.universal.appointment_types import router as appt_types_router
from src.app.api.routes.universal.providers import router as providers_router
from src.app.api.routes.universal.operatories import router as operatories_router
from src.app.api.routes.universal.locations import router as locations_router
from src.app.api.routes.universal.setup import router as setup_router

universal_router = APIRouter(prefix="/pms", tags=["Universal PMS"])
universal_router.include_router(patients_router)
universal_router.include_router(slots_router)
universal_router.include_router(appointments_router)
universal_router.include_router(appt_types_router)
universal_router.include_router(providers_router)
universal_router.include_router(operatories_router)
universal_router.include_router(locations_router)
universal_router.include_router(setup_router)
