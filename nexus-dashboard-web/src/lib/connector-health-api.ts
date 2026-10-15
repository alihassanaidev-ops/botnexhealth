import api from "@/lib/api"

/**
 * Clinic-facing connector health (Task 13). Read-only.
 *
 * When the GoTracker connector on the clinic PC stops reaching the cloud,
 * bookings stop syncing. The dashboard warns on this and clears on recovery.
 */
export interface ConnectorHealth {
    applicable: boolean
    status: string
    warning: boolean
    message: string | null
    connector_version: string | null
    last_report_at: string | null
    seconds_since_last_report: number | null
    queue_depth: number | null
    failed_write_count: number | null
    conflict_count: number | null
}

export async function getConnectorHealth(
    locationId?: string | null,
): Promise<ConnectorHealth> {
    const params = new URLSearchParams()
    if (locationId) params.set("location_id", locationId)
    const q = params.toString() ? `?${params.toString()}` : ""
    const { data } = await api.get<ConnectorHealth>(`/institution/connector-health${q}`)
    return data
}
