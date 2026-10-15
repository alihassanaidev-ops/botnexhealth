import { useEffect, useState } from "react"
import { AlertTriangle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useLocationContext } from "@/context/LocationContext"
import { getConnectorHealth, type ConnectorHealth } from "@/lib/connector-health-api"

const POLL_MS = 60_000

/**
 * Warns clinic staff when the GoTracker connection to their practice system is
 * down, and clears when it recovers (Task 13). Renders nothing for NexHealth or
 * no-PMS clinics, and nothing while the connection is healthy.
 */
export function ConnectorHealthBanner() {
    const { selectedLocationId } = useLocationContext()
    const [health, setHealth] = useState<ConnectorHealth | null>(null)

    useEffect(() => {
        let cancelled = false
        const load = async () => {
            try {
                const next = await getConnectorHealth(selectedLocationId)
                if (!cancelled) setHealth(next)
            } catch {
                // A failed check must not break the dashboard; the endpoint's own
                // "unknown" state drives the warning when it can be read.
            }
        }
        load()
        const id = setInterval(load, POLL_MS)
        return () => {
            cancelled = true
            clearInterval(id)
        }
    }, [selectedLocationId])

    if (!health?.applicable || !health.warning) return null

    return (
        <Alert variant="destructive" data-testid="connector-health-warning">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Practice system connection problem</AlertTitle>
            <AlertDescription>
                {health.message ?? "Bookings may not be syncing right now."}
            </AlertDescription>
        </Alert>
    )
}
