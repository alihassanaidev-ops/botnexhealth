import { useCallback, useEffect, useState } from "react"
import { RefreshCcw, Tag } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/PageHeader"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { CachedDescriptor } from "@/types"
import { getSetupOverview, listReasons, triggerSync } from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"
import { useSelectedLocationId } from "@/context/LocationContext"

function reasonMinutes(reason: CachedDescriptor): string {
    const minutes = reason.source_metadata?.minutes
    return typeof minutes === "number" || typeof minutes === "string" ? `${minutes} min` : "-"
}

export default function Reasons() {
    const { user } = useAuth()
    const locationId = useSelectedLocationId()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [reasons, setReasons] = useState<CachedDescriptor[]>([])
    const [loading, setLoading] = useState(true)
    const [syncing, setSyncing] = useState(false)
    const [isGoTracker, setIsGoTracker] = useState(false)

    const fetchData = useCallback(async () => {
        if (!locationId) return
        setLoading(true)
        try {
            const overview = await getSetupOverview(locationId)
            const gotracker = overview.pms_source === "gotracker"
            setIsGoTracker(gotracker)
            setReasons(gotracker ? await listReasons(locationId) : [])
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load reasons"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [locationId])

    useEffect(() => {
        void fetchData()
    }, [fetchData])

    const handleSync = async () => {
        if (!canManage || !locationId) return
        setSyncing(true)
        try {
            const result = await triggerSync(locationId)
            if (!result.success) {
                toast.error(`Sync errors: ${result.errors.join(", ")}`)
                return
            }
            toast.success(`Synced ${result.descriptors_synced} GoTracker reasons`)
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Sync failed"
            toast.error(message)
        } finally {
            setSyncing(false)
        }
    }

    return (
        <div className="relative flex-1 space-y-4 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <PageHeader
                icon={Tag}
                title="Reasons"
                description="Tracker-native reasons. They are read from GoTracker; link one to a scheduling appointment type from Appointment Types."
                actions={canManage && isGoTracker && (
                    <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                    </Button>
                )}
            />

            {!loading && !isGoTracker ? (
                <p className="rounded-lg border bg-background/60 p-6 text-muted-foreground">
                    Reasons are available only for GoTracker locations.
                </p>
            ) : (
                <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Reason</TableHead>
                                <TableHead>Duration</TableHead>
                                <TableHead>Kind</TableHead>
                                <TableHead>Status</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow><TableCell colSpan={4} className="h-24 text-center text-muted-foreground">Loading...</TableCell></TableRow>
                            ) : reasons.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={4} className="h-32 text-center text-muted-foreground">
                                        No reasons found. {canManage ? "Click Sync to load them from GoTracker." : ""}
                                    </TableCell>
                                </TableRow>
                            ) : reasons.map((reason) => (
                                <TableRow key={reason.source_id}>
                                    <TableCell className="font-medium">{reason.name}</TableCell>
                                    <TableCell>{reasonMinutes(reason)}</TableCell>
                                    <TableCell>{reason.source_metadata?.is_recall === true ? "Recall" : "Standard"}</TableCell>
                                    <TableCell>
                                        <Badge variant={reason.is_active ? "secondary" : "outline"}>
                                            {reason.is_active ? "Active" : "Inactive"}
                                        </Badge>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
        </div>
    )
}
