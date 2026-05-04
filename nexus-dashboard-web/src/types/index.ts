export interface User {
    id: string;
    email: string;
    role:
    | "SUPER_ADMIN"
    | "INSTITUTION_ADMIN"
    | "LOCATION_ADMIN"
    | "STAFF";
    is_active?: boolean;
    institution_id?: string;
    location_id?: string;
}

export interface Institution {
    id: string;
    name: string;
    slug: string;
    is_active: boolean;
    created_at?: string;
    updated_at?: string;
}

export interface InstitutionUser {
    id: string;
    email: string;
    role: string;
    is_active: boolean;
    invite_status: "PENDING" | "ACCEPTED";
}

export interface InstitutionDetail {
    id: string;
    name: string;
    slug: string;
    is_active: boolean;


    has_nexhealth_key: boolean;
    has_system_nexhealth_key: boolean;
    has_retell_secret: boolean;

    user: InstitutionUser | null;
}


export interface LocationUser {
    id: string;
    email: string;
    role: string;
    is_active: boolean;
}

export interface Location {
    id: string;
    institution_id: string;
    name: string;
    slug: string;
    is_active: boolean;

    nexhealth_subdomain: string | null;
    nexhealth_location_id: string | null;
    retell_agent_id: string | null;
    twilio_from_number: string | null;
    has_retell_secret: boolean;

    address: string | null;
    city: string | null;
    state: string | null;
    phone: string | null;
    timezone: string | null;

    user: LocationUser | null;
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

// ── Operating Hours & Breaks ────────────────────────────────────────────────

export interface OperatingHoursResponse {
    id: string;
    location_id: string;
    day_of_week: number;
    is_open: boolean;
    open_time: string | null;  // HH:MM
    close_time: string | null; // HH:MM
}

export interface OperatingHoursEntry {
    day_of_week: number;
    is_open: boolean;
    open_time: string | null;
    close_time: string | null;
}

export interface BreakResponse {
    id: string;
    location_id: string;
    name: string;
    day_of_week: number | null;
    start_time: string; // HH:MM
    end_time: string;   // HH:MM
}

export interface BreakCreateRequest {
    name: string;
    day_of_week: number | null;
    start_time: string;
    end_time: string;
}

// ── Institution Setup Types ─────────────────────────────────────────────────

export interface LocationInfo {
    id: string;
    name: string;
    slug: string;
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
    name: string | null;
    first_name: string | null;
    last_name: string | null;
    specialty: string | null;
    is_active: boolean;
    buffer_minutes: number;
    same_day_cutoff_time: string | null;
    min_age: number | null;
    max_age: number | null;
    synced_at: string | null;
}

export interface CachedAppointmentType {
    id: string;
    source_id: string;
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
    name: string;
    is_active: boolean;
    synced_at: string | null;
}

export interface CachedDescriptor {
    id: string;
    source_id: string;
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
    created_at: string;
    contact: ContactSummary | null;
}

export interface CustomFieldValue {
    field_key: string;
    field_name: string;
    field_type: "text" | "number" | "boolean" | "date" | "dropdown";
    value: string | null;
    is_phi: boolean;
    value_masked: boolean;
    reveal_available: boolean;
    display_order: number;
}

export interface CustomFieldDefinition {
    id: string;
    institution_id: string;
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
    // The detail response intentionally carries no PHI bodies — only flags
    // indicating whether a reveal is possible. Transcripts and recordings
    // come back from explicit, audited reveal endpoints below. The backend
    // stores only the PII-scrubbed structured transcript (raw / unscrubbed
    // variants were dropped in migration 20260505_encrypt_call_transcript).
    transcript_available: boolean;
    recording_available: boolean;
    custom_fields: CustomFieldValue[];
}

export interface TranscriptRevealResponse {
    call_id: string;
    transcript_with_tool_calls: TranscriptTurn[] | null;
}

export interface RecordingRevealResponse {
    call_id: string;
    recording_url: string | null;
}

export interface CustomFieldRevealResponse {
    call_id: string;
    field_key: string;
    value: string | null;
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
    institution_location_id: string;
}

export interface SendSmsResponse {
    message_sid: string;
    status: string;
    from_number: string;
    to_number_masked: string | null;
}

export interface SmsLocation {
    id: string;
    institution_id: string;
    institution_name: string;
    location_name: string;
    twilio_from_number: string | null;
}

export interface SmsSuppression {
    id: string;
    institution_id: string;
    location_id: string | null;
    phone_masked: string;
    is_active: boolean;
    source: string;
    keyword: string | null;
    reason: string | null;
    created_at: string;
    released_at: string | null;
}

// ── Notifications ────────────────────────────────────────────────────────────────

export type NotificationType =
    | "new_call"
    | "callback_item"
    | "callback_resolved"
    | "appointment_booked"
    | "urgent";

export interface Notification {
    id: string;
    user_id: string;
    type: NotificationType;
    title: string;
    message: string;
    is_read: boolean;
    created_at: string;
    data?: Record<string, unknown>;
}

export interface NotificationGroup {
    type: NotificationType;
    label: string;
    icon: string;
    items: Notification[];
    unreadCount: number;
}

export interface NotificationUnreadCount {
    total: number;
    new_calls: number;
    callbacks: number;
    appointments: number;
    urgent: number;
}

export interface NotificationBadgeCounts {
    calls: number;
    callbacks: number;
    appointments: number;
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
    appointments_booked_month?: number;
    new_patients_month?: number;
    booking_rate_month?: number;
    avg_call_duration_seconds?: number;
}

// ── Callbacks Page ──────────────────────────────────────────────────────

export interface CallbackListItem {
    call_id: string;
    contact_name: string | null;
    call_date: string | null;
    call_time: string | null;
    call_duration_seconds: number | null;
    summary: string | null;
    next_action: string | null;
    callback_resolved: boolean;
    callback_resolved_at: string | null;
    callback_note: string | null;
    preferred_callback_datetime: string | null;
    created_at: string;
    contact: ContactSummary | null;
}

export interface CallbacksListResponse {
    total: number;
    limit: number;
    offset: number;
    items: CallbackListItem[];
}
