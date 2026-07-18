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
    | "appointment_offset"
    | "recall_scan"
    | "manual"
    | "bulk_import"
    | "callback_requested"

export interface AppointmentOffsetTrigger {
    type: "appointment_offset"
    /** Hours relative to the appointment (negative = before, e.g. -24). */
    offset_hours: number
    appointment_type_ids?: string[] | null
}
export interface RecallScanTrigger {
    type: "recall_scan"
    /** Inactivity/recall interval in months (>= 1). */
    recall_interval_months: number
}
export interface ManualTrigger {
    type: "manual"
}
export interface BulkImportTrigger {
    type: "bulk_import"
}
export interface CallbackRequestedTrigger {
    type: "callback_requested"
}

export type WorkflowTrigger =
    | AppointmentOffsetTrigger
    | RecallScanTrigger
    | ManualTrigger
    | BulkImportTrigger
    | CallbackRequestedTrigger

// ---------------------------------------------------------------------------
// Wait delay (discriminated on `delay_type`)
// ---------------------------------------------------------------------------
export type DelayType = "duration" | "calendar"

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
export type WaitDelay = DurationDelay | CalendarDelay

// ---------------------------------------------------------------------------
// Condition rule
// ---------------------------------------------------------------------------
export type ConditionOp = "eq" | "neq" | "in" | "not_in" | "is_null" | "is_not_null"

export interface ConditionRule {
    field: string
    op: ConditionOp
    value?: boolean | number | string | string[] | null
}

// ---------------------------------------------------------------------------
// Nodes (discriminated on `type`)
// ---------------------------------------------------------------------------
export type NodeType =
    | "wait"
    | "send_sms"
    | "send_voice"
    | "send_email"
    | "condition"
    | "exit"

export interface WaitNode {
    type: "wait"
    id: string
    delay: WaitDelay
    next_node_id: string
    respect_quiet_hours?: boolean
}
export interface SendSmsNode {
    type: "send_sms"
    id: string
    body_template: string
    next_node_id: string
    respect_quiet_hours?: boolean
    max_attempts?: number
}
export interface SendVoiceNode {
    type: "send_voice"
    id: string
    retell_agent_id: string
    next_node_id: string
    respect_quiet_hours?: boolean
    max_attempts?: number
}
export interface SendEmailNode {
    type: "send_email"
    id: string
    subject_template: string
    body_template: string
    next_node_id: string
    respect_quiet_hours?: boolean
    max_attempts?: number
}
export interface ConditionNode {
    type: "condition"
    id: string
    logic?: "AND" | "OR"
    rules: ConditionRule[]
    true_next_node_id: string
    false_next_node_id: string
}
export interface ExitNode {
    type: "exit"
    id: string
    outcome?: string | null
}

export type WorkflowNode =
    | WaitNode
    | SendSmsNode
    | SendVoiceNode
    | SendEmailNode
    | ConditionNode
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
    /**
     * Optional presentational layout — manual canvas positions keyed by node id
     * (the synthetic trigger uses `TRIGGER_NODE_ID`). Purely visual: the runtime
     * ignores it, and edges/`next_node_id` remain the source of truth. Backend
     * accepts it as a top-level passthrough.
     */
    layout?: Record<string, NodePosition> | null
}

/** Node types that carry exactly one forward pointer (`next_node_id`). */
export type LinearNode = WaitNode | SendSmsNode | SendVoiceNode | SendEmailNode
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
