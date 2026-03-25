import api from "@/lib/api"

export interface TemplateVariable {
    key: string
    label: string
    sample: string
}

export interface EmailTemplate {
    id: string
    institution_id: string
    template_type: string
    name: string
    subject_template: string
    html_body: string
    text_body: string
    is_active: boolean
    created_at: string
    updated_at: string
    variables: TemplateVariable[]
}

export interface EmailTemplateListResponse {
    templates: EmailTemplate[]
}

export interface EmailTemplateUpdateRequest {
    name?: string
    subject_template?: string
    html_body?: string
    text_body?: string
    is_active?: boolean
}

export interface EmailTemplatePreviewResponse {
    subject: string
    html: string
    text: string
}

export async function listEmailTemplates(): Promise<EmailTemplate[]> {
    const { data } = await api.get<EmailTemplateListResponse>("/institution/email-templates")
    return data.templates
}

export async function getEmailTemplate(templateType: string): Promise<EmailTemplate> {
    const { data } = await api.get<EmailTemplate>(`/institution/email-templates/${templateType}`)
    return data
}

export async function updateEmailTemplate(
    templateType: string,
    body: EmailTemplateUpdateRequest,
): Promise<EmailTemplate> {
    const { data } = await api.put<EmailTemplate>(
        `/institution/email-templates/${templateType}`,
        body,
    )
    return data
}

export async function resetEmailTemplate(templateType: string): Promise<EmailTemplate> {
    const { data } = await api.post<EmailTemplate>(
        `/institution/email-templates/${templateType}/reset`,
    )
    return data
}

export async function previewEmailTemplate(templateType: string): Promise<EmailTemplatePreviewResponse> {
    const { data } = await api.get<EmailTemplatePreviewResponse>(
        `/institution/email-templates/${templateType}/preview`,
    )
    return data
}

export async function livePreviewEmailTemplate(body: {
    subject_template: string
    html_body: string
    text_body: string
    template_type: string
}): Promise<EmailTemplatePreviewResponse> {
    const { data } = await api.post<EmailTemplatePreviewResponse>(
        "/institution/email-templates/preview/live",
        body,
    )
    return data
}
