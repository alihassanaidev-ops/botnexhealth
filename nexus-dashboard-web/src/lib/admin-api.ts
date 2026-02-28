import api from "@/lib/api"
import type { TenantDetail, TwilioPhoneNumber, SendSmsRequest, SendSmsResponse } from "@/types"
import type { AuditLogPaginatedResponse } from "./tenant-api"

export async function listTenantsDetailed(): Promise<TenantDetail[]> {
    const { data } = await api.get<TenantDetail[]>("/admin/tenants")
    return data
}

export async function verifyRetellAgent(agentId: string): Promise<any> {
    const { data } = await api.get(`/admin/tenants/retell/agents/${agentId}`)
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

export async function listAdminAuditLogs(
    page: number = 1,
    size: number = 50,
    tenantId?: string
): Promise<AuditLogPaginatedResponse> {
    const params = new URLSearchParams({
        page: page.toString(),
        size: size.toString()
    })

    if (tenantId) {
        params.append("tenant_id", tenantId)
    }

    const { data } = await api.get<AuditLogPaginatedResponse>(`/admin/tenants/audit-logs?${params.toString()}`)
    return data
}
