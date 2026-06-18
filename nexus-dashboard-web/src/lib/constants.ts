// Dark-mode-aware colors using opacity modifiers (safe for both light and dark)
export const STATUS_OPTIONS: { value: string; label: string; color: string }[] = [
    { value: "appointment_booked", label: "Appointment Booked", color: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400" },
    { value: "appointment_rescheduled", label: "Rescheduled", color: "bg-blue-500/15 text-blue-600 border-blue-500/25 dark:text-blue-400" },
    { value: "appointment_cancelled", label: "Cancelled", color: "bg-zinc-500/15 text-zinc-600 border-zinc-500/25 dark:text-zinc-400" },
    { value: "emergency", label: "Emergency", color: "bg-red-500/15 text-red-600 border-red-500/25 dark:text-red-400" },
    { value: "complaint", label: "Complaint", color: "bg-orange-500/15 text-orange-600 border-orange-500/25 dark:text-orange-400" },
    { value: "needs_callback", label: "Needs Callback", color: "bg-amber-500/15 text-amber-600 border-amber-500/25 dark:text-amber-400" },
    { value: "faq_handled", label: "FAQ Handled", color: "bg-sky-500/15 text-sky-600 border-sky-500/25 dark:text-sky-400" },
    { value: "financial_inquiry", label: "Financial Inquiry", color: "bg-violet-500/15 text-violet-600 border-violet-500/25 dark:text-violet-400" },
    { value: "transferred", label: "Transferred", color: "bg-teal-500/15 text-teal-600 border-teal-500/25 dark:text-teal-400" },
    { value: "insurance_verified", label: "Insurance Verified", color: "bg-green-500/15 text-green-600 border-green-500/25 dark:text-green-400" },
    { value: "insurance_unverified", label: "Insurance Unverified", color: "bg-rose-500/15 text-rose-600 border-rose-500/25 dark:text-rose-400" },
    { value: "no_action_needed", label: "No Action Needed", color: "bg-zinc-500/10 text-zinc-500 border-zinc-500/20 dark:text-zinc-500" },
    // No-PMS vocabulary — requests the team books/handles manually.
    { value: "needs_booking", label: "Needs Booking", color: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400" },
    { value: "needs_reschedule", label: "Needs Reschedule", color: "bg-blue-500/15 text-blue-600 border-blue-500/25 dark:text-blue-400" },
    { value: "needs_cancellation", label: "Needs Cancellation", color: "bg-rose-500/15 text-rose-600 border-rose-500/25 dark:text-rose-400" },
    { value: "insurance_and_billing", label: "Insurance & Billing", color: "bg-green-500/15 text-green-600 border-green-500/25 dark:text-green-400" },
]

export const DIRECTION_OPTIONS = [
    { value: "inbound", label: "Inbound" },
    { value: "outbound", label: "Outbound" },
]
