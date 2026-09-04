/**
 * Typed model of the Plan-01 workflow definition JSON.
 *
 * This mirrors `src/app/services/automation/definition_schema.py` EXACTLY (snake_case
 * field names), because this object is round-tripped verbatim to/from the backend
 * (`POST/PATCH /automation/workflows`). The backend forbids unknown keys, but now
 * accepts two presentational top-level passthroughs the runtime ignores: `compliance`
 * and `layout` (manual canvas coordinates). Everything else must match the schema.
 */

export const SCHEMA_VERSION = "1.0" as const

// ---------------------------------------------------------------------------
// Triggers (discriminated on `type`)
// ---------------------------------------------------------------------------
export type TriggerType =
    | "event"
    | "manual"
    | "form_submitted"
    | "internal_status"
    | "schedule"
    | "inbound_message"

/**
 * One of the canonical events from `GET /automation/workflows/event-catalog`.
 *
 * Authors pick clinic-language keys (`appointment.cancelled`), never a PMS
 * payload field, so one campaign definition runs unchanged on either practice-
 * management system. The catalog says which PMS can raise each key, so the
 * picker hides what a location cannot deliver rather than letting someone
 * publish a campaign that silently never enrolls anyone.
 */
export interface EventTrigger {
    type: "event"
    event_keys: string[]
    /** Optional eligibility filter, evaluated before a run is created. */
    filter?: FilterExpression | null
    /** Canonical context path whose value dedupes enrollment. */
    dedupe_field?: string | null
    /**
     * Hours relative to the appointment for `appointment.reminder_due`
     * (negative = before, e.g. -24). Required for that key and rejected on any
     * other, because it is what decides when that event fires.
     */
    reminder_offset_hours?: number | null
    /** How long after the event the campaign is still worth running (0–168h). */
    max_followup_delay_hours?: number | null
    campaign_goal?: string | null
}
export interface ManualTrigger {
    type: "manual"
    /** Optional eligibility filter, evaluated before a run is created. */
    filter?: FilterExpression | null
}
/**
 * A connected Meta or Typeform form was submitted.
 *
 * Narrower than the `enquiry.received` event, which fires for anything landing
 * through intake — a token endpoint, a staff member typing in a phone enquiry.
 * This fires only for forms the practice connected, synced and mapped, which is
 * what makes "when the ABC form is submitted" expressible at all.
 */
export interface FormSubmittedTrigger {
    type: "form_submitted"
    /** Optional eligibility filter, evaluated before a run is created. */
    filter?: FilterExpression | null
    /** Null means either provider. */
    provider?: "meta" | "typeform" | null
    /** Our own form ids. Empty means every enabled form of that provider. */
    form_ids?: string[]
}

/** Status fields the platform owns and can start a campaign from. */
export type InternalStatusField =
    | "call_workflow_status"
    | "contact_lead_status"
    | "handoff_status"
    | "patient_workflow_status"

export interface InternalStatusTrigger {
    type: "internal_status"
    field: InternalStatusField
    to_statuses: string[]
    /** Empty means "arrived here from anything". */
    from_statuses?: string[]
    /** Optional eligibility filter, evaluated before a run is created. */
    filter?: FilterExpression | null
    campaign_goal?: string | null
}

export interface PmsRecallSource {
    kind: "pms_recall"
    recall_interval_months: number
    /** Days before the same patient may enter this campaign again. */
    reenrollment_cooldown_days?: number
}
export interface AudienceSegmentSource {
    kind: "audience_segment"
}
export type ScheduleSource = PmsRecallSource | AudienceSegmentSource

export interface ScheduleTrigger {
    type: "schedule"
    /** Standard five-field cron expression; the presets write this. */
    cron: string
    timezone_mode?: "location" | "fixed"
    fixed_timezone?: string | null
    source: ScheduleSource
    max_enrollments_per_run?: number
    /** Optional eligibility filter, evaluated before a run is created. */
    filter?: FilterExpression | null
    campaign_goal?: string | null
}

export interface InboundMessageTrigger {
    type: "inbound_message"
    channels: Array<"sms" | "email">
    /** Optional whole-token filters. Empty means any routed inbound reply. */
    tokens?: string[]
    /** Optional eligibility filter, evaluated before a run is created. */
    filter?: FilterExpression | null
    campaign_goal?: string | null
}

