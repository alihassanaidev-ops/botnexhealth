import api from "@/lib/api"
import type { OperatingHoursEntry, OperatingHoursResponse } from "@/types"

export interface InstitutionPortalMe {
    id: string
    name: string
    slug: string
    role: string
    institution_id: string | null
    location_id: string | null
}

export interface InstitutionPortalLocation {
    id: string
    institution_id: string
    name: string
    slug: string
    is_active: boolean
    phone: string | null
    timezone: string | null
}

export async function getInstitutionPortalMe(): Promise<InstitutionPortalMe> {
    const { data } = await api.get<InstitutionPortalMe>("/institution/me")
    return data
}

export async function listInstitutionPortalLocations(): Promise<InstitutionPortalLocation[]> {
    const { data } = await api.get<InstitutionPortalLocation[]>("/institution/locations")
    return data
}

export async function getLocationOperatingHours(locSlug: string): Promise<OperatingHoursResponse[]> {
    const { data } = await api.get<OperatingHoursResponse[]>(`/institution/locations/${locSlug}/operating-hours`)
    return data
}

export async function updateLocationOperatingHours(
    locSlug: string,
    hours: OperatingHoursEntry[],
): Promise<OperatingHoursResponse[]> {
    const { data } = await api.put<OperatingHoursResponse[]>(
        `/institution/locations/${locSlug}/operating-hours`,
        { hours },
    )
    return data
}

export async function inviteInstitutionAdmin(email: string): Promise<void> {
    await api.post("/institution/users/invite-institution-admin", { email })
}

export async function inviteLocationAdmin(locSlug: string, email: string): Promise<void> {
    await api.post(`/institution/locations/${locSlug}/invite-location-admin`, { email })
}
