import api from "@/lib/api"

function unwrapArray<T>(
    payload: unknown,
    keys: string[],
    endpoint: string,
): T[] {
    if (Array.isArray(payload)) return payload as T[]
    if (payload && typeof payload === "object") {
        const record = payload as Record<string, unknown>
        for (const key of keys) {
            const value = record[key]
            if (Array.isArray(value)) return value as T[]
        }
    }
    console.warn(`Expected array response from ${endpoint}`, payload)
    return []
}

export interface SmsNotificationRecipient {
    id: string
    phone_number_masked: string
    notification_type: string
    /** null = alerts for every location in the institution. */
    location_id?: string | null
    is_active: boolean
    created_at: string
}

const endpoint = "/institution/sms-notification-recipients"

export async function listSmsNotificationRecipients(): Promise<SmsNotificationRecipient[]> {
    const { data } = await api.get<unknown>(endpoint)
    return unwrapArray<SmsNotificationRecipient>(
        data,
        ["recipients", "data", "items"],
        endpoint,
    )
}

export async function addSmsNotificationRecipient(body: {
    phone_number: string
    notification_type?: string
    /** Omit for institution-wide; ignored (forced) for location admins. */
    location_id?: string | null
}): Promise<SmsNotificationRecipient[]> {
    const { data } = await api.post<unknown>(endpoint, body)
    return unwrapArray<SmsNotificationRecipient>(
        data,
        ["recipients", "data", "items"],
        endpoint,
    )
}

export async function updateSmsNotificationRecipient(
    id: string,
    body: { is_active?: boolean },
): Promise<SmsNotificationRecipient> {
    const { data } = await api.put<SmsNotificationRecipient>(`${endpoint}/${id}`, body)
    return data
}

export async function deleteSmsNotificationRecipient(id: string): Promise<void> {
    await api.delete(`${endpoint}/${id}`)
}