export type WorkflowTrigger =
    | EventTrigger
    | ManualTrigger
    | FormSubmittedTrigger
    | InternalStatusTrigger
    | ScheduleTrigger
    | InboundMessageTrigger

// ---------------------------------------------------------------------------
// Wait delay (discriminated on `delay_type`)
// ---------------------------------------------------------------------------
export type DelayType = "duration" | "calendar" | "appointment_relative"

export interface DurationDelay {
    delay_type: "duration"
    duration_seconds: number
}
export interface CalendarDelay {
    delay_type: "calendar"
    offset_days: number
    /** HH:MM in the location timezone. */
    time_of_day: string
}
export interface AppointmentRelativeDelay {
    delay_type: "appointment_relative"
    /** Seconds relative to appointment_at. Negative = before, positive = after. */
    offset_seconds: number
    anchor_field?: string
}
export type WaitDelay = DurationDelay | CalendarDelay | AppointmentRelativeDelay

// ---------------------------------------------------------------------------
// Condition rule
// ---------------------------------------------------------------------------
/**
 * Legacy condition operators. Kept because published definitions use them and
 * the backend deliberately does NOT up-convert those rules — the legacy
 * evaluator's equality is exact where the filter DSL coerces types, so
 * rewriting them could change how a live campaign branches.
 */
export type ConditionOp = "eq" | "neq" | "in" | "in_case_insensitive" | "not_in" | "is_null" | "is_not_null" | "contains" | "not_contains"

export interface ConditionRule {
    field: string
    op: ConditionOp
    value?: boolean | number | string | string[] | null
}

// ---------------------------------------------------------------------------
// Filter expression — the shared DSL for trigger filters, condition nodes and
// switch cases. Mirrors `src/app/services/automation/filter_expression.py`.
// ---------------------------------------------------------------------------

export type FilterOp =
    | "eq" | "neq"
    | "in" | "not_in" | "in_case_insensitive" | "not_in_case_insensitive"
    | "contains" | "not_contains" | "starts_with" | "ends_with" | "matches"
    | "gt" | "gte" | "lt" | "lte" | "between"
    | "before" | "after" | "within" | "older_than"
    | "is_null" | "is_not_null" | "is_empty" | "is_not_empty"
    | "any_of" | "all_of"
    | "field_eq" | "field_neq" | "field_gt" | "field_lt"

export type FilterValue = boolean | number | string | Array<string | number | boolean> | null

export interface FilterRule {
    kind: "rule"
    field: string
    op: FilterOp
    value?: FilterValue
    /** Text comparison is case-insensitive unless this is set. */
    case_sensitive?: boolean
}

export interface FilterGroup {
    kind: "group"
    /** `not` negates the conjunction of its children. */
    op: "and" | "or" | "not"
    children: FilterExpression[]
}

export type FilterExpression = FilterRule | FilterGroup

// ---------------------------------------------------------------------------
// Nodes (discriminated on `type`)
// ---------------------------------------------------------------------------
export type NodeType =
    | "wait"
    | "drip"
    | "send_sms"
    | "retell_sms_conversation"
    | "send_voice"
    | "send_email"
    | "update_patient_status"
    | "update_appointment"
    | "book_appointment"
    | "update_gotracker_appointment"
    | "booking_link"
    | "patient_registration"
    | "json_mapper"
    | "llm"
    | "condition"
    | "switch"
    | "split"
    | "exit"

export interface TimeWaitConfig {
    type: "time"
    delay: WaitDelay
    respect_quiet_hours?: boolean
}
export interface SmsReplyWaitConfig {
    type: "sms_reply"
    response_window_seconds?: number
    response_mappings?: SmsResponseMapping[]
}
/**
 * Park the run until the patient replies to the email, or the window closes.
 * The default window is a week rather than SMS's three days — email is answered
 * on a slower rhythm and a weekend must not read as a non-response.
 */
