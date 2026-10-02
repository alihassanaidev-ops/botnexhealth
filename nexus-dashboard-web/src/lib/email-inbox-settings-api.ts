import api from "@/lib/api"

const BASE = "/institution/email-inbox-settings"

export interface EmailInboxSettings {
    institution_id: string
    location_id: string | null
    is_enabled: boolean
    allow_new_contacts: boolean
    stop_automation_on_reply: boolean
    forward_to: string | null
    inherited: boolean
    platform_ready: boolean
    receiving_pipeline_ready: boolean
    platform_fallback_ready: boolean
    inbound_domain: string | null
    inbox_address: string | null
    email_identity_id: string | null
}

export interface EmailInboxSettingsUpdate {
    is_enabled: boolean
    allow_new_contacts: boolean
    stop_automation_on_reply: boolean
    forward_to: string | null
    email_identity_id: string | null
}

function params(institutionId?: string, locationId?: string) {
    return { institution_id: institutionId, location_id: locationId }
}

export async function getEmailInboxSettings(
    institutionId?: string,
    locationId?: string,
): Promise<EmailInboxSettings> {
    const { data } = await api.get<EmailInboxSettings>(BASE, {
        params: params(institutionId, locationId),
    })
    return data
}

export async function updateEmailInboxSettings(
    value: EmailInboxSettingsUpdate,
    institutionId?: string,
    locationId?: string,
): Promise<EmailInboxSettings> {
    const { data } = await api.put<EmailInboxSettings>(BASE, value, {
        params: params(institutionId, locationId),
    })
    return data
}
