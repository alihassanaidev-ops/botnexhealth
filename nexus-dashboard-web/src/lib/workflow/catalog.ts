/**
 * Display metadata for the palette and node renderer: labels, descriptions, icons,
 * grouping, and accent classes. Kept separate from the pure graph/validation logic
 * (this module imports React icon components; the logic modules do not).
 */
import {
    CalendarCheck,
    CalendarClock,
    CalendarPlus,
    ClipboardCheck,
    Clock,
    Flag,
    GitBranch,
    Split,
    Shuffle,
    Mail,
    MessageSquare,
    MessageSquareReply,
    MousePointerClick,
    Phone,
    RefreshCw,
    Upload,
    PhoneIncoming,
    Inbox,
    ClipboardList,
    FormInput,
    Stethoscope,
    UserPlus,
    Braces,
    Sparkles,
    type LucideIcon,
} from "lucide-react"
import type { ConditionOp, NodeType, TriggerType } from "@/types/workflow"

export interface NodeMeta {
    label: string
    description: string
    icon: LucideIcon
    group: "channel" | "control" | "action" | "advanced"
    /** Tailwind classes for the node's icon chip (light + dark). */
    accent: string
}

export const NODE_META: Record<NodeType, NodeMeta> = {
    send_sms: {
        label: "Send SMS",
        description: "Send a compliant text message.",
        icon: MessageSquare,
        group: "channel",
        accent: "bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
    },
    retell_sms_conversation: {
        label: "Retell SMS Conversation",
        description: "Generate patient replies with Retell while Twilio remains the transport.",
        icon: MessageSquareReply,
        group: "channel",
        accent: "bg-cyan-100 text-cyan-700 dark:bg-cyan-950/50 dark:text-cyan-300",
    },
    send_voice: {
        label: "AI Voice Call",
        description: "Place an outbound AI call.",
        icon: Phone,
        group: "channel",
        accent: "bg-violet-100 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300",
    },
    send_email: {
        label: "Send Email",
        description: "Send a branded email.",
        icon: Mail,
        group: "channel",
        accent: "bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300",
    },
    update_patient_status: {
        label: "Update Internal Status",
        description: "Record an internal ScaleNexus workflow status.",
        icon: ClipboardCheck,
        group: "advanced",
        accent: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
    },
    update_appointment: {
        label: "Update Appointment",
        description: "Confirm, cancel, or reschedule in the clinic's PMS.",
        icon: ClipboardList,
        group: "action",
        accent: "bg-rose-100 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300",
    },
    book_appointment: {
        label: "Book Appointment",
        description: "Book a campaign-selected slot in the clinic's PMS.",
        icon: CalendarPlus,
        group: "action",
        accent: "bg-sky-100 text-sky-700 dark:bg-sky-950/50 dark:text-sky-300",
    },
    booking_link: {
        label: "Booking Link",
        description: "Set the rules the patient's booking link follows.",
        icon: CalendarCheck,
        group: "action",
        accent: "bg-sky-100 text-sky-700 dark:bg-sky-950/50 dark:text-sky-300",
    },
    patient_registration: {
        label: "Register Patient",
        description: "Turn a lead into a patient record before booking.",
        icon: UserPlus,
        group: "action",
        accent: "bg-sky-100 text-sky-700 dark:bg-sky-950/50 dark:text-sky-300",
    },
    update_gotracker_appointment: {
        label: "Update GoTracker Appointment",
        description: "GoTracker only. Prefer Update Appointment for new workflows.",
        icon: ClipboardList,
        group: "action",
        accent: "bg-rose-100 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300",
    },
    json_mapper: {
        label: "JSON Mapper",
        description: "Advanced JSON path mapping.",
        icon: Braces,
        group: "advanced",
        accent: "bg-teal-100 text-teal-700 dark:bg-teal-950/50 dark:text-teal-300",
    },
    split: {
        label: "Split (A/B)",
        description: "Randomly divide contacts to test variants.",
        icon: Shuffle,
        group: "control",
        accent: "bg-violet-100 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300",
    },
    llm: {
        label: "AI Action",
        description: "Run an OpenAI prompt.",
        icon: Sparkles,
        group: "action",
        accent: "bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-950/50 dark:text-fuchsia-300",
    },
    drip: {
        label: "Drip",
        description: "Release contacts in batches over time.",
        icon: Clock,
        group: "action",
        accent: "bg-cyan-100 text-cyan-700 dark:bg-cyan-950/50 dark:text-cyan-300",
    },
    wait: {
        label: "Wait",
        description: "Pause for time or an incoming event.",
        icon: Clock,
        group: "control",
        accent: "bg-slate-100 text-slate-700 dark:bg-slate-800/70 dark:text-slate-300",
    },
    condition: {
        label: "Condition",
        description: "Branch two ways on contact / appointment / response.",
        icon: GitBranch,
        group: "control",
        accent: "bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-300",
    },
    switch: {
        label: "Switch",
        description: "Route down one of many branches on the first match.",
        icon: Split,
        group: "control",
        accent: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300",
    },
    exit: {
        label: "Exit",
        description: "End the sequence with an outcome.",
        icon: Flag,
        group: "control",
        accent: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
    },
}

export interface TriggerMeta {
    label: string
    description: string
    icon: LucideIcon
}