export interface EmailReplyWaitConfig {
    type: "email_reply"
    response_window_seconds?: number
    response_mappings?: SmsResponseMapping[]
}
export type WaitForConfig = TimeWaitConfig | SmsReplyWaitConfig | EmailReplyWaitConfig
export interface WaitNode {
    type: "wait"
    id: string
    wait_for: WaitForConfig
    next_node_id: string
}
export interface DripNode {
    type: "drip"
    id: string
    batch_size: number
    interval_seconds: number
    next_node_id: string
}
export interface SendSmsNode {
    type: "send_sms"
    id: string
    body_template: string
    next_node_id: string
    include_opt_out_footer?: boolean
    respect_quiet_hours?: boolean
    max_attempts?: number
    expect_response?: boolean
    response_window_seconds?: number
    response_mappings?: SmsResponseMapping[]
}
export interface SmsResponseMapping {
    tokens: string[]
    context_updates?: Record<string, boolean | number | string | string[] | null>
    handoff_reason?: string | null
}
export interface RetellSmsConversationNode {
    type: "retell_sms_conversation"
    id: string
    chat_profile_id: string
    next_node_id: string
}
export interface SendVoiceNode {
    type: "send_voice"
    id: string
    voice_profile_id?: string | null
    /** Legacy fallback for old workflows and emergency manual entry. */
    retell_agent_id: string
    next_node_id: string
    respect_quiet_hours?: boolean
    max_attempts?: number
    patient_voice_cooldown_hours?: number
    /**
     * What to do when the cross-run patient cooldown blocks this call.
     * `skip` (default) abandons the attempt; `defer` waits for the cooldown to
     * expire, optionally bounded by a deadline held in run context. The live
     * post-op campaign uses `defer`.
     */
    patient_voice_cooldown_behavior?: "skip" | "defer"
    /** Context field holding the deadline that bounds a `defer`. */
    patient_voice_cooldown_deadline_field?: string | null
    phone_country_code_enabled?: boolean
    phone_country_region?: string | null
    wait_for_outcome?: boolean
    /** Item 19. Whether the agent leaves a message on an answering machine. */
    leave_voicemail?: boolean
    /** Item 19. Whether reaching voicemail uses up one of the attempts below. */
    voicemail_consumes_attempt?: boolean
    /** Item 19. Counted attempts to reach the patient. Distinct from
     *  `max_attempts`, which bounds retries after a transient vendor error. */
    voice_attempt_allowance?: number
    /** Item 19. Hard ceiling on dials whatever the outcome — what stops a
     *  number that is always voicemail being redialled for ever. */
    max_dials?: number
}
/** Who a `send_email` node addresses. Mirrors the backend `EmailRecipient`
 *  union in definition_schema.py. Omitted on definitions published before the
 *  field existed, which the backend reads as `contact`. */
export type EmailRecipient =
    | { kind: "contact" }
    | { kind: "staff"; notification_type?: string | null; include_external?: boolean }
    | { kind: "static"; addresses: string[] }
    | { kind: "merge_field"; field: string }

export interface SendEmailNode {
    type: "send_email"
    id: string
    /** Inline content. Empty when `template_key` names a saved template —
     *  the backend rejects a node that carries both. */
    subject_template: string
    body_template: string
    /** Optional HTML part for inline mode. A saved template brings its own. */
    html_template?: string | null
    /** Key of a saved campaign email template owned by this institution. */
    template_key?: string | null
    /** Approved clinic sender. Omit to inherit the location/practice default. */
    sender_address_id?: string | null
    next_node_id: string
    respect_quiet_hours?: boolean
    max_attempts?: number
    recipient?: EmailRecipient
    /** `continue` lets an optional email fail without abandoning the run. */
    on_failure?: "fail_run" | "continue"
}
export interface UpdatePatientStatusNode {
    type: "update_patient_status"
    id: string
    status: string
    next_node_id: string
    note_template?: string | null
}
/**
 * PMS-neutral appointment write-back. Prefer this over
 * UpdateGoTrackerAppointmentNode, which only runs on GoTracker locations.
 */
