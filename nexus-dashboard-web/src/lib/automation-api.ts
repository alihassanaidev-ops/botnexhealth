import api from "@/lib/api"
import type {
    AutomationWorkflow,
    AutomationWorkflowRun,
    CampaignUsageReport,
    OutboundHaltStatus,
    UsageSummary,
    WorkflowHaltResult,
} from "@/types"

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

export async function enrollContactInCampaign(
    workflowId: string,
    contactId: string,
): Promise<AutomationWorkflowRun> {
    const idempotencyKey = `manual:${workflowId}:${contactId}:${Date.now()}`
    const { data } = await api.post<AutomationWorkflowRun>(
        `/automation/workflows/${workflowId}/enroll`,
        {
            contact_id: contactId,
            idempotency_key: idempotencyKey,
            trigger_metadata: { source: "manual_ui" },
        },
    )
    return data
}

export async function cancelCampaignRun(
    workflowId: string,
    runId: string,
): Promise<AutomationWorkflowRun> {
    const { data } = await api.post<AutomationWorkflowRun>(
        `/automation/workflows/${workflowId}/runs/${runId}/cancel`,
    )
    return data
}

function rangeQuery(range?: { startDate?: string; endDate?: string }): string {
    const params = new URLSearchParams()
    if (range?.startDate) params.set("start_date", range.startDate)
    if (range?.endDate) params.set("end_date", range.endDate)
    const query = params.toString()
    return query ? `?${query}` : ""
}

export async function getUsageSummary(
    range?: { startDate?: string; endDate?: string },
): Promise<UsageSummary> {
    const { data } = await api.get<UsageSummary>(
        `/institution/usage/summary${rangeQuery(range)}`,
    )
    return data
}

export async function getUsageByCampaign(
    range?: { startDate?: string; endDate?: string },
    limit = 50,
): Promise<CampaignUsageReport> {
    const params = new URLSearchParams()
    if (range?.startDate) params.set("start_date", range.startDate)
    if (range?.endDate) params.set("end_date", range.endDate)
    params.set("limit", String(limit))
    const { data } = await api.get<CampaignUsageReport>(
        `/institution/usage/by-campaign?${params.toString()}`,
    )
    return data
}

export async function getOutboundHaltStatus(): Promise<OutboundHaltStatus> {
    const { data } = await api.get<OutboundHaltStatus>("/automation/workflows/outbound-halt")
    return data
}

export async function activateOutboundHalt(reason?: string): Promise<OutboundHaltStatus> {
    const { data } = await api.post<OutboundHaltStatus>("/automation/workflows/outbound-halt", {
        reason: reason?.trim() || null,
    })
    return data
}

export async function releaseOutboundHalt(): Promise<OutboundHaltStatus> {
    const { data } = await api.delete<OutboundHaltStatus>("/automation/workflows/outbound-halt")
    return data
}

export async function emergencyHaltCampaign(
    workflowId: string,
    reason?: string,
): Promise<WorkflowHaltResult> {
    const { data } = await api.post<WorkflowHaltResult>(
        `/automation/workflows/${workflowId}/emergency-halt`,
        { reason: reason?.trim() || null },
    )
    return data
}
