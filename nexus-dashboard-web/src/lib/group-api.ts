/**
 * Group (DSO oversight) API service.
 *
 * GROUP_ADMIN endpoints return read-only, aggregate, cross-institution data
 * (no PHI). Super-admin endpoints manage groups + membership.
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api"

export interface GroupMemberInfo {
    id: string
    name: string
    slug: string
    is_active: boolean
}

export interface GroupMe {
    id: string
    name: string
    slug: string
    members: GroupMemberInfo[]
}

export interface InstitutionComparisonRow {
    institution_id: string
    institution_name: string
    institution_slug: string
    status: string
    total_calls: number
    appointments_booked: number
    new_patients: number
    booking_rate: number
    avg_call_duration_seconds: number
}

export interface GroupSummaryCards {
    institution_count: number
    total_calls: number
    appointments_booked: number
    new_patients: number
    booking_rate: number
    avg_call_duration_seconds: number
}

export interface GroupTrendPoint {
    bucket: string
    label: string
    total_calls: number
    appointments_booked: number
    new_patients: number
}

export interface GroupTagCount {
    tag: string
    label: string
    count: number
}

export interface GroupDashboardResponse {
    start_date: string
    end_date: string
    summary: GroupSummaryCards
    institution_comparison: InstitutionComparisonRow[]
    trend: GroupTrendPoint[]
    tag_distribution: GroupTagCount[]
    as_of: string
}

// ── GROUP_ADMIN ────────────────────────────────────────────────────────────────

export async function getGroupMe(): Promise<GroupMe> {
    const { data } = await api.get<GroupMe>("/group/me")
    return data
}

export async function getGroupDashboard(
    range?: { startDate: string; endDate: string },
): Promise<GroupDashboardResponse> {
    const params = new URLSearchParams()
    if (range) {
        params.set("start_date", range.startDate)
        params.set("end_date", range.endDate)
    }
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<GroupDashboardResponse>(`/group/dashboard${q}`)
    return data
}

export interface GroupLocationInfo {
    id: string
    name: string
    slug: string
}

export interface LocationComparisonRow {
    location_id: string
    location_name: string
    total_calls: number
    appointments_booked: number
    new_patients: number
    booking_rate: number
    avg_call_duration_seconds: number
}

export interface InstitutionKpis {
    total_calls: number
    appointments_booked: number
    new_patients: number
    booking_rate: number
    avg_call_duration_seconds: number
}

export interface GroupInstitutionDashboardResponse {
    institution_id: string
    institution_name: string
    start_date: string
    end_date: string
    selected_location_id: string | null
    locations: GroupLocationInfo[]
    summary: InstitutionKpis
    trend: GroupTrendPoint[]
    tag_distribution: GroupTagCount[]
    location_comparison: LocationComparisonRow[]
    as_of: string
}

export async function getGroupInstitutionDashboard(
    institutionId: string,
    range?: { startDate: string; endDate: string },
    locationId?: string | null,
): Promise<GroupInstitutionDashboardResponse> {
    const params = new URLSearchParams()
    if (range) {
        params.set("start_date", range.startDate)
        params.set("end_date", range.endDate)
    }
    if (locationId) params.set("location_id", locationId)
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<GroupInstitutionDashboardResponse>(
        `/group/institution/${institutionId}/dashboard${q}`,
    )
    return data
}

// ── Super-admin group management ────────────────────────────────────────────────

export interface AdminGroup {
    id: string
    name: string
    slug: string
    is_active: boolean
    member_count: number
}

export async function listGroups(): Promise<AdminGroup[]> {
    const { data } = await api.get<AdminGroup[]>("/admin/institution-groups")
    return data
}

export async function createGroup(payload: {
    name: string
    slug: string
    email: string
}): Promise<AdminGroup> {
    const { data } = await api.post<AdminGroup>("/admin/institution-groups", payload)
    return data
}

export async function assignInstitution(groupSlug: string, instSlug: string): Promise<AdminGroup> {
    const { data } = await api.post<AdminGroup>(
        `/admin/institution-groups/${groupSlug}/institutions/${instSlug}`,
    )
    return data
}

export async function unassignInstitution(groupSlug: string, instSlug: string): Promise<AdminGroup> {
    const { data } = await api.delete<AdminGroup>(
        `/admin/institution-groups/${groupSlug}/institutions/${instSlug}`,
    )
    return data
}