export interface UpdateAppointmentNode {
    type: "update_appointment"
    id: string
    next_node_id: string
    operation: "confirm" | "cancel" | "reschedule"
    start_time?: string | null
    end_time?: string | null
    duration_min?: number | null
    provider_id?: string | null
    operatory_id?: string | null
    reason?: string | null
}
/**
 * Book a campaign-selected slot in the clinic's PMS. Patient identity is
 * resolved from the run contact on the backend; authors provide only the
 * scheduling fields and the three outcome branches.
 */
export interface BookAppointmentNode {
    type: "book_appointment"
    id: string
    appointment_type_id: string
    provider_id: string
    start_time: string
    end_time?: string | null
    duration_min?: number | null
    operatory_id?: string | null
    note_template?: string | null
    booked_next_node_id: string
    could_not_book_next_node_id: string
    pending_next_node_id: string
}
/**
 * Configures the patient action link this run offers. Sends nothing — the link
 * still travels inside a later message that renders {{booking_link}}. What this
 * carries is the rules the link obeys, which the public booking API enforces
 * server-side rather than trusting the page.
 */
export interface BookingLinkNode {
    type: "booking_link"
    id: string
    next_node_id: string
    actions: ("book" | "confirm" | "reschedule" | "cancel")[]
    /** Empty means every type the practice software offers. */
    appointment_type_ids: string[]
    window_days: number
    provider_id?: string | null
    /** When the patient must prove who they are before the link will act. */
    identity_check: "off" | "sensitive" | "always"
}

/**
 * Offers a lead the short form that turns them into a patient record, so the
 * booking step after it has something to book against.
 */
export interface PatientRegistrationNode {
    type: "patient_registration"
    id: string
    next_node_id: string
    /** Which provider a self-registered patient is filed under. */
    provider_id: string
    on_abandoned_node_id?: string | null
}

export interface UpdateGoTrackerAppointmentNode {
    type: "update_gotracker_appointment"
    id: string
    next_node_id: string
    status_id?: number | null
    confirmed?: boolean | null
    preconfirmed?: boolean | null
    start_time?: string | null
    end_time?: string | null
    duration_min?: number | null
    provider_id?: string | null
    operatory_id?: string | null
    patient_id?: string | null
    reason?: string | null
}
export interface JsonMapping {
    source_path: string
    target_field: string
    default_value?: boolean | number | string | string[] | null
}
export interface JsonMapperNode {
    type: "json_mapper"
    id: string
    mappings: JsonMapping[]
    next_node_id: string
}
export interface LlmLabelRule {
    label: string
    keywords: string[]
}
export interface LlmNode {
    type: "llm"
    id: string
    source_field: string
    output_field: string
    prompt_template: string
    model?: string | null
    output_mode?: "label" | "text" | "json"
    max_output_tokens?: number
    include_context?: boolean
    /**
     * Context keys the node may send to the model when `include_context` is on.
     * Empty means the whole context — what definitions published before this
     * field existed meant, and the reason it cannot become the new default.
     */
    context_fields?: string[]
    require_model?: boolean
    allow_keyword_fallback?: boolean | null
    json_schema?: Record<string, unknown> | null
    labels?: string[]
    label_rules?: LlmLabelRule[]
    fallback_label?: string | null
    next_node_id: string
}
/**
 * Two-way branch. Exactly one authoring shape is set: `filter` (current) or
 * `logic` + `rules` (legacy, still executed as authored).
 */
export interface ConditionNode {
    type: "condition"
    id: string
    logic?: "AND" | "OR"
    rules?: ConditionRule[]
    filter?: FilterExpression | null
    true_next_node_id: string
    false_next_node_id: string
}

export interface SwitchCase {
    /** Port identity in the builder and in execution traces; unique per node. */
    label: string
    filter: FilterExpression
    next_node_id: string
}

/**
 * Multi-way branch: the first case whose filter matches wins, otherwise the
 * default. Replaces the chain of binary conditions campaigns used to need to
 * route a single value.
 */
export interface SwitchNode {
    type: "switch"
    id: string
    /** Descriptive only — each case filter is self-contained. */
    subject?: string | null
    cases: SwitchCase[]
    default_next_node_id: string
}
/** One weighted arm of a {@link SplitNode}. */
export interface SplitBranch {
    /** Port identity in the builder, in traces, and in the A/B report. */
    label: string
    /** Whole percent of contacts routed here. Weights across a node sum to 100. */
    weight: number
    next_node_id: string
}

