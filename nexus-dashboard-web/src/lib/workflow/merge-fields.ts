/**
 * Merge-field catalog + sample data.
 *
 * The catalog is sourced from the backend `GET /automation/workflows/merge-fields`
 * endpoint. A static fallback keeps preview/insert/validation working during
 * initial render and offline; it mirrors the backend catalog shape.
 */
import { useEffect, useState } from "react"
import type { MergeField, TriggerType } from "@/types/workflow"
import { listMergeFields } from "@/lib/workflow-api"

type MergeChannel = "sms" | "email" | "voice"

const ALL_TRIGGERS: TriggerType[] = [
    "appointment_offset",
    "appointment_state_changed",
    "recall_scan",
    "manual",
    "bulk_import",
    "callback_requested",
    "enquiry_received",
    "patient_status_changed",
    "sms_reply",
    "email_reply",
]
const ALL_CHANNELS: MergeChannel[] = ["sms", "email", "voice"]
const APPOINTMENT_CONTEXT_TRIGGERS: TriggerType[] = ["appointment_offset", "appointment_state_changed", "patient_status_changed"]

export const FALLBACK_MERGE_FIELDS: MergeField[] = [
    field("patient_first_name", "Patient first name", "Jordan", "patient", "derived", "low", ALL_CHANNELS, ALL_TRIGGERS),
    field("patient_last_name", "Patient last name", "Rivera", "patient", "derived", "low", ALL_CHANNELS, ALL_TRIGGERS),
    field("patient_full_name", "Patient full name", "Jordan Rivera", "patient", "derived", "low", ALL_CHANNELS, ALL_TRIGGERS),
    field("patient_preferred_language", "Preferred language", "English", "patient", "optional_context", "none", ALL_CHANNELS, ALL_TRIGGERS),
    field("guardian_first_name", "Guardian first name", "Alex", "patient", "optional_context", "low", ALL_CHANNELS, ALL_TRIGGERS),
    field("guardian_full_name", "Guardian full name", "Alex Rivera", "patient", "optional_context", "low", ALL_CHANNELS, ALL_TRIGGERS),
    field("appointment_date", "Appointment date", "July 22, 2026", "appointment", "required_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_time", "Appointment time", "2:00 PM", "appointment", "required_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_datetime", "Appointment date and time", "July 22, 2026 at 2:00 PM", "appointment", "required_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_reason", "Appointment reason", "bridge prep", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_status", "Appointment status", "scheduled", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_status_id", "Appointment status ID", "1", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_duration", "Appointment duration", "00:15:00", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_type", "Appointment type", "Implant consultation", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_type_name", "Appointment type name", "Implant consultation", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("provider_id", "Provider ID", "gt-2", "appointment", "optional_context", "low", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("provider_name", "Provider name", "Dr. Smith", "appointment", "optional_context", "low", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("schedule_column_id", "Schedule column ID", "1", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("booked_user_id", "Booked user", "Admin", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("booked_timestamp", "Booked timestamp", "2026-07-29T20:32:00.810", "appointment", "optional_context", "medium", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("created_machine_name", "Created machine", "EC2AMAZ-QKGJ1Q1", "appointment", "optional_context", "none", ALL_CHANNELS, APPOINTMENT_CONTEXT_TRIGGERS),
    field("clinic_name", "Clinic name", "Riverside Dental", "location", "derived", "none", ALL_CHANNELS, ALL_TRIGGERS),
    field("location_name", "Location name", "Riverside Dental - Downtown", "location", "derived", "none", ALL_CHANNELS, ALL_TRIGGERS),
    field("location_phone", "Location phone", "(555) 010-2211", "location", "derived", "none", ALL_CHANNELS, ALL_TRIGGERS),
    field("location_address", "Location address", "100 Main St, Austin, TX", "location", "derived", "none", ["email", "voice"], ALL_TRIGGERS),
    field("booking_link", "Booking link", "https://book.example.com/r/jordan", "booking", "required_context", "low", ["sms", "email"], ALL_TRIGGERS),
    field("registration_link", "Registration link", "https://book.example.com/book/register?token=abc123", "booking", "required_context", "low", ["sms", "email"], ALL_TRIGGERS),
    field("confirmation_link", "Confirmation link", "https://book.example.com/c/abc123", "booking", "required_context", "low", ["sms", "email"], ["appointment_offset"]),
    field("reschedule_link", "Reschedule link", "https://book.example.com/r/abc123", "booking", "required_context", "low", ["sms", "email"], ["appointment_offset"]),
    field("recall_due_date", "Recall due date", "August 15, 2026", "recall", "required_context", "medium", ALL_CHANNELS, ["recall_scan"]),
    field("recall_type", "Recall type", "Hygiene", "recall", "optional_context", "high", ["email"], ["recall_scan"]),
    field("last_visit_date", "Last visit date", "February 12, 2026", "recall", "optional_context", "high", ["email"], ["recall_scan"]),
    field("callback_requested_at", "Callback requested at", "July 18, 2026 at 10:30 AM", "callback", "required_context", "low", ALL_CHANNELS, ["callback_requested"]),
    field("callback_reason", "Callback reason", "Reschedule request", "callback", "optional_context", "medium", ["email", "voice"], ["callback_requested"]),
    field("preferred_callback_time", "Preferred callback time", "Today after 3:00 PM", "callback", "optional_context", "low", ALL_CHANNELS, ["callback_requested"]),
    field("enquiry_source", "Enquiry source", "website_form", "enquiry", "required_context", "none", ALL_CHANNELS, ["enquiry_received"]),
    field("enquiry_status", "Enquiry status", "new", "enquiry", "optional_context", "none", ALL_CHANNELS, ["enquiry_received"]),
    field("enquiry_external_ref", "Enquiry external ref", "typeform-response-123", "enquiry", "optional_context", "low", ["email", "voice"], ["enquiry_received"]),
    field("matched_existing_contact", "Matched existing contact", "false", "enquiry", "required_context", "none", ALL_CHANNELS, ["enquiry_received"]),
    field("sms_reply_body", "SMS reply body", "I need to reschedule", "sms_reply", "required_context", "high", ["email", "voice"], ["sms_reply"]),
    field("sms_reply_intent", "SMS reply intent", "free_text", "sms_reply", "required_context", "none", ALL_CHANNELS, ["sms_reply"]),
    field("inbound_sms_message_id", "Inbound SMS message ID", "inbound-1", "sms_reply", "required_context", "none", ALL_CHANNELS, ["sms_reply"]),
    field("email_reply_intent", "Email reply intent", "reschedule_request", "email_reply", "required_context", "none", ALL_CHANNELS, ["email_reply"]),
    field("email_reply_message_id", "Inbound email message ID", "inbound-email-1", "email_reply", "required_context", "none", ALL_CHANNELS, ["email_reply"]),
]

let catalog: MergeField[] = FALLBACK_MERGE_FIELDS
const scopedCatalog = new Map<string, MergeField[]>()
const fetchPromises = new Map<string, Promise<MergeField[]>>()

/** The current merge-field catalog (fallback until the backend catalog loads). */
export function getMergeFields(opts?: {
    triggerType?: TriggerType
    channel?: MergeChannel
}): MergeField[] {
    const key = cacheKey(opts)
    return scopedCatalog.get(key) ?? filterFields(catalog, opts)
}

/**
 * Fetch the backend catalog once and cache it. Idempotent; on failure the
 * cached fallback stays in place and a retry is permitted.
 */
export async function loadMergeFields(opts?: {
    triggerType?: TriggerType
    channel?: MergeChannel
}): Promise<MergeField[]> {
    const key = cacheKey(opts)
    let fetchPromise = fetchPromises.get(key)
    if (!fetchPromise) {
        fetchPromise = listMergeFields(opts)
            .then((fields) => {
                const loaded = fields.map((f) => ({
                    name: f.name,
                    token: f.token,
                    label: f.label,
                    sample: f.sample,
                    description: f.description,
                    group: f.group,
                    availability: f.availability,
                    requires: f.requires,
                    phi_level: f.phi_level,
                    channels: f.channels,
                    trigger_types: f.trigger_types,
                }))
                if (key === "all:all") catalog = loaded
                scopedCatalog.set(key, loaded)
                return loaded
            })
            .catch((err) => {
                fetchPromises.delete(key) // allow a later retry
                throw err
            })
        fetchPromises.set(key, fetchPromise)
    }
    return fetchPromise
}

/** Test-only: reset the module cache. */
export function _resetMergeFieldsCache(): void {
    catalog = FALLBACK_MERGE_FIELDS
    scopedCatalog.clear()
    fetchPromises.clear()
}

/** React hook: returns the catalog, fetching + caching from the backend once. */
export function useMergeFields(opts?: {
    triggerType?: TriggerType
    channel?: MergeChannel
}): MergeField[] {
    const triggerType = opts?.triggerType
    const channel = opts?.channel
    const [fields, setFields] = useState<MergeField[]>(
        getMergeFields({ triggerType, channel }),
    )
    useEffect(() => {
        let active = true
        loadMergeFields({ triggerType, channel })
            .then((f) => {
                if (active) setFields(f)
            })
            .catch(() => {
                /* keep fallback; callers toast if they need to */
            })
        return () => {
            active = false
        }
    }, [triggerType, channel])
    return fields
}

/** Map of token -> sample value, for preview/simulation. */
export function sampleMergeData(): Record<string, string> {
    const out: Record<string, string> = {}
    for (const f of catalog) out[f.token] = f.sample
    return out
}

const TOKEN_RE = /\{\{\s*[a-zA-Z0-9_]+\s*\}\}/g

/** Normalize a raw token (trim inner whitespace) to the catalog form. */
export function normalizeToken(raw: string): string {
    const inner = raw.replace(/[{}]/g, "").trim()
    return `{{${inner}}}`
}

/** All merge tokens referenced in a template string (normalized). */
export function extractTokens(template: string): string[] {
    const matches = template.match(TOKEN_RE) ?? []
    return matches.map(normalizeToken)
}

/** Tokens in the template that are not in the known (fetched) catalog. */
export function unknownTokens(template: string): string[] {
    const known = new Set(catalog.map((f) => f.token))
    return Array.from(new Set(extractTokens(template))).filter((t) => !known.has(t))
}

export function unavailableTokens(
    template: string,
    opts: { triggerType: TriggerType; channel: MergeChannel },
): string[] {
    const byToken = new Map(catalog.map((f) => [f.token, f]))
    return Array.from(new Set(extractTokens(template))).filter((token) => {
        const field = byToken.get(token)
        if (!field) return false
        return (
            (field.trigger_types?.length && !field.trigger_types.includes(opts.triggerType))
            || (field.channels?.length && !field.channels.includes(opts.channel))
        )
    })
}

function field(
    name: string,
    label: string,
    sample: string,
    group: string,
    availability: MergeField["availability"],
    phiLevel: MergeField["phi_level"],
    channels: MergeChannel[],
    triggerTypes: TriggerType[],
): MergeField {
    return {
        name,
        token: `{{${name}}}`,
        label,
        sample,
        group,
        availability,
        phi_level: phiLevel,
        channels,
        trigger_types: triggerTypes,
        requires: [],
    }
}

function cacheKey(opts?: { triggerType?: TriggerType; channel?: MergeChannel }): string {
    return `${opts?.triggerType ?? "all"}:${opts?.channel ?? "all"}`
}

function filterFields(
    fields: MergeField[],
    opts?: { triggerType?: TriggerType; channel?: MergeChannel },
): MergeField[] {
    return fields.filter((f) => {
        const triggerOk = !opts?.triggerType || !f.trigger_types || f.trigger_types.includes(opts.triggerType)
        const channelOk = !opts?.channel || !f.channels || f.channels.includes(opts.channel)
        return triggerOk && channelOk
    })
}
