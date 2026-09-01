import api from "@/lib/api"

const BASE = "/institution/enquiries"

/** Derived server-side from whether the person exists in the practice software. */
export type EnquiryStage = "lead" | "contacted" | "registered" | "booked"

export type Enquiry = {
    id: string
    first_name: string | null
    last_name: string | null
    phone_masked: string | null
    email_masked: string | null
    status: string
    stage: EnquiryStage
    source: string
    contact_id: string | null
    has_notes: boolean
    created_at: string
    updated_at: string
}

export type EnquiryDetail = Enquiry & {
    notes: string | null
    attribution: Record<string, unknown> | null
    external_ref: string | null
    intake_key: string
    location_id: string | null
}

export type EnquiryList = {
    items: Enquiry[]
    total: number
    limit: number
    offset: number
}

export async function listEnquiries(params: {
    stage?: EnquiryStage
    search?: string
    limit?: number
    offset?: number
} = {}): Promise<EnquiryList> {
    const { data } = await api.get<EnquiryList>(BASE, { params })
    return data
}

export async function getEnquiry(id: string): Promise<EnquiryDetail> {
    const { data } = await api.get<EnquiryDetail>(`${BASE}/${id}`)
    return data
}

export async function updateEnquiry(
    id: string,
    body: { notes?: string; status?: string },
): Promise<EnquiryDetail> {
    const { data } = await api.patch<EnquiryDetail>(`${BASE}/${id}`, body)
    return data
}

export type EnquiryCreate = {
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

export type EnquiryCreated = {
    enquiry: EnquiryDetail
    /** False when this person was already on the list. */
    created: boolean
    matched_existing_contact: boolean
}

export async function createEnquiry(body: EnquiryCreate): Promise<EnquiryCreated> {
    const { data } = await api.post<EnquiryCreated>(BASE, body)
    return data
}