/**
 * Weighted random branch — an A/B test over the rest of the workflow.
 *
 * A switch routes on what a contact *is*; a split routes on nothing, which is
 * what makes a difference in outcome attributable to the message rather than to
 * the audience. Assignment is derived server-side from the run id, so a
 * contact stays on one arm across retries and resumes.
 */
export interface SplitNode {
    type: "split"
    id: string
    /** Author-facing note on what is being tested. Descriptive only. */
    subject?: string | null
    branches: SplitBranch[]
}

export interface ExitNode {
    type: "exit"
    id: string
    outcome?: string | null
}

export type WorkflowNode =
    | WaitNode
    | DripNode
    | SendSmsNode
    | RetellSmsConversationNode
    | SendVoiceNode
    | SendEmailNode
    | UpdatePatientStatusNode
    | UpdateAppointmentNode
    | BookAppointmentNode
    | UpdateGoTrackerAppointmentNode
    | BookingLinkNode
    | PatientRegistrationNode
    | JsonMapperNode
    | LlmNode
    | ConditionNode
    | SwitchNode
    | SplitNode
    | ExitNode

// ---------------------------------------------------------------------------
// Compliance metadata (top-level `compliance` block; mirrors backend
// `ComplianceMetadata` in definition_schema.py). Drives the validation
// service's consent-path + content-class checks.
// ---------------------------------------------------------------------------
export type ContentClass = "transactional_care" | "recall" | "sales" | "marketing"

export interface ComplianceMetadata {
    content_class: ContentClass | null
    consent_required: boolean
}

/** Presentational canvas coordinate for a node (keyed by node id). */
export interface NodePosition {
    x: number
    y: number
}

export interface WorkflowDefinition {
    schema_version: typeof SCHEMA_VERSION
    trigger: WorkflowTrigger
    entry_node_id: string
    nodes: WorkflowNode[]
    /** Optional compliance classification (content class + consent basis). */
    compliance?: ComplianceMetadata | null
    /** Flat PMS-derived context fields allowed into runtime trigger context. */
    pms_context_fields?: string[]
    /**
     * Optional presentational layout — manual canvas positions keyed by node id
     * (the synthetic trigger uses `TRIGGER_NODE_ID`). Purely visual: the runtime
     * ignores it, and edges/`next_node_id` remain the source of truth. Backend
     * accepts it as a top-level passthrough.
     */
    layout?: Record<string, NodePosition> | null
}

/** Node types that carry exactly one forward pointer (`next_node_id`). */
export type LinearNode = WaitNode | DripNode | SendSmsNode | RetellSmsConversationNode | SendVoiceNode | SendEmailNode | UpdatePatientStatusNode | UpdateAppointmentNode | UpdateGoTrackerAppointmentNode | JsonMapperNode | LlmNode
/** Node types that place a message/call on a channel. */
export type SendNode = SendSmsNode | SendVoiceNode | SendEmailNode

// ---------------------------------------------------------------------------
// Client-side validation results (node-linked; backend 422 is authoritative)
// ---------------------------------------------------------------------------
export type IssueSeverity = "error" | "warning"

export interface ValidationIssue {
    /** Node the issue attaches to, or null for graph-level issues. */
    node_id: string | null
    severity: IssueSeverity
    message: string
    /** Optional recommended fix, surfaced in the validation panel. */
    fix?: string
    /** Pydantic-style location path (backend issues only). */
    field_path?: (string | number)[]
    /** Machine code for the issue, e.g. "consent_required" (backend issues). */
    code?: string
}

// ---------------------------------------------------------------------------
// Backend validate endpoint — `POST /automation/workflows/validate`
// ---------------------------------------------------------------------------
export interface ValidateDefinitionResponse {
    valid: boolean
    issues: ValidationIssue[]
}

export interface WorkflowNodeCapability {
    node_type: string
    outgoing_fields: string[]
    authorable: boolean
    runtime_supported: boolean
    dry_run_supported: boolean
    legacy: boolean
}

export interface WorkflowNodeCapabilitiesResponse {
    registry_version: string
    nodes: WorkflowNodeCapability[]
}

