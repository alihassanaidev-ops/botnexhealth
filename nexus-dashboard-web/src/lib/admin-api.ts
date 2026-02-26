import api from "@/lib/api"
import type { TenantDetail, TwilioPhoneNumber, SendSmsRequest, SendSmsResponse } from "@/types"

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
