import api from "@/lib/api"
import type { OperatingHoursEntry, OperatingHoursResponse } from "@/types"

export interface InstitutionPortalMe {
    id: string
    name: string
    slug: string
    role: string
    institution_id: string | null
    location_id: string | null
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

export interface AggregateSummaryCards {
    total_calls_today: number
    total_calls_week: number
    total_calls_month: number
    total_calls_all_time: number
    appointments_booked_month: number
    new_patients_month: number
    booking_rate_month: number
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

export async function getAggregateDashboard(): Promise<AggregateDashboardResponse> {
    const { data } = await api.get<AggregateDashboardResponse>("/institution/dashboard/aggregate")
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
    role: "INSTITUTION_ADMIN" | "LOCATION_ADMIN"
    location_slug?: string
}): Promise<void> {
    await api.post("/institution/users/invite", payload)
}

export async function inviteStaff(locSlug: string, email: string): Promise<void> {
    await api.post(`/institution/locations/${locSlug}/invite-staff`, { email })
}

export async function deactivateInstitutionUser(userId: string): Promise<void> {
    await api.post(`/institution/users/${userId}/deactivate`)
}

export async function reinviteInstitutionUser(userId: string): Promise<void> {
    await api.post(`/institution/users/${userId}/reinvite`)
}
