import api from "@/lib/api"
import type { AutomationWorkflow, AutomationWorkflowRun } from "@/types"

export async function listCampaigns(): Promise<AutomationWorkflow[]> {
    const { data } = await api.get<AutomationWorkflow[]>("/automation/workflows")
    return data
}

export async function getCampaign(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.get<AutomationWorkflow>(`/automation/workflows/${id}`)
    return data
}

export async function pauseCampaign(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/pause`)
    return data
}

export async function resumeCampaign(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/resume`)
    return data
}

export async function archiveCampaign(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/archive`)
    return data
}

export async function listCampaignRuns(
    workflowId: string,
    limit = 50,
): Promise<AutomationWorkflowRun[]> {
    const { data } = await api.get<AutomationWorkflowRun[]>(
        `/automation/workflows/${workflowId}/runs?limit=${limit}`,
    )
    return data
}
