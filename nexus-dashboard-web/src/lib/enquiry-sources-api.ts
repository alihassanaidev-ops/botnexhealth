import api from "@/lib/api"

const BASE = "/institution/enquiry-sources"

export type EnquirySource = {
    id: string
    label: string
    location_id: string | null
    source_name: string
    is_active: boolean
    has_signing_secret: boolean
    default_attribution: Record<string, unknown> | null
    created_at: string
    last_used_at: string | null
}

/**
 * The only shape that ever carries the token. Returned by create and rotate,
 * and by nothing else — the server keeps a hash, so there is no way to ask for
 * it again.
 */
export type EnquirySourceCreated = EnquirySource & {
    token: string
    intake_url: string
}

export type EnquirySourceCreate = {
    label: string
    location_id?: string | null
    source_name?: string
    signing_secret?: string | null
    default_attribution?: Record<string, unknown> | null
}

export async function listEnquirySources(): Promise<EnquirySource[]> {
    const { data } = await api.get<EnquirySource[]>(BASE)
    return Array.isArray(data) ? data : []
}

export async function createEnquirySource(
    body: EnquirySourceCreate,
): Promise<EnquirySourceCreated> {
    const { data } = await api.post<EnquirySourceCreated>(BASE, body)
    return data
}

export async function updateEnquirySource(
    id: string,
    body: { label?: string; is_active?: boolean },
): Promise<EnquirySource> {
    const { data } = await api.patch<EnquirySource>(`${BASE}/${id}`, body)
    return data
}

export async function rotateEnquirySource(
    id: string,
): Promise<EnquirySourceCreated> {
    const { data } = await api.post<EnquirySourceCreated>(`${BASE}/${id}/rotate`)
    return data
}
