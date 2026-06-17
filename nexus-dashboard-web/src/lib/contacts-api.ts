/**
 * Contacts (patients) API service — institution-facing patient directory.
 *
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api"

export interface ContactCallSummary {
    id: string
    contact_id: string | null
    call_date: string | null
    call_time: string | null
    call_status: string | null
    call_tags: string[]
    summary: string | null
    callback_resolved: boolean
    created_at: string
}

export interface ContactAlias {
    id: string
    full_name: string | null
    phone_masked: string | null
    phone_reveal_available: boolean
}

export interface ContactListItem {
    id: string
    full_name: string | null
    first_name: string | null
    last_name: string | null
    is_new_patient: boolean
    phone_masked: string | null
    phone_reveal_available: boolean
    call_count: number
    last_call_at: string | null
    alias_count: number
    created_at: string
}

export interface ContactsListResponse {
    total: number
    limit: number
    offset: number
    items: ContactListItem[]
}

export interface ContactDetail {
    id: string
    full_name: string | null
    first_name: string | null
    last_name: string | null
    is_new_patient: boolean
    phone_masked: string | null
    phone_reveal_available: boolean
    created_at: string
    aliases: ContactAlias[]
    calls: ContactCallSummary[]
    call_count: number
}

export interface ContactPhoneReveal {
    contact_id: string
    phone: string | null
}

export interface ContactsFilters {
    limit?: number
    offset?: number
    search?: string
}

export async function listContacts(filters: ContactsFilters = {}): Promise<ContactsListResponse> {
    const params = new URLSearchParams()
    if (filters.limit !== undefined) params.set("limit", String(filters.limit))
    if (filters.offset !== undefined) params.set("offset", String(filters.offset))
    if (filters.search) params.set("search", filters.search)
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<ContactsListResponse>(`/institution/contacts${q}`)
    return data
}

export async function getContact(contactId: string): Promise<ContactDetail> {
    const { data } = await api.get<ContactDetail>(`/institution/contacts/${contactId}`)
    return data
}

export async function revealContactPhone(contactId: string): Promise<ContactPhoneReveal> {
    const { data } = await api.post<ContactPhoneReveal>(
        `/institution/contacts/${contactId}/reveal/phone`,
    )
    return data
}

export async function mergeContact(contactId: string, aliasId: string): Promise<ContactDetail> {
    const { data } = await api.post<ContactDetail>(
        `/institution/contacts/${contactId}/merge`,
        { alias_id: aliasId },
    )
    return data
}

export async function unmergeContact(contactId: string, aliasId: string): Promise<ContactDetail> {
    const { data } = await api.post<ContactDetail>(
        `/institution/contacts/${contactId}/unmerge`,
        { alias_id: aliasId },
    )
    return data
}
