/** One person record, projected into relationship and patient directories. */

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
    lifecycle: "lead" | "contact" | "patient"
    lead_status: string | null
    source: string | null
    email_masked: string | null
    has_notes: boolean
    pms_last_synced_at: string | null
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
    lifecycle: "lead" | "contact" | "patient"
    lead_status: string | null
    source: string | null
    email_masked: string | null
    notes: string | null
    pms_last_synced_at: string | null
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
    directory?: "all" | "contacts" | "patients"
    lifecycle?: "lead" | "contact" | "patient"
}

export interface LivePatientListItem {
    pms_patient_id: string
    source: "nexhealth" | "gotracker" | string
    first_name: string
    last_name: string
    full_name: string
    inactive: boolean
    email: string | null
    phone: string | null
    email_masked: string | null
    phone_masked: string | null
    contact_details_masked: boolean
    can_reveal_contact_details: boolean
    pms_updated_at: string | null
    pms_last_sync_time: string | null
    contact_id: string | null
}

export interface LivePatientPage {
    source: string
    fetched_at: string
    total: number | null
    returned: number
    items: LivePatientListItem[]
    next_cursor: string | null
    previous_cursor: string | null
    has_next_page: boolean
    has_previous_page: boolean
}

export interface LivePatientFilters {
    locationId: string
    cursor?: string | null
    pageSize?: number
    search?: string
    patientStatus?: "active" | "inactive" | "all"
    revealPatientId?: string
}

export async function listLivePatients(filters: LivePatientFilters): Promise<LivePatientPage> {
    const params = new URLSearchParams({ location_id: filters.locationId })
    if (filters.cursor) params.set("cursor", filters.cursor)
    if (filters.pageSize !== undefined) params.set("page_size", String(filters.pageSize))
    if (filters.search) params.set("search", filters.search)
    if (filters.patientStatus) params.set("patient_status", filters.patientStatus)
    if (filters.revealPatientId) params.set("reveal_patient_id", filters.revealPatientId)
    const { data } = await api.get<LivePatientPage>(`/v1/pms/patients/page?${params.toString()}`)
    return data
}

export async function listContacts(filters: ContactsFilters = {}): Promise<ContactsListResponse> {
    const params = new URLSearchParams()
    if (filters.limit !== undefined) params.set("limit", String(filters.limit))
    if (filters.offset !== undefined) params.set("offset", String(filters.offset))
    if (filters.search) params.set("search", filters.search)
    if (filters.directory) params.set("directory", filters.directory)
    if (filters.lifecycle) params.set("lifecycle", filters.lifecycle)
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<ContactsListResponse>(`/institution/contacts${q}`)
    return data
}

export interface ContactCreate {
    first_name?: string
    last_name?: string
    phone?: string
    email?: string
    notes?: string
    location_id?: string | null
    consent_sms?: boolean
    consent_email?: boolean
    consent_wording?: string
}

export interface ContactCreateResponse {
    contact: ContactDetail
    created: boolean
    matched_existing_patient: boolean
}

export async function createContact(body: ContactCreate): Promise<ContactCreateResponse> {
    const { data } = await api.post<ContactCreateResponse>("/institution/contacts", body)
    return data
}

export async function updateContact(
    contactId: string,
    body: { notes?: string | null; lead_status?: string },
): Promise<ContactDetail> {
    const { data } = await api.patch<ContactDetail>(
        `/institution/contacts/${contactId}`,
        body,
    )
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
