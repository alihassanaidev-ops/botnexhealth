"""Universal PMS domain models.

These models are PMS-agnostic — they represent YOUR system's language.
Each adapter translates PMS-specific responses into these models.
"""

from __future__ import annotations

from typing import Literal

from enum import Enum

from pydantic import BaseModel, Field


class UniversalPatient(BaseModel):
    id: str
    source: str  # "nexhealth", "gotracker", etc.
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    date_of_birth: str | None = None
    extra: dict = {}  # PMS-specific data (upcoming_appts, procedures, etc.)


class UniversalPatientPage(BaseModel):
    """One bounded page read directly from the configured PMS.

    Cursors are deliberately opaque to API consumers. NexHealth supplies real
    cursor values; GoTracker uses its Synchronizer page number. The adapter is
    the only layer that needs to understand either representation.
    """

    items: list[UniversalPatient] = Field(default_factory=list)
    total: int | None = None
    next_cursor: str | None = None
    previous_cursor: str | None = None
    has_next_page: bool = False
    has_previous_page: bool = False


class UniversalClinicalNote(BaseModel):
    """PHI-minimized clinical-note metadata from the practice software.

    The note body is deliberately excluded. Clinical free text is not required
    for current workflow eligibility and should not enter generic automation
    context.
    """

    id: str
    source: str
    patient_id: str
    provider_id: str | None = None
    procedure_id: str | None = None
    note_type: str | None = None
    title: str | None = None
    entered_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UniversalDocumentType(BaseModel):
    id: str
    source: str
    name: str
    active: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UniversalPatientDocument(BaseModel):
    """Patient document metadata without file contents or download URLs."""

    id: str
    source: str
    patient_id: str
    document_type_id: str | None = None
    document_type_name: str | None = None
    name: str | None = None
    mime_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    uploaded_at: str | None = None


class UniversalPatientRecall(BaseModel):
    id: str
    source: str
    patient_id: str
    recall_type_id: str | None = None
    recall_type_name: str | None = None
    due_date: str | None = None
    last_visit_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UniversalRecallType(BaseModel):
    id: str
    source: str
    name: str
    interval_months: int | None = None
    active: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UniversalTreatmentPlan(BaseModel):
    """Treatment-plan routing metadata without procedure details or fees."""

    id: str
    source: str
    patient_id: str
    status: str | None = None
    name: str | None = None
    provider_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    accepted_at: str | None = None
    completed_at: str | None = None


class PatientCommunicationSnapshot(BaseModel):
    """Bounded read model for Item 25 patient-communication data."""

    source: str
    patient_id: str
    fetched_at: str
    clinical_notes: list[UniversalClinicalNote] = Field(default_factory=list)
    document_types: list[UniversalDocumentType] = Field(default_factory=list)
    patient_documents: list[UniversalPatientDocument] = Field(default_factory=list)
    patient_recalls: list[UniversalPatientRecall] = Field(default_factory=list)
    recall_types: list[UniversalRecallType] = Field(default_factory=list)
    treatment_plans: list[UniversalTreatmentPlan] = Field(default_factory=list)
    patient_alerts_included: bool = False
    patient_alerts_policy: str


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
    duration_min: int | None = None
    operatory_id: str | None = None
    descriptor_ids: list[str] = []  # NexHealth: EHR procedure codes
    note: str | None = None
    #: Why this booking is happening (Item 34): actor, trace id, and the
    #: campaign run and step where there is one. Optional so an unconverted
    #: caller still books rather than failing, and flat because the adapter
    #: forwards it verbatim into another team's record of the write.
    provenance: dict[str, str] | None = None


class BookingWriteStatus(str, Enum):
    """Whether a booking has actually reached the practice's own software.

    For NexHealth clinics the write is immediate, so an accepted booking is
    CONFIRMED. For GoTracker clinics the Cloud Service queues the write until
    the clinic's machine is reachable, so acceptance means only PENDING - the
    appointment may still hit a conflict or exhaust its retries and never
    arrive. Reporting PENDING as though it were CONFIRMED is what lets a
    patient be told "you're booked" for an appointment the practice never sees.
    """

    CONFIRMED = "confirmed"
    PENDING = "pending"
    UNKNOWN = "unknown"


class BookingResult(BaseModel):
    success: bool
    id: str | None = None
    source: str = ""
    status: str = ""  # "confirmed" | "pending" | "error"
    # Distinct from `status`: has this reached the practice software yet?
    write_status: str = BookingWriteStatus.UNKNOWN.value
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
    gender: Literal["Female", "Male", "Other"]


class SetupStep(BaseModel):
    id: str
    label: str
    description: str
    required: bool = True
    completed: bool = False
