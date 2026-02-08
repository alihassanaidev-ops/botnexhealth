"""Abstract base for PMS adapters.

Every PMS adapter implements PMSAdapter. Optional capabilities
(appointment type creation, availability linking) are separate ABCs
that adapters can opt into.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from src.app.pms.models import (
    BookingRequest,
    BookingResult,
    PatientCreateRequest,
    SetupStep,
    UniversalAppointmentType,
    UniversalLocation,
    UniversalOperatory,
    UniversalPatient,
    UniversalProvider,
    UniversalSlot,
)


class PMSAdapter(ABC):
    """Core operations every PMS adapter must implement."""

    source: str  # "nexhealth" | "sikka"

    # --- Patients ---

    @abstractmethod
    async def search_patients(self, query: str, **kwargs: Any) -> list[UniversalPatient]:
        ...

    @abstractmethod
    async def create_patient(self, req: PatientCreateRequest) -> dict[str, Any]:
        ...

    # --- Appointment Types ---

    @abstractmethod
    async def list_appointment_types(self) -> list[UniversalAppointmentType]:
        ...

    # --- Providers ---

    @abstractmethod
    async def list_providers(self) -> list[UniversalProvider]:
        ...

    # --- Operatories ---

    @abstractmethod
    async def list_operatories(self) -> list[UniversalOperatory]:
        ...

    # --- Slots ---

    @abstractmethod
    async def get_available_slots(
        self,
        start_date: str,
        days: int = 7,
        provider_id: str | None = None,
        appointment_type_id: str | None = None,
        operatory_ids: list[str] | None = None,
    ) -> list[UniversalSlot]:
        ...

    # --- Booking ---

    @abstractmethod
    async def book_appointment(self, req: BookingRequest) -> BookingResult:
        ...

    @abstractmethod
    async def cancel_appointment(self, appointment_id: str) -> BookingResult:
        ...

    @abstractmethod
    async def reschedule_appointment(
        self, old_appointment_id: str, new_booking: BookingRequest
    ) -> BookingResult:
        ...

    # --- Locations ---

    @abstractmethod
    async def list_locations(self) -> list[UniversalLocation]:
        ...

    @abstractmethod
    async def get_location(self, location_id: str) -> UniversalLocation | None:
        ...

    # --- Setup ---

    @abstractmethod
    async def get_setup_steps(self) -> list[SetupStep]:
        ...

    # --- Cleanup ---

    async def close(self) -> None:
        """Release any resources held by this adapter."""
        pass


class SupportsAppointmentTypeCreation(ABC):
    """Optional: PMS supports creating appointment types (e.g. NexHealth)."""

    @abstractmethod
    async def list_pms_descriptors(self) -> list[dict]:
        ...

    @abstractmethod
    async def create_appointment_type(
        self, name: str, duration_minutes: int, descriptor_ids: list[str]
    ) -> UniversalAppointmentType:
        ...


class SupportsAvailabilityLinking(ABC):
    """Optional: PMS requires explicit availability linking (e.g. NexHealth)."""

    @abstractmethod
    async def link_availability(
        self,
        provider_id: str,
        appointment_type_id: str,
        operatory_id: str,
        days: list[str],
        start_time: str,
        end_time: str,
    ) -> dict:
        ...

    @abstractmethod
    async def list_availabilities(self, **kwargs: Any) -> list[dict]:
        ...
