"""Universal PMS domain models.

These models are PMS-agnostic — they represent YOUR system's language.
Each adapter translates PMS-specific responses into these models.
"""

from __future__ import annotations


from pydantic import BaseModel, Field


class UniversalPatient(BaseModel):
    id: str
    source: str  # "nexhealth"
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    date_of_birth: str | None = None
    extra: dict = {}  # PMS-specific data (upcoming_appts, procedures, etc.)


class UniversalProvider(BaseModel):
    id: str
    source: str
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    specialty: str | None = None
    appointment_types: list[dict] = []  # simplified appt types this provider offers
    operatory_ids: list[str] = []


class UniversalAppointmentType(BaseModel):
    id: str
    source: str
    name: str
    duration_minutes: int | None = None
    source_id: str  # raw PMS ID for API calls
    source_metadata: dict = {}
    # NexHealth: {"nh_appt_type_id": ..., "descriptor_ids": [...]}



class UniversalOperatory(BaseModel):
    id: str
    source: str
    name: str
    is_active: bool = True


class UniversalSlot(BaseModel):
    start: str  # ISO datetime string
    end: str
    provider_id: str
    provider_name: str = ""
    operatory_id: str | None = None
    operatory_name: str | None = None
    appointment_type_id: str | None = None
    location_id: str | None = None


class SlotSearchResult(BaseModel):
    """Slots plus the PMS "next available date" hint.

    When the requested window has no bookable slots, some PMSes (e.g. NexHealth)
    return the next date that *does* have slots, so callers don't have to probe
    day-by-day. ``next_available_date`` is the earliest such date across all
    queried providers; ``next_available_by_provider`` keeps the per-provider
    breakdown. Both are ``None``/empty when slots were found or when there is no
    availability within the PMS lookahead window.
    """

    slots: list[UniversalSlot] = Field(default_factory=list)
    next_available_date: str | None = None  # YYYY-MM-DD, earliest across providers
    next_available_by_provider: dict[str, str] = Field(default_factory=dict)


class UniversalLocation(BaseModel):
    id: str
    source: str
    name: str
    subdomain: str | None = None  # NexHealth-specific but useful
    address: str | None = None
    city: str | None = None
    phone: str | None = None
    timezone: str | None = None
    hours: dict | None = None


class BookingRequest(BaseModel):
    patient_id: str
    provider_id: str
    appointment_type_id: str | None = None
    slot_start: str  # ISO datetime
    slot_end: str | None = None
    operatory_id: str | None = None
    descriptor_ids: list[str] = []  # NexHealth: EHR procedure codes
    note: str | None = None


class BookingResult(BaseModel):
    success: bool
    id: str | None = None
    source: str = ""
    status: str = ""  # "confirmed" | "pending" | "error"
    start: str | None = None
    end: str | None = None
    patient_id: str | None = None
    provider_id: str | None = None
    appointment_type_id: str | None = None
    message: str = ""
    error: str | None = None


class PatientCreateRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    date_of_birth: str  # YYYY-MM-DD
    provider_id: str
    gender: str = "Female"


class SetupStep(BaseModel):
    id: str
    label: str
    description: str
    required: bool = True
    completed: bool = False
