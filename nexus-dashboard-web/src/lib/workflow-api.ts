/**
 * Workflow Builder API client. Mirrors the thin `automation-api.ts` idiom
 * (import shared axios, async fn -> api.get/post -> return data, no try/catch —
 * pages handle errors). Endpoints per findings.md §3 (base already ends in `/api`).
 */
import api from "@/lib/api"
import type { AutomationWorkflow } from "@/types"
import type { WorkflowDefinition } from "@/types/workflow"

/** Response of `GET /automation/templates` (definition is the Plan-01 shape). */
export interface CampaignTemplate {
    id: string
    name: string
    description: string
    trigger_type: string
    definition: WorkflowDefinition
    tags: string[]
}

// ---- Workflows ----
export async function listWorkflows(): Promise<AutomationWorkflow[]> {
    const { data } = await api.get<AutomationWorkflow[]>("/automation/workflows")
    return data
}

export async function getWorkflow(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.get<AutomationWorkflow>(`/automation/workflows/${id}`)
    return data
}

export async function createWorkflow(payload: {
    name: string
    definition: WorkflowDefinition
}): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>("/automation/workflows", payload)
    return data
}

export async function updateWorkflow(
    id: string,
    payload: { name?: string; definition?: WorkflowDefinition },
): Promise<AutomationWorkflow> {
    const { data } = await api.patch<AutomationWorkflow>(
        `/automation/workflows/${id}`,
        payload,
    )
    return data
}

export async function publishWorkflow(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/publish`)
    return data
}

export async function pauseWorkflow(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/pause`)
    return data
}

export async function resumeWorkflow(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/resume`)
    return data
}

export async function archiveWorkflow(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/archive`)
    return data
}

// ---- Templates ----
export async function listTemplates(): Promise<CampaignTemplate[]> {
    const { data } = await api.get<CampaignTemplate[]>("/automation/templates")
    return data
}

export async function getTemplate(id: string): Promise<CampaignTemplate> {
    const { data } = await api.get<CampaignTemplate>(`/automation/templates/${id}`)
    return data
}

/**
 * Clone a template into a new workflow.
 *
 * NOTE (limitation): the backend `POST /automation/templates/{id}/instantiate`
 * endpoint is broken (TPL-01/02 — `create_draft` signature mismatch + never persists
 * a version). We therefore clone via the working create endpoint: fetch the template
 * definition and `POST /automation/workflows`. When instantiate is fixed this can call
 * it directly with no consumer change.
 */
export async function createWorkflowFromTemplate(
    templateId: string,
    name?: string,
): Promise<AutomationWorkflow> {
    const template = await getTemplate(templateId)
    return createWorkflow({
        name: name?.trim() || template.name,
        definition: template.definition,
    })
}
