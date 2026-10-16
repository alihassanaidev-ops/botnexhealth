import api from "@/lib/api"
import type {
    AutomationWorkflow,
    CampaignAnalytics,
    CampaignRevenue,
    CampaignSplitAnalytics,
    AutomationWorkflowRun,
    CampaignAudienceDefinition,
    CampaignAudienceEnrollResult,
    CampaignAudienceExclusions,
    CampaignAudienceFilters,
    CampaignAudiencePreview,
    CampaignOperations,
    CampaignOverview,
    CampaignRunFilters,
    CampaignRunList,
    CampaignUsageReport,
    OutboundHaltStatus,
    RunTimeline,
    UsageSummary,
    WorkflowHaltResult,
} from "@/types"

export async function listCampaigns(locationId: string): Promise<AutomationWorkflow[]> {
    const { data } = await api.get<AutomationWorkflow[]>("/automation/workflows", {
        params: { location_id: locationId },
    })
    return data
}

export async function getCampaign(
    id: string,
    locationId?: string,
): Promise<AutomationWorkflow> {
    const path = `/automation/workflows/${id}`
    const { data } = locationId
        ? await api.get<AutomationWorkflow>(path, { params: { location_id: locationId } })
        : await api.get<AutomationWorkflow>(path)
    return data
}

