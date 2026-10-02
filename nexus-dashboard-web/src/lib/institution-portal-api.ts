import api from "@/lib/api"
import type {
    BreakCreateRequest,
    BreakResponse,
    OperatingHoursEntry,
    OperatingHoursResponse,
} from "@/types"

export interface InstitutionPortalMe {
    id: string
    name: string
    slug: string
    role: string
    institution_id: string | null
    location_id: string | null
    // PMS integration mode. has_pms === false for call-intelligence-only
    // tenants — the UI hides Practice Setup and surfaces the Patients page.
    pms_type?: string
    has_pms?: boolean
}

export interface InstitutionPortalLocation {
    id: string
    institution_id: string
    name: string
    slug: string
    is_active: boolean
    phone: string | null
    timezone: string | null
}

export interface TransferNumber {
    id: string
    location_id: string
    location_slug: string
    location_name: string
    phone_number: string
    department: string
}

export interface AggregateSummaryCards {
    total_calls_today: number
    total_calls_week: number
    total_calls_month: number
    total_calls_all_time: number
    appointments_booked_month: number
    new_patients_month: number
    booking_rate_month: number
    avg_call_duration_seconds: number
    open_callbacks: number
}

export interface AggregateTagCount {
    tag: string
    label: string
    count: number
}

export interface ClinicComparisonRow {
    location_id: string
    location_name: string
    location_slug: string
    status: string
    calls_today: number
    calls_this_month: number
    appointments_booked_month: number
    new_patients_month: number
    booking_rate_month: number
    avg_call_duration_seconds: number
    open_callbacks: number
}

export interface AggregateDashboardResponse {
    summary: AggregateSummaryCards
    tag_distribution: AggregateTagCount[]
    clinic_comparison: ClinicComparisonRow[]
    as_of: string
}

export interface InstitutionUserRow {
    id: string
    email: string
    role: "INSTITUTION_ADMIN" | "LOCATION_ADMIN" | "STAFF"
    is_active: boolean
    invite_status: "PENDING" | "ACCEPTED"
    institution_id: string | null
    location_id: string | null
    location_name: string | null
}

export async function getInstitutionPortalMe(): Promise<InstitutionPortalMe> {
    const { data } = await api.get<InstitutionPortalMe>("/institution/me")
    return data
}

export async function listInstitutionPortalLocations(): Promise<InstitutionPortalLocation[]> {
    const { data } = await api.get<InstitutionPortalLocation[]>("/institution/locations")
    return data
}

export async function updateLocationTimezone(
    locSlug: string,
    timezone: string,
): Promise<InstitutionPortalLocation> {
    const { data } = await api.patch<InstitutionPortalLocation>(
        `/institution/locations/${locSlug}/timezone`,
        { timezone },
    )
    return data
}

export async function listTransferNumbers(): Promise<TransferNumber[]> {
    const { data } = await api.get<TransferNumber[]>("/institution/transfer-numbers")
    return data
}

export async function createTransferNumber(
    locSlug: string,
    payload: { phone_number: string; department: string },
): Promise<TransferNumber> {
    const { data } = await api.post<TransferNumber>(
        `/institution/locations/${locSlug}/transfer-numbers`,
        payload,
    )
    return data
}

export async function updateTransferNumber(
    locSlug: string,
    transferId: string,
    payload: { phone_number: string; department: string },
): Promise<TransferNumber> {
    const { data } = await api.patch<TransferNumber>(
        `/institution/locations/${locSlug}/transfer-numbers/${transferId}`,
        payload,
    )
    return data
}

export async function deleteTransferNumber(
    locSlug: string,
    transferId: string,
): Promise<void> {
    await api.delete(`/institution/locations/${locSlug}/transfer-numbers/${transferId}`)
}

export async function getAggregateDashboard(
    range?: { startDate?: string; endDate?: string },
): Promise<AggregateDashboardResponse> {
    const params = new URLSearchParams()
    if (range?.startDate) params.set("start_date", range.startDate)
    if (range?.endDate) params.set("end_date", range.endDate)
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<AggregateDashboardResponse>(`/institution/dashboard/aggregate${q}`)
    return data
}

export async function getLocationOperatingHours(locSlug: string): Promise<OperatingHoursResponse[]> {
    const { data } = await api.get<OperatingHoursResponse[]>(`/institution/locations/${locSlug}/operating-hours`)
    return data
}

