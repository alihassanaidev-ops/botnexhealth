import api from "@/lib/api"

const BASE = "/institution/campaign-email-templates"

/**
 * `institutionId` is for platform administrators, who have no institution of
 * their own and must name the one they are administering. A clinic admin omits
 * it — the API pins them to their own institution and refuses any other.
 */
type TargetInstitution = string | undefined

function scoped(institutionId: TargetInstitution, extra?: Record<string, unknown>) {
    const params = { ...(extra ?? {}) } as Record<string, unknown>
    if (institutionId) params.institution_id = institutionId
    return Object.keys(params).length > 0 ? { params } : undefined
}

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
    institutionId?: TargetInstitution,
): Promise<CampaignEmailTemplate[]> {
    const { data } = await api.get<{ templates: CampaignEmailTemplate[] }>(
        BASE,
        scoped(institutionId, activeOnly ? { active_only: true } : undefined),
    )
    return data.templates
}

export async function createCampaignEmailTemplate(
    body: CampaignEmailTemplateCreateRequest,
    institutionId?: TargetInstitution,
): Promise<CampaignEmailTemplate> {
    const { data } = await api.post<CampaignEmailTemplate>(
        BASE,
        body,
        scoped(institutionId),
    )
    return data
}

export async function updateCampaignEmailTemplate(
    key: string,
    body: CampaignEmailTemplateUpdateRequest,
    institutionId?: TargetInstitution,
): Promise<CampaignEmailTemplate> {
    const { data } = await api.put<CampaignEmailTemplate>(
        `${BASE}/${key}`,
        body,
        scoped(institutionId),
    )
    return data
}

export async function deleteCampaignEmailTemplate(
    key: string,
    institutionId?: TargetInstitution,
): Promise<void> {
    await api.delete(`${BASE}/${key}`, scoped(institutionId))
}

/** Render unsaved editor content against sample merge values. */
export async function previewCampaignEmailTemplate(
    body: {
        subject_template: string
        html_body: string
        text_body: string
    },
    institutionId?: TargetInstitution,
): Promise<CampaignEmailTemplatePreview> {
    const { data } = await api.post<CampaignEmailTemplatePreview>(
        `${BASE}/preview/live`,
        body,
        scoped(institutionId),
    )
    return data
}

export async function listCampaignMergeFields(
    institutionId?: TargetInstitution,
): Promise<CampaignMergeField[]> {
    const { data } = await api.get<{ fields: CampaignMergeField[] }>(
        `${BASE}/merge-fields`,
        scoped(institutionId),
    )
    return data.fields
}
