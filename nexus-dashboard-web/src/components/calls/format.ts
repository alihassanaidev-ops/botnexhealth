/** Pure formatting helpers for the Calls surface (no JSX — safe for fast-refresh). */

export function formatDuration(seconds: number | null): string {
    if (seconds === null) return "—"
    if (seconds < 60) return `${seconds}s`
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`
    const h = Math.floor(m / 60)
    const rem = m % 60
    return rem > 0 ? `${h}h ${rem}m` : `${h}h`
}

export function formatDateTime(dateStr: string | null, timeStr: string | null): string {
    if (!dateStr) return "—"
    const d = new Date(dateStr)
    const datePart = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    if (!timeStr) return datePart
    const [h, m] = timeStr.split(":")
    const hour = parseInt(h, 10)
    const ampm = hour >= 12 ? "PM" : "AM"
    const h12 = hour % 12 || 12
    return `${datePart} · ${h12}:${m} ${ampm}`
}

/** Short "Jun 13, 2:40 PM" form for dense list rails. */
export function formatListTimestamp(dateStr: string | null, timeStr: string | null): string {
    if (!dateStr) return "—"
    const d = new Date(dateStr)
    const datePart = d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
    if (!timeStr) return datePart
    const [h, m] = timeStr.split(":")
    const hour = parseInt(h, 10)
    const ampm = hour >= 12 ? "PM" : "AM"
    const h12 = hour % 12 || 12
    return `${datePart}, ${h12}:${m} ${ampm}`
}

/** How a caller should be labelled in a list.
 *
 *  `kind` drives the styling: a real name reads as a name, a phone number gets
 *  tabular figures so a column of them lines up, and only a caller we can say
 *  nothing at all about gets the muted italic placeholder. */
export interface CallerLabel {
    text: string
    kind: "name" | "phone" | "unknown"
}

/** Label an unnamed caller by their number rather than "Unknown caller".
 *
 *  The list payload already carries the number — in full for no-PMS location
 *  admins, masked to the last four digits for everyone else (see
 *  `_call_to_record` in `src/app/api/routes/calls.py`). Falling back to it
 *  means two unnamed callers are still telling apart, which "Unknown caller"
 *  twice over never was. No reveal is triggered here: this renders only what
 *  the response already contains. */
export function callerLabel(
    name: string | null | undefined,
    phone: string | null | undefined,
): CallerLabel {
    const trimmedName = name?.trim()
    if (trimmedName) return { text: trimmedName, kind: "name" }

    // The backend's mask_phone() yields "Unknown" when there were no digits to
    // mask — that is not a label worth showing in place of one.
    const trimmedPhone = phone?.trim()
    if (trimmedPhone && trimmedPhone.toLowerCase() !== "unknown") {
        return { text: trimmedPhone, kind: "phone" }
    }

    return { text: "Unknown caller", kind: "unknown" }
}

/** "Ashley Bentley" → "AB"; single word → first two letters; empty → "?". */
export function getInitials(name: string | null | undefined): string {
    const parts = (name ?? "").trim().split(/\s+/).filter(Boolean)
    if (parts.length === 0) return "?"
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/** "just now" / "12m ago" / "3h ago" / "Jun 13" for a note timestamp.
 *
 *  Notes read as a message thread, so recency matters more than the exact
 *  clock time; anything older than a week falls back to a plain date. */
export function formatRelativeTime(iso: string): string {
    const then = new Date(iso).getTime()
    if (Number.isNaN(then)) return "—"
    const seconds = Math.floor((Date.now() - then) / 1000)
    if (seconds < 45) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

/** Absolute form for the note timestamp's tooltip. */
export function formatAbsoluteTime(iso: string): string {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return "—"
    return d.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
    })
}
