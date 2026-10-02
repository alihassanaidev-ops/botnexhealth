/**
 * Presentation helpers for the workflow builder.
 *
 * Lifted out of the old `test-run` module when its client-side run simulator
 * was removed: the simulator was a second implementation of the engine that
 * silently stood in when the server dry-run failed, but this is just
 * formatting and several nodes render through it.
 */

export function humanizeSeconds(seconds: number): string {
    if (seconds <= 0) return "0 seconds"
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    const parts: string[] = []
    if (days) parts.push(`${days} day${days > 1 ? "s" : ""}`)
    if (hours) parts.push(`${hours} hour${hours > 1 ? "s" : ""}`)
    if (mins) parts.push(`${mins} minute${mins > 1 ? "s" : ""}`)
    return parts.length ? parts.join(", ") : `${seconds} seconds`
}