// ---------------------------------------------------------------------------
// Channel readiness — `GET /automation/workflows/channel-readiness?location_id=`
// Mirrors backend `ChannelReadinessResponse`. Advisory (Plan 02 B6 / Plan 10):
// an unready channel the definition uses WARNS at publish but never hard-blocks.
// ---------------------------------------------------------------------------
/** The three deliverable channels a send node can target. */
export type ChannelKey = "sms" | "email" | "voice"

export interface ChannelReadinessDetail {
    /** "sms" | "email" | "voice" (mirrors backend detail channel names). */
    channel: string
    ready: boolean
    reason: string | null
}

export interface ChannelReadiness {
    sms: boolean
    email: boolean
    voice_configurable: boolean
    details: ChannelReadinessDetail[]
}

// ---------------------------------------------------------------------------
// Launch checklist — `GET /automation/workflows/{id}/launch-checklist` and
// `POST /automation/workflows/{id}/launch-checklist/preview`.
// ---------------------------------------------------------------------------
export type LaunchChecklistStatus = "pass" | "warning" | "blocked" | "unknown"

export interface LaunchChecklistItem {
    id: string
    section: string
    label: string
    status: LaunchChecklistStatus
    message: string
    fix_href: string | null
    metadata: Record<string, unknown>
}

export interface LaunchChecklist {
    workflow_id: string
    workflow_version_id: string | null
    location_id: string | null
    overall_status: LaunchChecklistStatus
    blockers_count: number
    warnings_count: number
    unknown_count: number
    estimated_audience: number | null
    estimated_send_volume: Record<string, number> | null
    estimated_cost_cents: number | null
    estimate_basis: string
    generated_at: string
    items: LaunchChecklistItem[]
}

// ---------------------------------------------------------------------------
// Version history — `GET /automation/workflows/{id}/versions` (newest-first)
// ---------------------------------------------------------------------------
export interface WorkflowVersion {
    id: string
    workflow_id: string
    version_number: number
    definition: WorkflowDefinition
    definition_checksum: string | null
    content_classification: string | null
    published_by_user_id: string | null
    published_at: string
    created_at: string
    is_current: boolean
}

// ---------------------------------------------------------------------------
// Merge fields — sourced from `GET /automation/workflows/merge-fields`.
// `MergeField` is the light shape the builder's preview/insert affordances use;
// `MergeFieldCatalogItem` mirrors the full backend `MergeFieldResponse`.
// ---------------------------------------------------------------------------
export interface MergeField {
    /** Full token including braces, e.g. "{{patient_first_name}}". */
    token: string
    label: string
    sample: string
    name?: string
    description?: string
    group?: string
    availability?: "required_context" | "optional_context" | "derived"
    requires?: string[]
    phi_level?: "none" | "low" | "medium" | "high"
    channels?: Array<"sms" | "email" | "voice">
    trigger_types?: TriggerType[]
}

export interface MergeFieldCatalogItem {
    name: string
    token: string
    label: string
    description: string
    sample: string
    group: string
    availability: "required_context" | "optional_context" | "derived"
    requires: string[]
    phi_level: "none" | "low" | "medium" | "high"
    channels: Array<"sms" | "email" | "voice">
    trigger_types: TriggerType[]
}

// ---------------------------------------------------------------------------
// Workflow LLM models — `GET /automation/workflows/llm-models`.
// ---------------------------------------------------------------------------
export interface WorkflowLlmModel {
    id: string
    label: string
    owned_by?: string | null
}

export interface WorkflowLlmModelsResponse {
    default_model: string
    configured: boolean
    models: WorkflowLlmModel[]
}

// ---------------------------------------------------------------------------
// Client-side dry-run simulation
// ---------------------------------------------------------------------------
export interface TestRunStep {
    node_id: string
    node_type: NodeType
    /** Human summary of what the step does. */
    summary: string
    /** Optional detail — rendered message, humanized wait, branch taken. */
    detail?: string
}
export interface TestRunResult {
    steps: TestRunStep[]
    outcome: string | null
    /** True if the simulation hit the step ceiling (possible cycle). */
    truncated: boolean
}
