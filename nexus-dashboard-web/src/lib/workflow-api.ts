/**
 * Workflow Builder API client. Mirrors the thin `automation-api.ts` idiom
 * (import shared axios, async fn -> api.get/post -> return data, no try/catch —
 * pages handle errors). Endpoints per findings.md §3 (base already ends in `/api`).
 */
import api from "@/lib/api"
import { getAccessToken } from "@/lib/token-manager"
import type { AutomationWorkflow } from "@/types"
import type {
    ChannelReadiness,
    LaunchChecklist,
    MergeFieldCatalogItem,
    TestRunResult,
    ValidateDefinitionResponse,
    WorkflowDefinition,
    WorkflowLlmModelsResponse,
    WorkflowNodeCapabilitiesResponse,
    WorkflowVersion,
} from "@/types/workflow"

/** Response of `GET /automation/templates` (definition is the Plan-01 shape). */
export interface CampaignTemplateFrequencyCap {
    max_per_day: number
    max_per_rolling_7_days: number
}

export interface CampaignTemplateSetupField {
    id: string
    label: string
    type:
        | "location"
        | "select"
        | "text"
        | "number"
        | "string_list"
        | "appointment_type_multiselect"
        | "voice_profile_select"
        | "retell_sms_profile_select"
        | "provider_select"
    required?: boolean
    default?: string | number | string[]
    placeholder?: string
    options?: string[]
}

export interface CampaignTemplateMetadata {
    category: string
    goal: string
    outcome_labels: string[]
    supported_channels: string[]
    required_readiness_checks: string[]
    required_merge_fields: string[]
    default_compliance_content_class: string
    default_audience: string
    default_eligibility_rules: string[]
    default_frequency_cap: CampaignTemplateFrequencyCap
    default_staff_handoff_reason: string | null
    analytics_outcome_map: Record<string, string>
    sample_preview_context: Record<string, unknown>
    setup_fields: CampaignTemplateSetupField[]
    copy_variants: { id: string; label: string }[]
    pms_capability_requirements: string[]
    pms_capability_evaluation?: {
        requirements: string[]
        supported: boolean
        status: "supported" | "partial" | "unsupported" | "unknown"
        pms_name: string | null
        missing: string[]
        partial: string[]
        unknown: string[]
        details: Record<
            string,
            {
                capability: string
                status: "supported" | "partial" | "unsupported" | "unknown"
                label: string
                matched_api: string | null
                raw_value: string | null
            }
        >
        message: string
    }
}

export interface CampaignTemplate {
    id: string
    name: string
    description: string
    trigger_type: string
    definition: WorkflowDefinition
    tags: string[]
    category: string
    metadata: CampaignTemplateMetadata
}

export interface PhoneCountryRegion {
    region: string
    calling_code: string
}

/** One PMS appointment disposition, served from the backend PMS status catalog. */
export interface PmsAppointmentStatus {
    id: number
    /** Stable snake_case key — what the runtime writes as `appointment_status`. */
    key: string
    label: string
    /** PMS-neutral meaning: booked | waiting | late | cancelled | no_show | pending. */
    semantics: string
    readable: boolean
    writable: boolean
    description: string
}

