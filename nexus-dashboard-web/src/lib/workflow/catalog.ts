/**
 * Display metadata for the palette and node renderer: labels, descriptions, icons,
 * grouping, and accent classes. Kept separate from the pure graph/validation logic
 * (this module imports React icon components; the logic modules do not).
 */
import {
    CalendarClock,
    ClipboardCheck,
    Clock,
    Flag,
    GitBranch,
    Mail,
    MessageSquare,
    MousePointerClick,
    Phone,
    RefreshCw,
    Upload,
    PhoneIncoming,
    type LucideIcon,
} from "lucide-react"
import type { ConditionOp, NodeType, TriggerType } from "@/types/workflow"

export interface NodeMeta {
    label: string
    description: string
    icon: LucideIcon
    group: "channel" | "control" | "action"
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
        label: "Update Status",
        description: "Record a patient workflow status.",
        icon: ClipboardCheck,
        group: "action",
        accent: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
    },
    wait: {
        label: "Wait",
        description: "Pause for a duration or until a time.",
        icon: Clock,
        group: "control",
        accent: "bg-slate-100 text-slate-700 dark:bg-slate-800/70 dark:text-slate-300",
    },
    condition: {
        label: "Condition",
        description: "Branch on contact / appointment / response.",
        icon: GitBranch,
        group: "control",
        accent: "bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-300",
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
    callback_requested: {
        label: "Callback requested",
        description: "Enroll patients who asked for a callback.",
        icon: PhoneIncoming,
    },
}

/** Palette groups, in display order. */
export const PALETTE_GROUPS: Array<{ title: string; group: NodeMeta["group"]; types: NodeType[] }> = [
    { title: "Channels", group: "channel", types: ["send_sms", "send_voice", "send_email"] },
    { title: "Actions", group: "action", types: ["update_patient_status"] },
    { title: "Control flow", group: "control", types: ["wait", "condition", "exit"] },
]

/** DataTransfer MIME used to drag a palette node type onto the canvas. */
export const WORKFLOW_NODE_DND_MIME = "application/x-nexus-node-type"

export const CONDITION_OP_LABELS: Record<ConditionOp, string> = {
    eq: "equals",
    neq: "does not equal",
    in: "is one of",
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
