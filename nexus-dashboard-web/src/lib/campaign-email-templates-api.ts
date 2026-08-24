import api from "@/lib/api"

const BASE = "/institution/campaign-email-templates"

/** A clinic-authored template, reusable across campaigns. Distinct from the
 *  five fixed system notification templates in `email-templates-api.ts`. */
export interface CampaignEmailTemplate {
    id: string
    key: string
    name: string
    subject_template: string
    html_body: string
    text_body: string
    is_active: boolean
}

export interface CampaignEmailTemplateCreateRequest {
    name: string
    subject_template: string
    html_body: string
    text_body: string
    /** Derived from `name` when omitted. Immutable once created. */
    key?: string | null
    is_active?: boolean
}

export interface CampaignEmailTemplateUpdateRequest {
    name?: string
    subject_template?: string
    html_body?: string
    text_body?: string
    is_active?: boolean
}

export interface CampaignEmailTemplatePreview {
    subject: string
    html: string
    text: string
}

export interface CampaignMergeField {
    name: string
    label: string
    description: string
    sample: string
    group: string
    phi_level: string
}

export async function listCampaignEmailTemplates(
    activeOnly = false,
): Promise<CampaignEmailTemplate[]> {
    const { data } = await api.get<{ templates: CampaignEmailTemplate[] }>(BASE, {
        params: activeOnly ? { active_only: true } : undefined,
    })
    return data.templates
}

export async function createCampaignEmailTemplate(
    body: CampaignEmailTemplateCreateRequest,
): Promise<CampaignEmailTemplate> {
    const { data } = await api.post<CampaignEmailTemplate>(BASE, body)
    return data
}

export async function updateCampaignEmailTemplate(
    key: string,
    body: CampaignEmailTemplateUpdateRequest,
): Promise<CampaignEmailTemplate> {
    const { data } = await api.put<CampaignEmailTemplate>(`${BASE}/${key}`, body)
    return data
}

export async function deleteCampaignEmailTemplate(key: string): Promise<void> {
    await api.delete(`${BASE}/${key}`)
}

/** Render unsaved editor content against sample merge values. */
export async function previewCampaignEmailTemplate(body: {
    subject_template: string
    html_body: string
    text_body: string
}): Promise<CampaignEmailTemplatePreview> {
    const { data } = await api.post<CampaignEmailTemplatePreview>(
        `${BASE}/preview/live`,
        body,
    )
    return data
}

export async function listCampaignMergeFields(): Promise<CampaignMergeField[]> {
    const { data } = await api.get<{ fields: CampaignMergeField[] }>(
        `${BASE}/merge-fields`,
    )
    return data.fields
}
