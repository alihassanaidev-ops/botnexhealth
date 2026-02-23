export interface User {
    id: string;
    email: string;
    role: "ADMIN" | "TENANT";
    is_active?: boolean;
    tenant_id?: string;
}

export interface Tenant {
    id: string;
    name: string;
    slug: string;
    is_active: boolean;
    created_at?: string;
    updated_at?: string;
}

export interface TenantUser {
    id: string;
    email: string;
    role: string;
    is_active: boolean;
}

export interface TenantDetail {
    id: string;
    name: string;
    slug: string;
    is_active: boolean;


    has_nexhealth_key: boolean;
    has_system_nexhealth_key: boolean;
    has_retell_secret: boolean;

    user: TenantUser | null;
}

export interface Location {
    id: string;
    tenant_id: string;
    name: string;
    slug: string;
    is_active: boolean;

    nexhealth_subdomain: string | null;
    nexhealth_location_id: string | null;
    retell_agent_id: string | null;
    has_retell_secret: boolean;

    address: string | null;
    city: string | null;
    state: string | null;
    phone: string | null;
    timezone: string | null;
}

export interface SyncResult {
    location: string;
    success: boolean;
    providers_synced: number;
    appointment_types_synced: number;
    operatories_synced: number;
    descriptors_synced: number;
    errors: string[];
}

// ── Tenant Setup Types ─────────────────────────────────────────────────

export interface LocationInfo {
    id: string;
    name: string;
    slug: string;
    nexhealth_subdomain: string | null;
    nexhealth_location_id: string | null;

}

export interface SetupOverview {
    location: LocationInfo;
    pms_source: string | null;
    can_create_appointment_types: boolean;
    can_link_availability: boolean;
    counts: Record<string, number>;
}

export interface CachedProvider {
    id: string;
    source_id: string;
    source: string;
    name: string | null;
    first_name: string | null;
    last_name: string | null;
    specialty: string | null;
    is_active: boolean;
    synced_at: string | null;
}

export interface CachedAppointmentType {
    id: string;
    source_id: string;
    source: string;
    name: string;
    duration_minutes: number | null;
    source_metadata: {
        nh_appt_type_id?: number;
        descriptor_ids?: string[];
    } | null;
    is_active: boolean;
    synced_at: string | null;
}

export interface CachedOperatory {
    id: string;
    source_id: string;
    source: string;
    name: string;
    is_active: boolean;
    synced_at: string | null;
}

export interface CachedDescriptor {
    id: string;
    source_id: string;
    source: string;
    name: string;
    descriptor_type: string | null;
    code: string | null;
    is_active: boolean;
    source_metadata: Record<string, unknown> | null;
    synced_at: string | null;
}

export interface CachedAvailability {
    id: string;
    source_id: string;
    source: string;
    provider_source_id: string | null;
    provider_name: string | null;
    operatory_source_id: string | null;
    operatory_name: string | null;
    begin_time: string | null;
    end_time: string | null;
    days: string[] | null;
    specific_date: string | null;
    appointment_type_ids: string[] | null;
    appointment_type_names: string[] | null;
    active: boolean;
    synced: boolean;
    source_metadata: Record<string, unknown> | null;
    synced_at: string | null;
}

export interface NexHealthLocation {
    id: number;
    name: string;
    institution_id: number;
    street_address: string | null;
    city: string | null;
    state: string | null;
    zip_code: string | null;
    phone_number: string | null;
    tz: string | null;
}

export interface InstitutionBasic {
    id: number;
    name: string;
    subdomain: string | null;
    locations: NexHealthLocation[];
}

export interface InstitutionBasicListResponse {
    code: boolean;
    data: InstitutionBasic[];
}

// ── Calls & Contacts ───────────────────────────────────────────────────

export type CallStatus =
    | "booked"
    | "needs_follow_up"
    | "cancelled"
    | "emergency"
    | "no_action_needed"
    | "rescheduled";

export type CallDirection = "inbound" | "outbound";

export interface ContactSummary {
    id: string;
    full_name: string | null;
    first_name: string | null;
    last_name: string | null;
}

export interface CallRecord {
    id: string;
    retell_call_id: string | null;
    call_direction: string | null;
    call_status: string | null;
    patient_status: string | null;
    summary: string | null;
    patient_sentiment: string | null;
    next_action: string | null;
    is_new_patient: boolean;
    is_complaint: boolean;
    is_insurance_billing: boolean;
    call_date: string | null;
    call_time: string | null;
    call_duration_seconds: number | null;
    created_at: string;
    contact: ContactSummary | null;
}

export interface CallsListResponse {
    total: number;
    limit: number;
    offset: number;
    items: CallRecord[];
}


