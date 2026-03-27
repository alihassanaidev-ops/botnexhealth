import api from "@/lib/api"

// -- External recipients ----------------------------------------------------

export interface ExternalRecipient {
    id: string
    email: string
    template_type: string
    is_active: boolean
    created_at: string
}

export async function listExternalRecipients(): Promise<ExternalRecipient[]> {
    const { data } = await api.get<{ recipients: ExternalRecipient[] }>(
        "/institution/notification-recipients",
    )
    return data.recipients
}

export async function addExternalRecipient(body: {
    email: string
    template_types: string[]
}): Promise<ExternalRecipient[]> {
    const { data } = await api.post<{ recipients: ExternalRecipient[] }>(
        "/institution/notification-recipients",
        body,
    )
    return data.recipients
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
    const { data } = await api.get<{ preferences: NotificationPreference[] }>(
        "/institution/notification-preferences",
    )
    return data.preferences
}

export async function updateNotificationPreferences(
    preferences: NotificationPreference[],
): Promise<NotificationPreference[]> {
    const { data } = await api.put<{ preferences: NotificationPreference[] }>(
        "/institution/notification-preferences",
        { preferences },
    )
    return data.preferences
}
