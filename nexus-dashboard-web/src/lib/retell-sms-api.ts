import api from "@/lib/api"
import type { RetellSmsChatProfile } from "@/types"

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