export const TRIGGER_META: Record<TriggerType, TriggerMeta> = {
    appointment_offset: {
        label: "Appointment offset",
        description: "Enroll a set time before/after an appointment.",
        icon: CalendarClock,
    },
    appointment_state_changed: {
        label: "Appointment state",
        description: "Enroll when GoTracker appointment state changes.",
        icon: Stethoscope,
    },
    recall_scan: {
        label: "Recall scan",
        description: "Enroll patients due for recall on a schedule.",
        icon: RefreshCw,
    },
    manual: {
        label: "Manual / bulk",
        description: "Enroll contacts manually or by CSV.",
        icon: MousePointerClick,
    },
    bulk_import: {
        label: "Bulk import",
        description: "Enroll a batch of imported contacts.",
        icon: Upload,
    },
    enquiry_received: {
        label: "Enquiry received",
        description: "Enroll when a sales enquiry lands.",
        icon: Inbox,
    },
    form_submitted: {
        label: "Form submitted",
        description: "Enroll when a connected Meta or Typeform form is submitted.",
        icon: FormInput,
    },
    callback_requested: {
        label: "Callback requested",
        description: "Enroll patients who asked for a callback.",
        icon: PhoneIncoming,
    },
    patient_status_changed: {
        label: "Internal status",
        description: "Enroll when a workflow records an internal status.",
        icon: ClipboardList,
    },
    sms_reply: {
        label: "SMS reply",
        description: "Enroll when a patient texts the clinic.",
        icon: MessageSquareReply,
    },
    email_reply: {
        label: "Email reply",
        description: "Enroll when a patient replies to a clinic email.",
        icon: Mail,
    },
}

/**
 * Trigger types the builder does not offer for new workflows.
 *
 * They stay in `TRIGGER_META` so an existing definition still renders its own
 * trigger with a proper label instead of a raw key.
 *
 * - `bulk_import` has no enrollment path at all: there is no CSV import route on
 *   `/automation/workflows`, so a workflow using it can never enroll anyone.
 * - `email_reply` is accepted by the backend schema but has no trigger service
 *   yet, so nothing enrolls from an inbound email. (The email *wait* node is
 *   fully wired — that is a different feature.)
 */
export const UNAVAILABLE_TRIGGER_TYPES: ReadonlySet<TriggerType> = new Set<TriggerType>([
    "bulk_import",
    "email_reply",
])

/**
 * PMS ownership of triggers and nodes. Mirrors the backend's
 * `src/app/services/automation/pms_scope.py` (parity-tested) — a trigger/node
 * absent from these maps is shared across every practice-management system.
 *
 * `appointment_state_changed` only ever fires from the GoTracker webhook
 * route, so offering it to a NexHealth institution builds a campaign that
 * silently never enrolls anyone.
 */
export const TRIGGER_PMS: Partial<Record<TriggerType, readonly string[]>> = {
    appointment_state_changed: ["gotracker"],
}

export const NODE_PMS: Partial<Record<NodeType, readonly string[]>> = {
    update_gotracker_appointment: ["gotracker"],
}

export function triggerAllowedForPms(type: TriggerType, pmsType: string | null): boolean {
    const owners = TRIGGER_PMS[type]
    // Unknown PMS (context still loading) fails closed for PMS-owned triggers:
    // better to briefly hide a GoTracker trigger from a GoTracker tenant than
    // to offer it to everyone else.
    return !owners || (pmsType !== null && owners.includes(pmsType))
}

/**
 * Trigger types offered in the picker, plus whichever one is already selected.
 * Pass the institution's `pmsType` so PMS-owned triggers only show up for the
 * PMS they belong to.
 */
export function selectableTriggerTypes(current: TriggerType, pmsType: string | null = null): TriggerType[] {
    return (Object.keys(TRIGGER_META) as TriggerType[]).filter(
        (type) =>
            type === current ||
            (!UNAVAILABLE_TRIGGER_TYPES.has(type) && triggerAllowedForPms(type, pmsType)),
    )
}

/** Palette groups, in display order. */
export const PALETTE_GROUPS: Array<{ title: string; group: NodeMeta["group"]; types: NodeType[] }> = [
    { title: "Channels", group: "channel", types: ["send_sms", "retell_sms_conversation", "send_voice", "send_email"] },
    // `update_gotracker_appointment` is deliberately absent: it is retained in the
    // schema for already-published definitions, but binds a new workflow to one
    // PMS. `update_appointment` is the PMS-neutral replacement.
    { title: "Actions", group: "action", types: ["drip", "llm", "book_appointment", "booking_link", "patient_registration", "update_appointment"] },
    { title: "Control flow", group: "control", types: ["wait", "condition", "switch", "split", "exit"] },
    { title: "Advanced", group: "advanced", types: ["update_patient_status", "json_mapper"] },
]

/** DataTransfer MIME used to drag a palette node type onto the canvas. */
export const WORKFLOW_NODE_DND_MIME = "application/x-nexus-node-type"

export const CONDITION_OP_LABELS: Record<ConditionOp, string> = {
    eq: "equals",
    neq: "does not equal",
    in: "is one of",
    in_case_insensitive: "is one of (ignore case)",
    not_in: "is not one of",
    is_null: "is empty",
    is_not_null: "is not empty",
    contains: "contains",
    not_contains: "does not contain",
}

/** Short human label for a node in lists/validation. */
export function nodeTypeLabel(type: NodeType): string {
    return NODE_META[type].label
}

export function triggerTypeLabel(type: TriggerType): string {
    return TRIGGER_META[type].label
}
