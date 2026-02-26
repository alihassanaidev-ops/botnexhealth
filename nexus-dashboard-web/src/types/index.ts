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
    | "appointment_booked"
    | "appointment_rescheduled"
    | "appointment_cancelled"
    | "emergency"
    | "complaint"
    | "needs_callback"
    | "faq_handled"
    | "financial_inquiry"
    | "transferred"
    | "insurance_verified"
    | "insurance_unverified"
    | "no_action_needed";

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
    call_tags: string[];           // all normalized tags for this call
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
    callback_resolved: boolean;
    agent_used: string | null;
    created_at: string;
    contact: ContactSummary | null;
}

export interface CustomFieldValue {
    field_key: string;
    field_name: string;
    field_type: "text" | "number" | "boolean" | "date" | "dropdown";
    value: string | null;
    is_phi: boolean;
    display_order: number;
}

export interface CustomFieldDefinition {
    id: string;
    tenant_id: string;
    entity_type: string;
    field_name: string;
    field_key: string;
    field_type: string;
    is_phi: boolean;
    is_required: boolean;
    dropdown_options: string[] | null;
    retell_source: string | null;
    retell_source_key: string | null;
    display_order: number;
    is_active: boolean;
    created_at: string;
}

// ── Transcript types ─────────────────────────────────────────────────────

export interface TranscriptTurn {
    role: "agent" | "user" | "tool_call_invocation" | "tool_call_result";
    content?: string;
    name?: string;         // for tool_call_invocation (function name)
    tool_call_id?: string;
    arguments?: string;    // for tool_call_invocation
    time_sec?: number;
    words?: Array<{ word: string; start: number; end: number }>;
    metadata?: Record<string, unknown>;
}

export interface CallDetail extends CallRecord {
    // Raw plain-text (may contain PHI — kept for completeness)
    transcript: string | null;
    // Structured JSONB turn-by-turn arrays from Retell
    transcript_with_tool_calls: TranscriptTurn[] | null;       // unredacted full conversation
    scrubbed_transcript_with_tool_calls: TranscriptTurn[] | null;  // PII-scrubbed (default UI)
    recording_url: string | null;
    custom_fields: CustomFieldValue[];
}

export interface CallsListResponse {
    total: number;
    limit: number;
    offset: number;
    items: CallRecord[];
}

// ── Twilio ─────────────────────────────────────────────────────────────

export interface TwilioPhoneNumber {
    sid: string;
    phone_number: string;
    friendly_name: string;
    capabilities: {
        voice: boolean;
        sms: boolean;
        mms: boolean;
    };
    status: string | null;
}

export interface SendSmsRequest {
    from_number: string;
    to_number: string;
    body: string;
}

export interface SendSmsResponse {
    message_sid: string;
    status: string;
    from_number: string;
    to_number: string;
}

// ── Dashboard ──────────────────────────────────────────────────────────

export interface CallVolume {
    today: number;
    this_week: number;
    this_month: number;
    all_time: number;
}

export interface TagCount {
    tag: string;
    label: string;
    count: number;
}

export interface CallbackQueueItem {
    call_id: string;
    contact_name: string | null;
    call_date: string | null;
    call_time: string | null;
    call_duration_seconds: number | null;
    summary: string | null;
    next_action: string | null;
}

export interface DashboardSummary {
    call_volume: CallVolume;
    tag_counts: TagCount[];
    callback_queue: CallbackQueueItem[];
    as_of: string;
}


