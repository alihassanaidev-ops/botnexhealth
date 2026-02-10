"""Pydantic models for API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field



class Location(BaseModel):
    """Location model within an institution."""

    id: int
    name: str = "Default"
    institution_id: int | None = None
    street_address: str | None = None
    street_address_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone_number: str | None = None
    foreign_id: str | None = None
    foreign_id_type: str | None = None
    email: str | None = None
    tz: str | None = None
    # last_sync_time: datetime | None = None
    # insert_appt_client: bool | None = None
    # map_by_operatory: bool | None = None
    # set_availability_by_operatory: bool | None = None
    # inactive: bool | None = None

    model_config = {"extra": "allow"}


class InstitutionBasic(BaseModel):
    """Basic Institution model (returned by GET /locations)."""

    id: int
    name: str
    subdomain: str | None = None
    locations: list[Location] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class Institution(InstitutionBasic):
    """Institution model from NexHealth API (returned by GET /institutions)."""

    created_at: datetime
    updated_at: datetime
    phone_number: str | None = None
    emrs: list[str] = Field(default_factory=list)
    country_code: str
    is_appt_categories_location_specific: bool | None = None
    appointment_types_location_scoped: bool | None = None
    is_appt_booking_patient_type_activated: bool | None = None
    is_appt_booking_insurance_activated: bool | None = None
    show_online_booking_no_insurance_option: bool | None = None
    request_online_booking_location_permissions: bool | None = None
    # gtm_tags: list[str] = Field(default_factory=list)
    # show_map: bool | None = None
    logo_location_scoped: bool | None = None
    # is_sync_notifications: bool | None = None
    # notify_insert_fails: bool | None = None
    # wlogo: str | None = None



class Operatory(BaseModel):
    """Operatory model."""

    id: int
    name: str
    foreign_id: str | None = None
    foreign_id_type: str | None = None
    location_id: int
    active: bool | None = None
    bookable_online: bool | None = None
    display_name: str | None = None
    profile_url: str | None = None
    appointment_types: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    
    model_config = {"extra": "allow"}


class NexHealthResponse(BaseModel):
    """Base response model for NexHealth API."""

    code: bool
    data: Any  # Can be list, object, or null
    description: str | None = None
    error: list[str] | None = None  # Specific list of error messages
    count: int | None = None



class InstitutionBasicListResponse(NexHealthResponse):
    """Response model for listing institutions (basic view for locations endpoint)."""

    data: list[InstitutionBasic]


class InstitutionListResponse(NexHealthResponse):
    """Response model for listing institutions."""

    data: list[Institution]


class InstitutionDetailResponse(NexHealthResponse):
    """Response model for viewing a single institution."""

    data: Institution


class LocationDetailResponse(NexHealthResponse):
    """Response model for viewing a single location."""

    data: Location


class OperatoryListResponse(NexHealthResponse):
    """Response model for listing operatories."""

    data: list[Operatory]


class OperatoryDetailResponse(NexHealthResponse):
    """Response model for viewing a single operatory."""

    data: Operatory


class AppointmentDescriptor(BaseModel):
    """Appointment descriptor model."""
    # fields are unknown from the example, so we allow anything
    model_config = {"extra": "allow"}


class AppointmentDescriptorListResponse(NexHealthResponse):
    """Response model for listing appointment descriptors."""

    data: list[AppointmentDescriptor]


class Bio(BaseModel):
    """Patient biological information."""

    city: str | None = None
    state: str | None = None
    gender: str | None = None
    zip_code: str | None = None
    new_patient: bool | None = None
    non_patient: bool | None = None
    phone_number: str | None = None
    date_of_birth: str | None = None  # Format YYYY-MM-DD
    address_line_1: str | None = None
    address_line_2: str | None = None
    street_address: str | None = None
    cell_phone_number: str | None = None
    home_phone_number: str | None = None
    work_phone_number: str | None = None

    model_config = {"extra": "allow"}


class EmrApptDescriptor(BaseModel):
    """EMR Appointment Descriptor model."""

    id: int
    descriptor_type: str | None = None
    name: str | None = None
    code: str | None = None
    location_id: int | None = None
    foreign_id: str | None = None
    foreign_id_type: str | None = None
    data: dict[str, Any] | None = None
    active: bool | None = None
    last_sync_time: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"extra": "allow"}


class AppointmentTypeBasic(BaseModel):
    """Basic appointment type model."""

    id: int
    name: str | None = None
    parent_type: str | None = None
    parent_id: int | None = None
    minutes: int | None = None
    bookable_online: bool | None = None

    model_config = {"extra": "allow"}


class AppointmentType(AppointmentTypeBasic):
    """Full appointment type model with descriptors."""

    descriptors: list[EmrApptDescriptor] = Field(default_factory=list)


class AppointmentTypeListResponse(NexHealthResponse):
    """Response model for listing appointment types."""

    data: list[AppointmentType]


class AppointmentTypeDetailResponse(NexHealthResponse):
    """Response model for viewing a single appointment type."""

    data: AppointmentType


class ProviderRequestable(BaseModel):
    """Provider requestable location model."""

    location_id: int

    model_config = {"extra": "allow"}


class CustomRecurrence(BaseModel):
    """Custom recurrence configuration."""

    num: int | None = None
    unit: str | None = None
    ref: str | None = None

    model_config = {"extra": "allow"}


class Availability(BaseModel):
    """Provider availability model."""

    id: int
    provider_id: int | None = None
    location_id: int | None = None
    operatory_id: int | None = None
    begin_time: str | None = None
    end_time: str | None = None
    days: list[str] = Field(default_factory=list)
    specific_date: str | None = None
    custom_recurrence: CustomRecurrence | None = None
    tz_offset: str | None = None
    active: bool | None = None
    synced: bool | None = None
    appointment_types: list[AppointmentTypeBasic] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class AvailabilityListResponse(NexHealthResponse):
    """Response model for listing availabilities."""

    data: list[Availability]


class AvailabilityDetailResponse(NexHealthResponse):
    """Response model for viewing a single availability."""

    data: Availability


class AvailableSlot(BaseModel):
    """Individual appointment slot with time and provider/operatory info."""

    time: str  # ISO8601 format: "2017-10-09T07:00:00.000-04:00"
    end_time: str | None = None
    operatory_id: int | None = None
    provider_id: int | None = None

    model_config = {"extra": "allow"}


class AvailableSlotResponse(BaseModel):
    """Response for each location/provider/operatory combination in appointment slots."""

    lid: int
    pid: int | None = None
    operatory_id: int | None = None
    slots: list[AvailableSlot] = Field(default_factory=list)
    next_available_date: str | None = None  # Format: YYYY-MM-DD

    model_config = {"extra": "allow"}


class AppointmentSlotsResponse(NexHealthResponse):
    """Response model for appointment slots endpoint."""

    data: list[AvailableSlotResponse]


class Provider(BaseModel):
    """Provider model."""

    id: int
    name: str | None = None
    email: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    institution_id: int | None = None
    foreign_id: str | None = None
    foreign_id_type: str | None = None
    bio: Bio | None = None
    inactive: bool | None = None
    last_sync_time: datetime | None = None
    npi: str | None = None
    tin: str | None = None
    state_license: str | None = None
    specialty_code: str | None = None
    # locations: list[Location] = Field(default_factory=list)
    # provider_requestables: list[ProviderRequestable] = Field(default_factory=list)
    # availabilities: list[Availability] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class ProviderListResponse(NexHealthResponse):
    """Response model for listing providers."""

    data: list[Provider]


class ProviderDetailResponse(NexHealthResponse):
    """Response model for viewing a single provider."""

    data: Provider


class InsurancePlan(BaseModel):
    """Insurance Plan model."""

    id: int
    payer_id: str | None = None
    name: str | None = None
    address: str | None = None
    address2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country_code: str | None = None
    group_num: str | None = None
    employer_name: str | None = None
    foreign_id: str | None = None

    model_config = {"extra": "allow"}


class InsuranceSubscriber(BaseModel):
    """Insurance Subscriber model."""

    id: int | None = None
    name: str | None = None
    date_of_birth: str | None = None

    model_config = {"extra": "allow"}


class InsuranceCoverage(BaseModel):
    """Insurance Coverage model."""

    id: int
    subscription_relation: str | None = None
    patient_id: int | None = None
    priority: int | None = None
    plan_id: int | None = None
    subscriber_num: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    plan: InsurancePlan | None = None
    subscriber: InsuranceSubscriber | None = None

    model_config = {"extra": "allow"}


class PatientAlert(BaseModel):
    """Patient Alert model."""

    id: int
    patient_id: int | None = None
    note: str | None = None
    disabled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"extra": "allow"}


class PatientAddress(BaseModel):
    """Patient Address model."""

    line_1: str | None = None
    line_2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    region: str | None = None

    model_config = {"extra": "allow"}


class Patient(BaseModel):
    """Patient model."""

    id: int
    email: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    institution_id: int | None = None
    foreign_id: str | None = None
    foreign_id_type: str | None = None
    bio: Bio | None = None
    inactive: bool | None = None
    last_sync_time: datetime | None = None
    guarantor_id: int | None = None
    unsubscribe_sms: bool | None = None
    preferred_language: str | None = None
    preferred_locale: str | None = None
    location_ids: list[int] = Field(default_factory=list)

    # Relationships
    upcoming_appts: list[dict[str, Any]] = Field(default_factory=list)
    procedures: list[dict[str, Any]] = Field(default_factory=list)
    insurance_coverages: list[InsuranceCoverage] = Field(default_factory=list)
    # patient_alerts: list[PatientAlert] = Field(default_factory=list)
    address: PatientAddress | None = None
    provider: Provider | None = None
    # Use generic list for children/guarantor to avoid complex recursive typing issues in this snippet 
    # if not using 'from __future__ import annotations' properly or if pydantic version issues arise.
    # But since we have annotations import, we can try using Patient string forward ref or just Patient.
    # However, to be safe and simple:
    # children: list[dict[str, Any]] = Field(default_factory=list) 
    guarantor: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


class PatientListData(BaseModel):
    """Data object for patient list response."""

    patients: list[Patient]


class PatientListResponse(NexHealthResponse):
    """Response model for listing patients."""

    data: PatientListData


class PatientDetailResponse(NexHealthResponse):
    """Response model for viewing a single patient."""

    data: Patient


class CreatePatientProvider(BaseModel):
    """Provider info for patient creation."""
    provider_id: int


class CreatePatientBio(BaseModel):
    """Patient bio info for creation."""
    date_of_birth: str | None = None
    phone_number: str | None = None
    gender: str | None = "Female"


class CreatePatientData(BaseModel):
    """Patient core info for creation."""
    first_name: str
    last_name: str
    email: str
    bio: CreatePatientBio


class CreatePatientRequest(BaseModel):
    """Request model for creating a patient."""
    provider: CreatePatientProvider
    patient: CreatePatientData


class CreateAppointmentBody(BaseModel):
    """Appointment details for booking."""

    patient_id: int
    provider_id: int
    start_time: str
    operatory_id: int | None = None
    end_time: str | None = None
    appointment_type_id: int | None = None
    note: str | None = Field(default=None, max_length=128, description="Note written to EHR (max 128 chars)")
    referrer: str | None = None
    descriptor_ids: list[int] | None = Field(
        default=None,
        description="EHR descriptor IDs (procedure codes) to sync with PMS",
    )
    # For cancellation
    cancelled: bool | None = None



class CreateAppointmentRequest(BaseModel):
    """Request model for booking an appointment."""
    appt: CreateAppointmentBody


class CancelAppointmentBody(BaseModel):
    """Body for cancelling appointment."""
    cancelled: bool = True


class CancelAppointmentRequest(BaseModel):
    """Request model for cancelling an appointment."""
    appt: CancelAppointmentBody


# =============================================================================
# Appointment Type Create/Update Models
# =============================================================================


class AppointmentTypeData(BaseModel):
    """Appointment type data for create/update."""

    name: str
    minutes: int = Field(ge=5, description="Duration in minutes, increments of 5 recommended")
    parent_type: str | None = Field(default="Institution", description="Institution or Location")
    parent_id: int | None = Field(default=None, description="Required if parent_type is Location")
    bookable_online: bool = Field(default=True, description="Allow online booking")
    emr_appt_descriptor_ids: list[int] = Field(
        default_factory=list, description="EMR descriptor IDs to link"
    )


class CreateAppointmentTypeRequest(BaseModel):
    """Request model for creating an appointment type."""

    location_id: int | None = Field(default=None, description="Location ID if location-specific")
    appointment_type: AppointmentTypeData


class UpdateAppointmentTypeData(BaseModel):
    """Appointment type data for update (all fields optional)."""

    name: str | None = None
    minutes: int | None = Field(default=None, ge=5)
    bookable_online: bool | None = None
    emr_appt_descriptor_ids: list[int] | None = None


class UpdateAppointmentTypeRequest(BaseModel):
    """Request model for updating an appointment type."""

    appointment_type: UpdateAppointmentTypeData


class EmrApptDescriptorListResponse(NexHealthResponse):
    """Response model for listing EMR appointment descriptors."""

    data: list[EmrApptDescriptor]


class TenantUserResponse(BaseModel):
    """User details in tenant response."""
    id: str
    email: str
    role: str
    is_active: bool


class TenantResponse(BaseModel):
    """Response model for tenant (no secrets)."""
    id: str
    name: str
    slug: str
    is_active: bool
    
    # Non-secret config
    nexhealth_subdomain: str | None
    nexhealth_location_id: str | None
    ghl_location_id: str | None
    ghl_custom_fields: dict[str, Any] | None
    retell_agent_id: str | None
    sikka_office_id: str | None
    
    # Credential presence indicators
    has_nexhealth_key: bool
    has_ghl_key: bool
    has_retell_secret: bool
    has_sikka_credentials: bool
    
    has_system_nexhealth_key: bool
    
    # Optional: Created user
    user: TenantUserResponse | None = None
    
    class Config:
        from_attributes = True

    @classmethod
    def from_tenant(cls, tenant: Any, user: Any = None) -> "TenantResponse":
        """Convert Tenant model to response (no secrets exposed)."""
        from src.app.config import settings

        user_resp = None
        if user:
            user_resp = TenantUserResponse(
                id=str(user.id),
                email=user.email,
                role=user.role,
                is_active=user.is_active
            )

        return cls(
            id=str(tenant.id),
            name=tenant.name,
            slug=tenant.slug,
            is_active=tenant.is_active,
            nexhealth_subdomain=tenant.nexhealth_subdomain,
            nexhealth_location_id=tenant.nexhealth_location_id,
            ghl_location_id=tenant.ghl_location_id,
            ghl_custom_fields=tenant.ghl_custom_fields,
            retell_agent_id=tenant.retell_agent_id,
            sikka_office_id=tenant.sikka_office_id,
            has_nexhealth_key=tenant.nexhealth_api_key_encrypted is not None,
            has_system_nexhealth_key=bool(settings.nexhealth_api_key),
            has_ghl_key=tenant.ghl_api_key_encrypted is not None,
            has_retell_secret=tenant.retell_api_secret_encrypted is not None,
            has_sikka_credentials=(
                tenant.sikka_app_id_encrypted is not None and 
                tenant.sikka_app_secret_encrypted is not None
            ),
            user=user_resp
        )