export async function updateLocationOperatingHours(
    locSlug: string,
    hours: OperatingHoursEntry[],
): Promise<OperatingHoursResponse[]> {
    const { data } = await api.put<OperatingHoursResponse[]>(
        `/institution/locations/${locSlug}/operating-hours`,
        { hours },
    )
    return data
}

export async function getLocationBreaks(locSlug: string): Promise<BreakResponse[]> {
    const { data } = await api.get<BreakResponse[]>(`/institution/locations/${locSlug}/breaks`)
    return data
}

export async function createLocationBreak(
    locSlug: string,
    body: BreakCreateRequest,
): Promise<BreakResponse> {
    const { data } = await api.post<BreakResponse>(
        `/institution/locations/${locSlug}/breaks`,
        body,
    )
    return data
}

export async function deleteLocationBreak(locSlug: string, breakId: string): Promise<void> {
    await api.delete(`/institution/locations/${locSlug}/breaks/${breakId}`)
}

export async function inviteInstitutionAdmin(email: string): Promise<void> {
    await api.post("/institution/users/invite-institution-admin", { email })
}

export async function inviteLocationAdmin(locSlug: string, email: string): Promise<void> {
    await api.post(`/institution/locations/${locSlug}/invite-location-admin`, { email })
}

export async function listInstitutionUsers(): Promise<InstitutionUserRow[]> {
    const { data } = await api.get<InstitutionUserRow[]>("/institution/users")
    return data
}

export async function inviteInstitutionUser(payload: {
    email: string
    role: "INSTITUTION_ADMIN" | "LOCATION_ADMIN" | "STAFF"
    location_slug?: string
}): Promise<void> {
    await api.post("/institution/users/invite", payload)
}

export async function inviteStaff(locSlug: string, email: string): Promise<void> {
    await api.post(`/institution/locations/${locSlug}/invite-staff`, { email })
}

// Billing Email

export interface BillingEmailResponse {
    billing_email: string | null
}

export async function getBillingEmail(): Promise<BillingEmailResponse> {
    const { data } = await api.get<BillingEmailResponse>("/institution/billing-email")
    return data
}

export async function updateBillingEmail(billing_email: string): Promise<BillingEmailResponse> {
    const { data } = await api.put<BillingEmailResponse>("/institution/billing-email", { billing_email })
    return data
}

// ROI Configuration & Calculation

export type SubscriptionBillingMode = "institution" | "location"

export interface ROIConfig {
    avg_appointment_value: number
    avg_new_patient_value: number
    /** The whole-group price. Ignored when billing per location. */
    monthly_subscription_cost: number
    staff_hourly_rate: number
    avg_call_duration_minutes: number
    subscription_billing_mode: SubscriptionBillingMode
}

export interface ROICalculation {
    /** Null when the totals were summed from per-location figures. */
    config: ROIConfig | null
    total_calls_month: number
    appointments_booked_month: number
    new_patients_month: number
    revenue_from_bookings: number
    revenue_from_new_patients: number
    total_revenue_generated: number
    staff_time_saved_hours: number
    /** Null when no hourly rate is configured — unknown, not zero. */
    staff_cost_saved: number | null
    total_value: number
    monthly_cost: number
    net_value: number
    /** Null when there is no cost to measure a return against. */
    roi_percentage: number | null
    revenue_basis: string
}

export async function getROIConfig(): Promise<ROIConfig | null> {
    const { data } = await api.get<ROIConfig | null>("/institution/roi/config")
    return data
}

export async function updateROIConfig(config: ROIConfig): Promise<ROIConfig> {
    const { data } = await api.put<ROIConfig>("/institution/roi/config", config)
    return data
}

export interface ROIWindow {
    startDate?: string
    endDate?: string
}

function roiParams(window?: ROIWindow): string {
    const params = new URLSearchParams()
    if (window?.startDate) params.set("start_date", window.startDate)
    if (window?.endDate) params.set("end_date", window.endDate)
    const qs = params.toString()
    return qs ? `?${qs}` : ""
}

export async function calculateROI(window?: ROIWindow): Promise<ROICalculation> {
    const { data } = await api.get<ROICalculation>(
        `/institution/roi/calculate${roiParams(window)}`,
    )
    return data
}