export async function createDraftCampaign(
    name: string,
    locationId?: string,
): Promise<AutomationWorkflow> {
    const body: { name: string; location_id?: string } = { name }
    if (locationId) body.location_id = locationId
    const { data } = await api.post<AutomationWorkflow>("/automation/workflows/draft", body)
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

/**
 * The server's reason a campaign action failed, e.g. the launch-checklist
 * blockers that stop a resume, or null when it sent none.
 */
export function campaignActionError(error: unknown): string | null {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    if (typeof detail === "string") return detail || null
    if (detail && typeof detail === "object") {
        const { message, blocker_messages } = detail as {
            message?: string
            blocker_messages?: string[]
        }
        if (blocker_messages?.length) return blocker_messages.join(" ")
        if (message) return message
    }
    return null
}

/** Whether a campaign pulls its patients from the PMS recall list. */
export function isRecallCampaign(workflow: AutomationWorkflow): boolean {
    const definition = workflow.definition as
        | { trigger?: { source?: { kind?: string } }; triggers?: { source?: { kind?: string } }[] }
        | null
    const triggers = [definition?.trigger, ...(definition?.triggers ?? [])]
    return triggers.some((trigger) => trigger?.source?.kind === "pms_recall")
}

export interface RecallPreviewPatient {
    patient_id: string
    patient_name: string | null
    recall_type_name: string | null
    recall_due_date: string | null
}

export interface RecallPreview {
    max_enrollments_per_run: number
    would_call: RecallPreviewPatient[]
    counts: Record<string, number>
}

/** Who the recall campaign's next scheduled run would call. Places no calls. */
export async function previewRecallCampaign(id: string): Promise<RecallPreview> {
    const { data } = await api.post<RecallPreview>(`/automation/workflows/${id}/recall/preview`)
    return data
}

export async function archiveCampaign(id: string): Promise<AutomationWorkflow> {
    const { data } = await api.post<AutomationWorkflow>(`/automation/workflows/${id}/archive`)
    return data
}

export async function deleteCampaign(id: string): Promise<void> {
    await api.delete(`/automation/workflows/${id}`)
}

export async function listCampaignRuns(
    workflowId: string,
    filters: CampaignRunFilters = {},
): Promise<CampaignRunList> {
    const params = new URLSearchParams()
    params.set("limit", String(filters.limit ?? 50))
    if (filters.status) params.set("status", filters.status)
    if (filters.outcome) params.set("outcome", filters.outcome)
    if (filters.current_node) params.set("current_node", filters.current_node)
    if (filters.next_due_from) params.set("next_due_from", filters.next_due_from)
    if (filters.next_due_to) params.set("next_due_to", filters.next_due_to)
    if (filters.channel) params.set("channel", filters.channel)
    if (filters.failure_reason) params.set("failure_reason", filters.failure_reason)
    if (filters.contact_search) params.set("contact_search", filters.contact_search)
    if (filters.cursor) params.set("cursor", filters.cursor)
    const { data } = await api.get<CampaignRunList>(
        `/automation/workflows/${workflowId}/runs?${params.toString()}`,
    )
    return data
}

export async function getCampaignOverview(workflowId: string): Promise<CampaignOverview> {
    const { data } = await api.get<CampaignOverview>(
        `/automation/workflows/${workflowId}/overview`,
    )
    return data
}

export interface CampaignAnalyticsRollup {
    start_date: string
    end_date: string
    campaigns: CampaignAnalytics[]
    totals: CampaignRevenue | null
}

export async function getCampaignRevenueRollup(
    locationId: string | null | undefined,
    range?: { startDate?: string; endDate?: string },
): Promise<CampaignAnalyticsRollup> {
    const params = new URLSearchParams()
    if (range?.startDate) params.set("start_date", range.startDate)
    if (range?.endDate) params.set("end_date", range.endDate)
    if (locationId) params.set("location_id", locationId)
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<CampaignAnalyticsRollup>(
        `/automation/campaign-analytics${q}`,
    )
    return data
}

export async function getCampaignAnalytics(
    workflowId: string,
    range?: { startDate?: string; endDate?: string },
): Promise<CampaignAnalytics> {
    const { data } = await api.get<CampaignAnalytics>(
        `/automation/workflows/${workflowId}/analytics${rangeQuery(range)}`,
    )
    return data
}

export async function getCampaignSplitAnalytics(
    workflowId: string,
    range?: { startDate?: string; endDate?: string },
): Promise<CampaignSplitAnalytics> {
    const { data } = await api.get<CampaignSplitAnalytics>(
        `/automation/workflows/${workflowId}/analytics/splits${rangeQuery(range)}`,
    )
    return data
}

export async function getRunTimeline(
    workflowId: string,
    runId: string,
): Promise<RunTimeline> {
    const { data } = await api.get<RunTimeline>(
        `/automation/workflows/${workflowId}/runs/${runId}/timeline`,
    )
    return data
}

export async function getCampaignOperations(workflowId: string): Promise<CampaignOperations> {
    const { data } = await api.get<CampaignOperations>(
        `/automation/workflows/${workflowId}/operations`,
    )
    return data
}

export async function getCampaignAudience(
    workflowId: string,
): Promise<CampaignAudienceDefinition> {
    const { data } = await api.get<CampaignAudienceDefinition>(
        `/automation/workflows/${workflowId}/audience`,
    )
    return data
}

export async function saveCampaignAudience(
    workflowId: string,
    payload: {
        filters: CampaignAudienceFilters
        exclusions: CampaignAudienceExclusions
    },
): Promise<CampaignAudienceDefinition> {
    const { data } = await api.put<CampaignAudienceDefinition>(
        `/automation/workflows/${workflowId}/audience`,
        payload,
    )
    return data
}

export async function previewCampaignAudience(
    workflowId: string,
    payload: {
        filters: CampaignAudienceFilters
        exclusions: CampaignAudienceExclusions
        sample_limit?: number
    },
): Promise<CampaignAudiencePreview> {
    const { data } = await api.post<CampaignAudiencePreview>(
        `/automation/workflows/${workflowId}/audience/preview`,
        payload,
    )
    return data
}

export async function enrollCampaignAudience(
    workflowId: string,
    payload: {
        preview_id?: string | null
        filters?: CampaignAudienceFilters
        exclusions?: CampaignAudienceExclusions
        max_enrollments?: number
    },
): Promise<CampaignAudienceEnrollResult> {
    const { data } = await api.post<CampaignAudienceEnrollResult>(
        `/automation/workflows/${workflowId}/audience/enroll`,
        payload,
    )
    return data
}

export async function enrollContactInCampaign(
    workflowId: string,
    contactId: string,
    locationId?: string | null,
): Promise<AutomationWorkflowRun> {
    const idempotencyKey = `manual:${workflowId}:${contactId}:${Date.now()}`
    const { data } = await api.post<AutomationWorkflowRun>(
        `/automation/workflows/${workflowId}/enroll`,
        {
            contact_id: contactId,
            location_id: locationId ?? null,
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
    opts?: { workflowId?: string },
): Promise<CampaignUsageReport> {
    const params = new URLSearchParams()
    if (range?.startDate) params.set("start_date", range.startDate)
    if (range?.endDate) params.set("end_date", range.endDate)
    params.set("limit", String(limit))
    if (opts?.workflowId) params.set("workflow_id", opts.workflowId)
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
