import api from "@/lib/api"

const BASE = "/inbox"

export type InboxChannel = "sms" | "email"

export interface InboxThread {
    id: string
    channel: InboxChannel
    status: string
    institution_id: string
    location_id: string | null
    institution_name: string | null
    location_name: string | null
    contact_id: string | null
    contact_name: string | null
    contact_masked_email: string | null
    last_message_at: string | null
    opened_at: string | null
    unresolved_handoffs: number
    assignee_user_id: string | null
    latest_intent: string | null
    /** The latest reply came from an address we did not mail — a forwarded copy
     *  or a shared mailbox. Identity must not be assumed from the thread. */
    sender_mismatch: boolean
}

export interface InboxMessage {
    id: string
    direction: string
    channel: InboxChannel
    body: string | null
    subject: string | null
    intent: string | null
    created_at: string | null
    from_masked: string | null
    sender_mismatch: boolean
}

export interface InboxThreadDetail {
    thread: InboxThread
    messages: InboxMessage[]
}

export interface InboxFilters {
    channel?: InboxChannel
    status?: string
    institution_id?: string
    location_id?: string
    assigned_to?: string
    unresolved_only?: boolean
    limit?: number
    offset?: number
}

export interface InboxActivityRow {
    institution_id: string
    location_id: string | null
    channel: string
    threads: number
    open_threads: number
    avg_resolution_seconds: number | null
}

export interface InboxActivity {
    since: string
    days: number
    breakdown: InboxActivityRow[]
    threads: number
    open_threads: number
    unresolved_handoffs: number
}

export interface InboxScopeLocation {
    id: string
    name: string
}

export interface InboxScopeInstitution {
    id: string
    name: string
    locations: InboxScopeLocation[]
}

/**
 * What this caller may filter by, and what they may do.
 *
 * The capability flags come from the server rather than being re-derived from
 * the role here — one authority for the permission model, so the UI cannot
 * drift from what the API actually enforces.
 */
export interface InboxScopes {
    role: string
    institutions: InboxScopeInstitution[]
    can_filter_institution: boolean
    can_filter_location: boolean
    can_read_content: boolean
    can_write: boolean
    can_assign: boolean
}

export async function getInboxScopes(): Promise<InboxScopes> {
    const { data } = await api.get<InboxScopes>(`${BASE}/scopes`)
    return data
}

export async function listInboxThreads(
    filters: InboxFilters = {},
): Promise<InboxThread[]> {
    const { data } = await api.get<{ threads: InboxThread[] }>(`${BASE}/threads`, {
        params: filters,
    })
    return data.threads
}

export async function getInboxThread(id: string): Promise<InboxThreadDetail> {
    const { data } = await api.get<InboxThreadDetail>(`${BASE}/threads/${id}`)
    return data
}

export async function assignInboxThread(
    id: string,
    assigneeUserId: string | null,
): Promise<void> {
    await api.post(`${BASE}/threads/${id}/assign`, { assignee_user_id: assigneeUserId })
}

export async function resolveInboxThread(
    id: string,
    outcome?: string,
): Promise<void> {
    await api.post(`${BASE}/threads/${id}/resolve`, { outcome: outcome ?? null })
}

/** Group oversight: counts and timings only, no patient content. */
export async function getInboxActivity(days = 30): Promise<InboxActivity> {
    const { data } = await api.get<InboxActivity>(`${BASE}/activity`, {
        params: { days },
    })
    return data
}