export interface PmsAppointmentStatusCatalog {
    pms: string
    statuses: PmsAppointmentStatus[]
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

export async function publishWorkflow(
    id: string,
    payload?: { name?: string; definition?: WorkflowDefinition },
): Promise<AutomationWorkflow> {
    const path = `/automation/workflows/${id}/publish`
    if (!payload) {
        const { data } = await api.post<AutomationWorkflow>(path)
        return data
    }

    const baseUrl = String(api.defaults?.baseURL ?? "/api").replace(/\/$/, "")
    const token = getAccessToken()
    const response = await fetch(`${baseUrl}${path}`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
    })
    const data = await response.json().catch(() => null) as AutomationWorkflow | { detail?: string } | null
    if (!response.ok) {
        const detail = data && "detail" in data ? data.detail : null
        throw new Error(detail || `Publish failed with HTTP ${response.status}.`)
    }
    if (!data || !("id" in data)) {
        throw new Error("The publish response was not a workflow.")
    }
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

export async function deleteWorkflow(id: string): Promise<void> {
    await api.delete(`/automation/workflows/${id}`)
}

// ---- Versions / validation / merge-field catalog ----

/** List every published version, newest-first (`GET .../{id}/versions`). */
export async function listVersions(workflowId: string): Promise<WorkflowVersion[]> {
    const { data } = await api.get<WorkflowVersion[]>(
        `/automation/workflows/${workflowId}/versions`,
    )
    return data
}

/**
 * Validate a definition against the authoritative backend schema without
 * persisting (`POST /automation/workflows/validate`). Returns errors AND
 * warnings (consent/content-class + structural + reachability).
 */
export async function validateDefinition(
    definition: WorkflowDefinition,
    locationId?: string | null,
): Promise<ValidateDefinitionResponse> {
    const { data } = await api.post<ValidateDefinitionResponse>(
        "/automation/workflows/validate",
        { definition, location_id: locationId ?? null },
    )
    return data
}

export async function listNodeCapabilities(): Promise<WorkflowNodeCapabilitiesResponse> {
    const { data } = await api.get<WorkflowNodeCapabilitiesResponse>(
        "/automation/workflows/node-capabilities",
    )
    return data
}

export async function listPhoneCountryRegions(): Promise<PhoneCountryRegion[]> {
    const { data } = await api.get<PhoneCountryRegion[]>("/automation/workflows/phone-country-regions")
    return data
}

/** How well a practice-management system can supply an event or a field. */
export type PmsSupport = "native" | "derived" | "unsupported"

export interface EventContextField {
    path: string
    label: string
    type: string
    description: string
    sample: unknown
    pms_support: Record<string, PmsSupport>
    phi_level: "none" | "low" | "medium" | "high"
    pms_specific: boolean
}

export interface EventCatalogEntry {
    key: string
    label: string
    description: string
    pms_support: Record<string, PmsSupport>
    context: EventContextField[]
}

interface EventCatalogPayload {
    pms: string
    events: EventCatalogEntry[]
}

/**
 * The canonical event vocabulary the builder authors against.
 *
 * Served rather than hardcoded so the picker can only ever offer events the
 * caller's practice software actually raises — the endpoint drops the
 * unsupported ones. Omit `pms` to let the backend use the caller's own.
 */
export async function listEventCatalog(pms?: string): Promise<EventCatalogEntry[]> {
    const { data } = await api.get<EventCatalogPayload>(
        "/automation/workflows/event-catalog",
        pms ? { params: { pms } } : undefined,
    )
    return data.events
}

/**
 * PMS appointment disposition catalog. Served rather than hardcoded so labels,
 * semantics and writability live in one place (`src/app/pms/gotracker/statuses.py`).
 */
export async function listPmsAppointmentStatuses(
    pms = "gotracker",
): Promise<PmsAppointmentStatus[]> {
    const { data } = await api.get<PmsAppointmentStatusCatalog>(
        "/automation/workflows/pms-appointment-statuses",
        { params: { pms } },
    )
    return data.statuses
}

/**
 * Server-side dry-run of a definition (`POST /automation/workflows/dry-run`). This is
 * the authoritative simulation — it walks the definition on the backend WITHOUT
 * dispatching anything and returns the ordered steps, final outcome, and a truncated
 * flag. `conditionChoices` (nodeId -> take-true) explores specific branches.
 */
export async function dryRun(
    definition: WorkflowDefinition,
    opts?: {
        context?: Record<string, unknown>
        conditionChoices?: Record<string, boolean>
    },
): Promise<TestRunResult> {
    const body: Record<string, unknown> = {
        definition,
        condition_choices: opts?.conditionChoices ?? {},
    }
    if (opts?.context) body.context = opts.context
    const { data } = await api.post<TestRunResult>("/automation/workflows/dry-run", body)
    return data
}

/**
 * Report whether SMS / email / voice are provisioned for a location
 * (`GET /automation/workflows/channel-readiness?location_id=...`). Advisory only:
 * an unready channel the workflow uses warns at publish but does not block it.
 */
export async function getChannelReadiness(locationId: string): Promise<ChannelReadiness> {
    const { data } = await api.get<ChannelReadiness>(
        `/automation/workflows/channel-readiness?location_id=${encodeURIComponent(locationId)}`,
    )
    return data
}

export async function getLaunchChecklist(
    workflowId: string,
    opts?: { locationId?: string | null },
): Promise<LaunchChecklist> {
    const params = new URLSearchParams()
    if (opts?.locationId) params.set("location_id", opts.locationId)
    const query = params.toString()
    const { data } = await api.get<LaunchChecklist>(
        `/automation/workflows/${workflowId}/launch-checklist${query ? `?${query}` : ""}`,
    )
    return data
}

export async function previewLaunchChecklist(
    workflowId: string,
    definition: WorkflowDefinition,
    opts?: { locationId?: string | null },
): Promise<LaunchChecklist> {
    const { data } = await api.post<LaunchChecklist>(
        `/automation/workflows/${workflowId}/launch-checklist/preview`,
        {
            definition,
            location_id: opts?.locationId ?? null,
        },
    )
    return data
}

/** The catalog of merge tokens the engine can resolve (`GET .../merge-fields`). */
export async function listMergeFields(opts?: {
    triggerType?: string
    channel?: "sms" | "email" | "voice"
    includeUnavailable?: boolean
}): Promise<MergeFieldCatalogItem[]> {
    const params = new URLSearchParams()
    if (opts?.triggerType) params.set("trigger_type", opts.triggerType)
    if (opts?.channel) params.set("channel", opts.channel)
    if (opts?.includeUnavailable) params.set("include_unavailable", "true")
    const query = params.toString()
    const { data } = await api.get<MergeFieldCatalogItem[]>(
        `/automation/workflows/merge-fields${query ? `?${query}` : ""}`,
    )
    return data
}

/** OpenAI model choices for workflow LLM nodes (`GET .../llm-models`). */
export async function listWorkflowLlmModels(): Promise<WorkflowLlmModelsResponse> {
    const { data } = await api.get<WorkflowLlmModelsResponse>("/automation/workflows/llm-models")
    return data
}

// ---- Templates ----
export async function listTemplates(locationId?: string | null): Promise<CampaignTemplate[]> {
    const params = new URLSearchParams()
    if (locationId) params.set("location_id", locationId)
    const query = params.toString()
    const { data } = await api.get<CampaignTemplate[]>(
        `/automation/templates${query ? `?${query}` : ""}`,
    )
    return data
}

export async function getTemplate(id: string): Promise<CampaignTemplate> {
    const { data } = await api.get<CampaignTemplate>(`/automation/templates/${id}`)
    return data
}

/** Clone a template into a new workflow through the guided instantiate endpoint. */
export async function createWorkflowFromTemplate(
    templateId: string,
    name?: string,
    setup?: {
        locationId?: string | null
        voiceProfileId?: string | null
        voiceAgentId?: string | null
        setupOptions?: Record<string, unknown>
    },
): Promise<AutomationWorkflow> {
    const body: Record<string, unknown> = {}
    if (name?.trim()) body.name = name.trim()
    if (setup?.locationId) body.location_id = setup.locationId
    if (setup?.voiceProfileId?.trim()) body.voice_profile_id = setup.voiceProfileId.trim()
    if (setup?.voiceAgentId?.trim()) body.voice_agent_id = setup.voiceAgentId.trim()
    if (setup?.setupOptions) body.setup_options = setup.setupOptions
    const { data } = await api.post<AutomationWorkflow>(
        `/automation/templates/${templateId}/instantiate`,
        body,
    )
    return data
}
