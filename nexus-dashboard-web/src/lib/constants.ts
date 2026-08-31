/**
 * Which agent vocabulary a call status belongs to.
 *
 * A PMS agent transacts in the practice-management system, so it reports what
 * it *did*: "Appointment Booked". A no-PMS agent cannot transact, so it reports
 * what the team must do: "Needs Booking". A handful of outcomes are neither an
 * action nor a request — an emergency, a complaint, a transfer — and both kinds
 * of agent emit those.
 *
 * Source of truth is `CallStatus` in `src/app/models/call.py` together with the
 * token map in `src/app/services/post_call_service.py` (`RETELL_STATUS_MAP`).
 * Keep this in step when either changes.
 */
export type CallStatusScope = "pms" | "no_pms" | "both"

export interface CallStatusOption {
    value: string
    label: string
    color: string
    scope: CallStatusScope
}

// Dark-mode-aware colors using opacity modifiers (safe for both light and dark)
//
// This is the full registry, and it stays full on purpose: it backs the label
// and color lookups for *rendering* a call, so a record carrying a status from
// the other vocabulary still displays correctly — historical calls from before
// a clinic switched modes, for one. Use `callStatusFilterOptions()` for menus.
export const STATUS_OPTIONS: CallStatusOption[] = [
    // PMS vocabulary — the agent completed the transaction in the PMS.
    { value: "appointment_booked", label: "Appointment Booked", color: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400", scope: "pms" },
    { value: "appointment_rescheduled", label: "Rescheduled", color: "bg-blue-500/15 text-blue-600 border-blue-500/25 dark:text-blue-400", scope: "pms" },
    { value: "appointment_cancelled", label: "Cancelled", color: "bg-zinc-500/15 text-zinc-600 border-zinc-500/25 dark:text-zinc-400", scope: "pms" },
    { value: "insurance_verified", label: "Insurance Verified", color: "bg-green-500/15 text-green-600 border-green-500/25 dark:text-green-400", scope: "pms" },
    { value: "insurance_unverified", label: "Insurance Unverified", color: "bg-rose-500/15 text-rose-600 border-rose-500/25 dark:text-rose-400", scope: "pms" },
    // Emitted by both agents — these describe the call, not a PMS transaction.
    { value: "emergency", label: "Emergency", color: "bg-red-500/15 text-red-600 border-red-500/25 dark:text-red-400", scope: "both" },
    { value: "complaint", label: "Complaint", color: "bg-orange-500/15 text-orange-600 border-orange-500/25 dark:text-orange-400", scope: "both" },
    // No-PMS "Needs call back" folds into this same value so both vocabularies
    // land in the Callback Queue.
    { value: "needs_callback", label: "Needs Callback", color: "bg-amber-500/15 text-amber-600 border-amber-500/25 dark:text-amber-400", scope: "both" },
    { value: "faq_handled", label: "FAQ Handled", color: "bg-sky-500/15 text-sky-600 border-sky-500/25 dark:text-sky-400", scope: "both" },
    // The no-PMS token "Financial" maps to this same value — one shared concept.
    { value: "financial_inquiry", label: "Financial Inquiry", color: "bg-violet-500/15 text-violet-600 border-violet-500/25 dark:text-violet-400", scope: "both" },
    { value: "transferred", label: "Transferred", color: "bg-teal-500/15 text-teal-600 border-teal-500/25 dark:text-teal-400", scope: "both" },
    { value: "no_action_needed", label: "No Action Needed", color: "bg-zinc-500/10 text-zinc-500 border-zinc-500/20 dark:text-zinc-500", scope: "both" },
    // No-PMS vocabulary — requests the team books/handles manually.
    { value: "needs_booking", label: "Needs Booking", color: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400", scope: "no_pms" },
    { value: "needs_reschedule", label: "Needs Reschedule", color: "bg-blue-500/15 text-blue-600 border-blue-500/25 dark:text-blue-400", scope: "no_pms" },
    { value: "needs_cancellation", label: "Needs Cancellation", color: "bg-rose-500/15 text-rose-600 border-rose-500/25 dark:text-rose-400", scope: "no_pms" },
    { value: "insurance_and_billing", label: "Insurance & Billing", color: "bg-green-500/15 text-green-600 border-green-500/25 dark:text-green-400", scope: "no_pms" },
]

/**
 * The statuses this tenant's agent can actually produce.
 *
 * Filter menus should offer only these: a NexHealth clinic can never have a
 * "Needs Booking" call, and a no-PMS clinic can never have an "Appointment
 * Booked" one, so listing both vocabularies gives every user a menu that is
 * half dead options. Both PMS types (NexHealth, GoTracker) share one
 * vocabulary — the split is PMS vs no-PMS, not per vendor.
 */
export function callStatusFilterOptions(isNoPms: boolean): CallStatusOption[] {
    const wanted: CallStatusScope = isNoPms ? "no_pms" : "pms"
    return STATUS_OPTIONS.filter((o) => o.scope === "both" || o.scope === wanted)
}

export const DIRECTION_OPTIONS = [
    { value: "inbound", label: "Inbound" },
    { value: "outbound", label: "Outbound" },
]
