/**
 * Typed model of the Plan-01 workflow definition JSON.
 *
 * This mirrors `src/app/services/automation/definition_schema.py` EXACTLY (snake_case
 * field names), because this object is round-tripped verbatim to/from the backend
 * (`POST/PATCH /automation/workflows`). The backend uses Pydantic `extra="forbid"`, so
 * NO extra keys (e.g. visual coordinates) may be added — layout is derived client-side.
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

export type WorkflowTrigger =
    | AppointmentOffsetTrigger
    | RecallScanTrigger
    | ManualTrigger
    | BulkImportTrigger

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

export interface WorkflowDefinition {
    schema_version: typeof SCHEMA_VERSION
    trigger: WorkflowTrigger
    entry_node_id: string
    nodes: WorkflowNode[]
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
}

// ---------------------------------------------------------------------------
// Merge fields (client-side catalog until a backend catalog endpoint exists)
// ---------------------------------------------------------------------------
export interface MergeField {
    /** Full token including braces, e.g. "{{patient_first_name}}". */
    token: string
    label: string
    sample: string
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
