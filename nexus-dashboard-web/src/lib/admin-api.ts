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
