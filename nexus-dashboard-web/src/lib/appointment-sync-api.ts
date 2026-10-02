import api from "@/lib/api"

export interface AppointmentSyncItem {
    id: string
    appointment_id: string
    patient_id: string | null
    contact_id: string | null
    patient_name: string | null
    location_id: string | null
    provider_id: string | null
    appointment_type_id: string | null
    start_time: string | null
    local_status: string
    gotracker_status_id: number | null
    gotracker_status_label: string | null
    is_confirmed: boolean | null
    is_preconfirmed: boolean | null
    last_status_source: string | null
    last_status_synced_at: string | null
    last_writeback_at: string | null
    last_event: string | null
    last_synced_at: string
    updated_at: string
}

export interface AppointmentSyncListResponse {
    total: number
    limit: number
    offset: number
    items: AppointmentSyncItem[]
}

export interface AppointmentSyncFilters {
    limit?: number
    offset?: number
    search?: string
    gotracker_status_id?: number
}

export async function listAppointmentSyncStatus(
    filters: AppointmentSyncFilters = {},
): Promise<AppointmentSyncListResponse> {
    const params = new URLSearchParams()
    params.set("limit", String(filters.limit ?? 50))
    params.set("offset", String(filters.offset ?? 0))
    if (filters.search) params.set("search", filters.search)
    if (filters.gotracker_status_id) {
        params.set("gotracker_status_id", String(filters.gotracker_status_id))
    }
    const { data } = await api.get<AppointmentSyncListResponse>(
        `/institution/appointment-sync?${params.toString()}`,
    )
    return data
}
