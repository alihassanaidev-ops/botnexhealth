import api from "@/lib/api"
import type { OutboundVoiceProfile } from "@/types"

export async function listOutboundVoiceProfiles(opts?: {
    locationId?: string | null
    isActive?: boolean
    purpose?: string | null
}): Promise<OutboundVoiceProfile[]> {
    const params = new URLSearchParams()
    if (opts?.locationId) params.set("location_id", opts.locationId)
    if (opts?.isActive !== undefined) params.set("is_active", String(opts.isActive))
    if (opts?.purpose) params.set("purpose", opts.purpose)
    const query = params.toString()
    const { data } = await api.get<OutboundVoiceProfile[]>(
        `/outbound-voice/profiles${query ? `?${query}` : ""}`,
    )
    return data
}
