/**
 * Resolve a location's channel-readiness report against the channels a workflow
 * definition actually uses, producing (a) a per-used-channel status for the
 * builder's readiness indicator and (b) graph-level WARNING issues for the
 * validation panel.
 *
 * Readiness is ADVISORY (Plan 02 B6 / Plan 10): an unready channel that the
 * definition uses warns at publish but never hard-blocks it. Pure module.
 */
import type {
    ChannelKey,
    ChannelReadiness,
    ValidationIssue,
    WorkflowDefinition,
} from "@/types/workflow"
import { channelsUsed } from "./graph"

export const CHANNEL_LABELS: Record<ChannelKey, string> = {
    sms: "SMS",
    email: "Email",
    voice: "Voice",
}

export interface ChannelStatus {
    channel: ChannelKey
    label: string
    ready: boolean
    /** Why the channel is (not) ready — surfaced from the backend detail. */
    reason: string | null
}

/**
 * Status for each channel the definition uses, resolved against `readiness`.
 * Channels the workflow does not use are omitted. Stable order: sms, email, voice.
 */
export function usedChannelStatuses(
    def: WorkflowDefinition,
    readiness: ChannelReadiness,
): ChannelStatus[] {
    const used = channelsUsed(def)
    const readyByChannel: Record<ChannelKey, boolean> = {
        sms: readiness.sms,
        email: readiness.email,
        voice: readiness.voice_configurable,
    }
    const reasonByChannel = new Map<string, string | null>(
        readiness.details.map((d) => [d.channel, d.reason ?? null]),
    )
    const order: ChannelKey[] = ["sms", "email", "voice"]
    return order
        .filter((c) => used.has(c))
        .map((channel) => ({
            channel,
            label: CHANNEL_LABELS[channel],
            ready: readyByChannel[channel],
            reason: reasonByChannel.get(channel) ?? null,
        }))
}

/** WARNING issues for the used channels that are not ready (graph-level). */
export function readinessIssues(statuses: ChannelStatus[]): ValidationIssue[] {
    return statuses
        .filter((s) => !s.ready)
        .map((s) => ({
            node_id: null,
            severity: "warning" as const,
            message: `${s.label} is not set up for this location.`,
            fix: s.reason ?? undefined,
        }))
}

/** True if any channel the workflow uses is not ready for this location. */
export function hasUnreadyChannel(statuses: ChannelStatus[]): boolean {
    return statuses.some((s) => !s.ready)
}
