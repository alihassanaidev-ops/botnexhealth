import api from "@/lib/api"

function unwrapArray<T>(
    payload: unknown,
    keys: string[],
    endpoint: string,
): T[] {
    if (Array.isArray(payload)) return payload as T[];
    if (payload && typeof payload === "object") {
        const record = payload as Record<string, unknown>;
        for (const key of keys) {
            const value = record[key];
            if (Array.isArray(value)) return value as T[];
        }
    }
    console.warn(`Expected array response from ${endpoint}`, payload);
    return [];
}

// -- External recipients ----------------------------------------------------

export interface ExternalRecipient {
    id: string
    email: string
    template_type: string
    is_active: boolean
    created_at: string
}

export async function listExternalRecipients(): Promise<ExternalRecipient[]> {
    const { data } = await api.get<unknown>(
        "/institution/notification-recipients",
    )
    return unwrapArray<ExternalRecipient>(
        data,
        ["recipients", "data", "items"],
        "/institution/notification-recipients",
    )
}

export async function addExternalRecipient(body: {
    email: string
    template_types: string[]
}): Promise<ExternalRecipient[]> {
    const { data } = await api.post<unknown>(
        "/institution/notification-recipients",
        body,
    )
    return unwrapArray<ExternalRecipient>(
        data,
        ["recipients", "data", "items"],
        "/institution/notification-recipients",
    )
}

export async function updateExternalRecipient(
    id: string,
    body: { is_active?: boolean },
): Promise<ExternalRecipient> {
    const { data } = await api.put<ExternalRecipient>(
        `/institution/notification-recipients/${id}`,
        body,
    )
    return data
}

export async function deleteExternalRecipient(id: string): Promise<void> {
    await api.delete(`/institution/notification-recipients/${id}`)
}

// -- User notification preferences ------------------------------------------

export interface NotificationPreference {
    template_type: string
    is_enabled: boolean
}

export async function getNotificationPreferences(): Promise<NotificationPreference[]> {
    const { data } = await api.get<unknown>(
        "/institution/notification-preferences",
    )
    return unwrapArray<NotificationPreference>(
        data,
        ["preferences", "data", "items"],
        "/institution/notification-preferences",
    )
}

export async function updateNotificationPreferences(
    preferences: NotificationPreference[],
): Promise<NotificationPreference[]> {
    const { data } = await api.put<unknown>(
        "/institution/notification-preferences",
        { preferences },
    )
    return unwrapArray<NotificationPreference>(
        data,
        ["preferences", "data", "items"],
        "/institution/notification-preferences",
    )
}
