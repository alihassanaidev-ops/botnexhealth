import api from "@/lib/api"
import type {
    InstitutionDetail,
    SmsLocation,
    SmsSuppression,
    TwilioPhoneNumber,
    SendSmsRequest,
    SendSmsResponse,
} from "@/types"
import type { AuditLogPaginatedResponse } from "./tenant-api"

export async function listInstitutionsDetailed(): Promise<InstitutionDetail[]> {
    const { data } = await api.get<InstitutionDetail[]>("/admin/institutions")
    return data
}

export async function verifyRetellAgent(agentId: string): Promise<unknown> {
    const { data } = await api.get(`/admin/institutions/retell/agents/${agentId}`)
    return data
}

export async function listTwilioPhoneNumbers(): Promise<TwilioPhoneNumber[]> {
    const { data } = await api.get<TwilioPhoneNumber[]>("/admin/twilio/phone-numbers")
    return data
}

export async function sendSms(payload: SendSmsRequest): Promise<SendSmsResponse> {
    const { data } = await api.post<SendSmsResponse>("/admin/twilio/send-sms", payload)
    return data
}

export async function listSmsLocations(): Promise<SmsLocation[]> {
    const { data } = await api.get<SmsLocation[]>("/admin/sms/locations")
    return data
}

export async function listSmsSuppressions(): Promise<SmsSuppression[]> {
    const { data } = await api.get<SmsSuppression[]>("/admin/sms/suppressions")
    return data
}

export async function createSmsSuppression(payload: {
    location_id: string
    phone: string
    reason?: string
}): Promise<SmsSuppression> {
    const { data } = await api.post<SmsSuppression>("/admin/sms/suppressions", payload)
    return data
}

export async function releaseSmsSuppression(id: string): Promise<SmsSuppression> {
    const { data } = await api.post<SmsSuppression>(`/admin/sms/suppressions/${id}/release`)
    return data
}

export async function listAdminAuditLogs(
    page: number = 1,
    size: number = 50,
    institutionId?: string
): Promise<AuditLogPaginatedResponse> {
    const params = new URLSearchParams({
        page: page.toString(),
        size: size.toString()
    })

    if (institutionId) {
        params.append("institution_id", institutionId)
    }

    const { data } = await api.get<AuditLogPaginatedResponse>(`/admin/institutions/audit-logs?${params.toString()}`)
    return data
}

// =============================================================================
// User management
// =============================================================================

export type AdminUserStatus = "active" | "pending" | "deleted" | "all"

export interface AdminUserRow {
    id: string
    email: string
    role: string
    is_active: boolean
    invite_status: string
    deleted_at: string | null
    institution_id: string | null
    institution_name: string | null
    institution_slug: string | null
    location_id: string | null
    location_name: string | null
    location_slug: string | null
}

export interface AdminUserListResponse {
    items: AdminUserRow[]
    total: number
    page: number
    size: number
    pages: number
}

export interface AdminUserFilters {
    q?: string
    role?: string
    institution_id?: string
    location_id?: string
    status?: AdminUserStatus
}

export async function listAdminUsers(
    filters: AdminUserFilters = {},
    page: number = 1,
    size: number = 50
): Promise<AdminUserListResponse> {
    const params = new URLSearchParams({
        page: page.toString(),
        size: size.toString(),
    })
    if (filters.q) params.append("q", filters.q)
    if (filters.role) params.append("role", filters.role)
    if (filters.institution_id) params.append("institution_id", filters.institution_id)
    if (filters.location_id) params.append("location_id", filters.location_id)
    if (filters.status) params.append("status", filters.status)

    const { data } = await api.get<AdminUserListResponse>(`/admin/users?${params.toString()}`)
    return data
}

export async function removeAdminUser(userId: string): Promise<void> {
    await api.delete(`/admin/users/${userId}`)
}

export async function reinviteAdminUser(userId: string): Promise<void> {
    await api.post(`/admin/users/${userId}/reinvite`)
}