// Per-location ROI. Subscription cost is charged either per institution or per
// location; the institution's subscription_billing_mode decides which, and the
// calculation says which one produced the figure it returns.

export interface LocationROIConfig {
    location_id: string
    location_slug: string
    avg_appointment_value: number
    avg_new_patient_value: number
    /** Null when the clinic does not track a front desk rate. */
    staff_hourly_rate: number | null
    avg_call_duration_minutes: number
    /** This clinic's own price. Null when unset; never inherited. */
    monthly_subscription_cost: number | null
    subscription_billing_mode: SubscriptionBillingMode
    /** "location" when set here, "institution" when inherited. */
    source: "location" | "institution"
}

export type LocationROIConfigInput = Pick<
    LocationROIConfig,
    | "avg_appointment_value"
    | "avg_new_patient_value"
    | "staff_hourly_rate"
    | "avg_call_duration_minutes"
    | "monthly_subscription_cost"
>

export interface LocationROICalculation {
    config: LocationROIConfig
    total_calls_month: number
    appointments_booked_month: number
    new_patients_month: number
    revenue_from_bookings: number
    revenue_from_new_patients: number
    total_revenue_generated: number
    staff_time_saved_hours: number
    /** Null when no hourly rate is configured — unknown, not zero. */
    staff_cost_saved: number | null
    period_start: string
    period_end: string
    total_value: number
    monthly_cost_allocated: number
    cost_allocation_basis: string
    net_value: number
    /** Null when there is no cost to measure a return against. */
    roi_percentage: number | null
}

export async function getLocationROIConfig(
    locSlug: string,
): Promise<LocationROIConfig | null> {
    const { data } = await api.get<LocationROIConfig | null>(
        `/institution/locations/${encodeURIComponent(locSlug)}/roi/config`,
    )
    return data
}

export async function updateLocationROIConfig(
    locSlug: string,
    config: LocationROIConfigInput,
): Promise<LocationROIConfig> {
    const { data } = await api.put<LocationROIConfig>(
        `/institution/locations/${encodeURIComponent(locSlug)}/roi/config`,
        config,
    )
    return data
}

export async function clearLocationROIConfig(locSlug: string): Promise<void> {
    await api.delete(
        `/institution/locations/${encodeURIComponent(locSlug)}/roi/config`,
    )
}

export async function calculateLocationROI(
    locSlug: string,
    window?: ROIWindow,
): Promise<LocationROICalculation> {
    const { data } = await api.get<LocationROICalculation>(
        `/institution/locations/${encodeURIComponent(locSlug)}/roi/calculate${roiParams(window)}`,
    )
    return data
}

// Insurance Plans

export interface InsurancePlan {
    id: string
    location_id: string
    name: string
    description: string | null
    is_active: boolean
}

export async function listInsurancePlans(locSlug: string): Promise<InsurancePlan[]> {
    const { data } = await api.get<InsurancePlan[]>(`/institution/locations/${locSlug}/insurance-plans`)
    return data
}

export async function createInsurancePlan(
    locSlug: string,
    payload: { name: string; description?: string },
): Promise<InsurancePlan> {
    const { data } = await api.post<InsurancePlan>(
        `/institution/locations/${locSlug}/insurance-plans`,
        payload,
    )
    return data
}

export async function updateInsurancePlan(
    locSlug: string,
    planId: string,
    payload: { name: string; description?: string },
): Promise<InsurancePlan> {
    const { data } = await api.patch<InsurancePlan>(
        `/institution/locations/${locSlug}/insurance-plans/${planId}`,
        payload,
    )
    return data
}

export async function deleteInsurancePlan(locSlug: string, planId: string): Promise<void> {
    await api.delete(`/institution/locations/${locSlug}/insurance-plans/${planId}`)
}

export async function deactivateInstitutionUser(userId: string): Promise<void> {
    await api.post(`/institution/users/${userId}/deactivate`)
}

export async function reinviteInstitutionUser(userId: string): Promise<void> {
    await api.post(`/institution/users/${userId}/reinvite`)
}

export async function listLocationUsers(): Promise<InstitutionUserRow[]> {
    const { data } = await api.get<InstitutionUserRow[]>("/institution/location/users")
    return data
}

export async function deactivateLocationUser(userId: string): Promise<void> {
    await api.post(`/institution/location/users/${userId}/deactivate`)
}
