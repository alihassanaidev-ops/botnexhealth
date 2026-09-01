import api from "@/lib/api"

export type UndeliverableStatus = "open" | "replayed" | "discarded"
export type DismissalReason =
    | "resolved_elsewhere"
    | "duplicate"
    | "not_actionable"
    | "superseded"
    | "other"

export interface UndeliverableEvent {
    id: string
    source: string
    event_type: string
    status: UndeliverableStatus
    attempts: number
    last_error: string
    payload_hash: string
    redacted_payload: Record<string, unknown> | null
    institution_id: string | null
    location_id: string | null
    created_at: string
    updated_at: string
    resolved_at: string | null
    resolution_reason: DismissalReason | null
    resolution_note: string | null
    replay_supported: boolean
    originating_run_id: string | null
    originating_timer_id: string | null
}

export interface UndeliverableListResponse {
    items: UndeliverableEvent[]
    total: number
    page: number
    size: number
    pages: number
}

export type UndeliverableScope = "platform" | "institution"

function basePath(scope: UndeliverableScope): string {
    return scope === "platform"
        ? "/admin/dead-letter-events"
        : "/institution/undeliverables"
}

export async function listUndeliverables(
    scope: UndeliverableScope,
    options: { page?: number; size?: number; status?: UndeliverableStatus | "all" } = {},
): Promise<UndeliverableListResponse> {
    const params = new URLSearchParams({
        page: String(options.page ?? 1),
        size: String(options.size ?? 50),
    })
    if (options.status && options.status !== "all") {
        params.set("status", options.status)
    } else if (options.status === "all") {
        // The backend treats an explicit empty value as no status filter.
        params.set("status", "")
    }
    const { data } = await api.get<UndeliverableListResponse>(
        `${basePath(scope)}?${params.toString()}`,
    )
    return data
}

export async function retryUndeliverable(
    scope: UndeliverableScope,
    eventId: string,
): Promise<UndeliverableEvent> {
    const { data } = await api.post<UndeliverableEvent>(
        `${basePath(scope)}/${eventId}/replay`,
    )
    return data
}

export async function dismissUndeliverable(
    scope: UndeliverableScope,
    eventId: string,
    payload: { reason: DismissalReason; note?: string },
): Promise<UndeliverableEvent> {
    const { data } = await api.post<UndeliverableEvent>(
        `${basePath(scope)}/${eventId}/discard`,
        payload,
    )
    return data
}
