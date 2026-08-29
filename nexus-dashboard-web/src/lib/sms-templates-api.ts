import api from "@/lib/api"

const BASE = "/institution/sms-templates"

export interface TemplateVariable {
    key: string
    label: string
    sample: string
}

export interface SmsTemplate {
    id: string
    institution_id: string
    template_type: string
    name: string
    /** SMS has no subject/HTML — a single plain-text body. */
    body: string
    is_active: boolean
    created_at: string
    updated_at: string
    variables: TemplateVariable[]
}

export interface SmsTemplateListResponse {
    templates: SmsTemplate[]
}

export interface SmsTemplateUpdateRequest {
    name?: string
    body?: string
    is_active?: boolean
}

export interface SmsTemplatePreviewResponse {
    body: string
}

export interface SmsTemplateValidateResponse {
    valid: boolean
    error: string | null
}

export async function listSmsTemplates(): Promise<SmsTemplate[]> {
    const { data } = await api.get<SmsTemplateListResponse>(BASE)
    return data.templates
}

export async function updateSmsTemplate(
    templateType: string,
    body: SmsTemplateUpdateRequest,
): Promise<SmsTemplate> {
    const { data } = await api.put<SmsTemplate>(`${BASE}/${templateType}`, body)
    return data
}

export async function resetSmsTemplate(templateType: string): Promise<SmsTemplate> {
    const { data } = await api.post<SmsTemplate>(`${BASE}/${templateType}/reset`)
    return data
}

/** Render the *saved* template with sample data. */
export async function previewSmsTemplate(templateType: string): Promise<SmsTemplatePreviewResponse> {
    const { data } = await api.get<SmsTemplatePreviewResponse>(`${BASE}/${templateType}/preview`)
    return data
}

/** Render *unsaved* editor content, so the preview tracks what you're typing. */
export async function livePreviewSmsTemplate(body: {
    body: string
    template_type: string
}): Promise<SmsTemplatePreviewResponse> {
    const { data } = await api.post<SmsTemplatePreviewResponse>(`${BASE}/preview/live`, body)
    return data
}

export async function validateSmsTemplate(templateStr: string): Promise<SmsTemplateValidateResponse> {
    const { data } = await api.post<SmsTemplateValidateResponse>(`${BASE}/validate`, {
        template_str: templateStr,
    })
    return data
}
