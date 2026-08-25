import api from "@/lib/api"
import type { RetellSmsChatProfile } from "@/types"

export type RetellSmsChatProfilePayload = {
    location_id?: string
    retell_agent_id: string
    agent_version?: number | null
    display_name: string
    purpose?: string | null
    allowed_tools?: string[]
    is_active?: boolean
    config?: Record<string, unknown> | null
}

export async function listRetellSmsChatProfiles(opts?: {
    locationId?: string | null
    isActive?: boolean
}): Promise<RetellSmsChatProfile[]> {
    const params = new URLSearchParams()
    if (opts?.locationId) params.set("location_id", opts.locationId)
    if (opts?.isActive !== undefined) params.set("is_active", String(opts.isActive))
    const query = params.toString()
    const { data } = await api.get<RetellSmsChatProfile[]>(
        `/retell-sms/profiles${query ? `?${query}` : ""}`,
    )
    return data
}

export async function createRetellSmsChatProfile(
    payload: RetellSmsChatProfilePayload & { location_id: string },
): Promise<RetellSmsChatProfile> {
    const { data } = await api.post<RetellSmsChatProfile>("/retell-sms/profiles", payload)
    return data
}

export async function updateRetellSmsChatProfile(
    profileId: string,
    payload: Partial<Omit<RetellSmsChatProfilePayload, "location_id">>,
): Promise<RetellSmsChatProfile> {
    const { data } = await api.patch<RetellSmsChatProfile>(
        `/retell-sms/profiles/${profileId}`,
        payload,
    )
    return data
}

export async function deleteRetellSmsChatProfile(profileId: string): Promise<void> {
    await api.delete(`/retell-sms/profiles/${profileId}`)
}
