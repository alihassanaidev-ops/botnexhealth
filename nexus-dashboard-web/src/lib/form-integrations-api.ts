import api from "@/lib/api"

/**
 * Connected lead-form providers.
 *
 * The clinic authorises Meta or Typeform, syncs the forms on that account, says
 * what each question means, and only then switches a form on. Nothing here ever
 * carries a provider token: the server keeps it encrypted and hands back only
 * the account it belongs to.
 */
const BASE = "/institution/form-integrations"

export type FormProvider = "meta" | "typeform"

export type ProviderStatus = {
    provider: FormProvider
    label: string
    /** False when this deployment has no OAuth app for the provider. */
    configured: boolean
    connection_count: number
}

export type FormConnection = {
    id: string
    provider: FormProvider
    account_ref: string
    account_name: string | null
    /** "active" | "needs_reauth" | "revoked" */
    status: string
    granted_scopes: string | null
    token_expires_at: string | null
    last_synced_at: string | null
    last_error: string | null
    form_count: number
    created_at: string
    /** Set when the practice disconnected it; the history is kept. */
    disconnected_at: string | null
}

export type FormFieldSpec = {
    key: string
    label: string
    type: string
    options?: string[]
}

export type FieldMapping = {
    id: string
    source_key: string
    source_label: string | null
    source_type: string | null
    /** "contact_field" | "custom_field" | "ignore" */
    target_kind: string
    target_contact_field: string | null
    target_custom_field_id: string | null
    /** Where this answer appears in a workflow's context, or null if nowhere. */
    context_key: string | null
}

export type FormSummary = {
    id: string
    provider: FormProvider
    external_form_id: string
    name: string
    location_id: string | null
    is_enabled: boolean
    source_name: string
    /** "none" | "registered" | "failed" */
    webhook_status: string
    webhook_last_error: string | null
    consent_sms: boolean
    consent_email: boolean
    archived_at: string | null
    last_submission_at: string | null
    last_synced_at: string | null
    connection_id: string
    /** Answer keys a workflow can branch on. */
    context_keys: string[]
    /** Submissions that arrived and did not become a contact. */
    unprocessed_count: number
    /** Why the most recent one was not processed. */
    last_issue: string | null
}

export type FormDetail = FormSummary & {
    fields: FormFieldSpec[]
    mappings: FieldMapping[]
}

export type FormSubmissionSummary = {
    id: string
    external_submission_id: string
    contact_id: string | null
    status: string
    error_summary: string | null
    context_answers: Record<string, unknown> | null
    submitted_at: string | null
    received_at: string
}

export type SyncResult = {
    discovered: number
    created: number
    updated: number
    archived: number
    new_fields: number
}

export type MappingUpsert = {
    source_key: string
    target_kind: string
    target_contact_field?: string | null
    target_custom_field_id?: string | null
}

/** Contact columns a question may be mapped onto. Mirrors CONTACT_FIELD_KEYS. */
export const CONTACT_FIELD_OPTIONS: Array<{ value: string; label: string }> = [
    { value: "first_name", label: "First name" },
    { value: "last_name", label: "Last name" },
    { value: "full_name", label: "Full name" },
    { value: "email", label: "Email" },
    { value: "phone", label: "Phone" },
    { value: "notes", label: "Notes" },
]

export async function listProviders(): Promise<ProviderStatus[]> {
    const { data } = await api.get<ProviderStatus[]>(`${BASE}/providers`)
    return Array.isArray(data) ? data : []
}

export async function listConnections(): Promise<FormConnection[]> {
    const { data } = await api.get<FormConnection[]>(`${BASE}/connections`)
    return Array.isArray(data) ? data : []
}

export async function startOAuth(
    provider: FormProvider,
): Promise<{ authorization_url: string; state: string }> {
    const { data } = await api.post(`${BASE}/oauth/start`, { provider })
    return data
}

export async function completeOAuth(
    code: string,
    state: string,
): Promise<{ provider: FormProvider; connections: FormConnection[] }> {
    const { data } = await api.post(`${BASE}/oauth/callback`, { code, state })
    return data
}

export async function syncConnection(id: string): Promise<SyncResult> {
    const { data } = await api.post<SyncResult>(`${BASE}/connections/${id}/sync`)
    return data
}

export async function disconnect(id: string): Promise<void> {
    await api.delete(`${BASE}/connections/${id}`)
}

export async function listForms(params?: {
    provider?: FormProvider
    enabledOnly?: boolean
}): Promise<FormSummary[]> {
    const { data } = await api.get<FormSummary[]>(`${BASE}/forms`, {
        params: {
            provider: params?.provider,
            enabled_only: params?.enabledOnly ? true : undefined,
        },
    })
    return Array.isArray(data) ? data : []
}

export async function getForm(id: string): Promise<FormDetail> {
    const { data } = await api.get<FormDetail>(`${BASE}/forms/${id}`)
    return data
}

export async function updateForm(
    id: string,
    body: {
        is_enabled?: boolean
        location_id?: string | null
        source_name?: string
        consent_sms?: boolean
        consent_email?: boolean
        consent_wording?: string | null
    },
): Promise<FormSummary> {
    const { data } = await api.patch<FormSummary>(`${BASE}/forms/${id}`, body)
    return data
}

export async function saveMappings(
    id: string,
    mappings: MappingUpsert[],
): Promise<FormDetail> {
    const { data } = await api.put<FormDetail>(`${BASE}/forms/${id}/mappings`, {
        mappings,
    })
    return data
}

export async function listSubmissions(
    id: string,
): Promise<FormSubmissionSummary[]> {
    const { data } = await api.get<FormSubmissionSummary[]>(
        `${BASE}/forms/${id}/submissions`,
    )
    return Array.isArray(data) ? data : []
}
