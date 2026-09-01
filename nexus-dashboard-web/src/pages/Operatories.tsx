import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/foundation/compat/button"
import { Switch } from "@/components/foundation/compat/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/foundation/compat/table"
import { Badge } from "@/components/foundation/compat/badge"
import { toast } from "sonner"
import { RefreshCcw, Armchair } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import type { CachedOperatory } from "@/types"
import { listOperatories, triggerSync, updateOperatory } from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"
import { useSelectedLocationId } from "@/context/LocationContext"

export default function Operatories() {
    const { user } = useAuth()
    const locationId = useSelectedLocationId()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [operatories, setOperatories] = useState<CachedOperatory[]>([])
    const [loading, setLoading] = useState(true)
    const [syncing, setSyncing] = useState(false)
    const [updatingIds, setUpdatingIds] = useState<Set<string>>(new Set())

    const fetchData = useCallback(async () => {
        if (!locationId) return
        setLoading(true)
        try {
            const data = await listOperatories(locationId, { includeHidden: true })
            setOperatories(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load operatories"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [locationId])

    useEffect(() => {
        fetchData()
    }, [fetchData])

    const handleSync = async () => {
        if (!canManage || !locationId) return
        setSyncing(true)
        try {
            const result = await triggerSync(locationId)
            if (result.success) {
                toast.success(`Synced: ${result.operatories_synced} operatories`)
                await fetchData()
            } else {
                toast.error(`Sync errors: ${result.errors.join(", ")}`)
            }
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Sync failed"
            toast.error(message)
        } finally {
            setSyncing(false)
        }
    }

    const handleToggleHidden = async (op: CachedOperatory, isHidden: boolean) => {
        if (!canManage || !locationId) return
        setUpdatingIds((prev) => new Set(prev).add(op.source_id))
        try {
            const updated = await updateOperatory(op.source_id, { is_hidden: isHidden }, locationId)
            setOperatories((prev) => prev.map((item) => (item.source_id === op.source_id ? updated : item)))
            toast.success(`${updated.name} is now ${updated.is_hidden ? "hidden" : "visible"}`)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to update operatory"
            toast.error(message)
        } finally {
            setUpdatingIds((prev) => {
                const next = new Set(prev)
                next.delete(op.source_id)
                return next
            })
        }
    }

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={Armchair}
                title="Operatories"
                description="Rooms and chairs synced from your PMS. Hide rooms locally to remove them from setup and scheduling selections."
                actions={canManage && (
                    <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                    </Button>
                )}
            />

            <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm mt-4">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>PMS ID</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead>Visibility</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={4} className="h-24 text-center">
                                    <div className="flex justify-center text-muted-foreground">Loading...</div>
                                </TableCell>
                            </TableRow>
                        ) : operatories.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={4} className="h-32 text-center text-muted-foreground">
                                    <p>No operatories found.</p>
                                    <p className="text-sm mt-1">
                                        {canManage ? 'Click "Sync" to fetch from your PMS.' : "No operatories are currently configured."}
                                    </p>
                                </TableCell>
                            </TableRow>
                        ) : (
                            operatories.map((op) => (
                                <TableRow key={op.source_id || op.id} className={op.is_hidden ? "opacity-70" : undefined}>
                                    <TableCell className="font-medium">{op.name}</TableCell>
                                    <TableCell className="font-mono text-sm">{op.source_id}</TableCell>
                                    <TableCell>
                                        <Badge
                                            variant="secondary"
                                            className={op.is_active
                                                ? "border border-border bg-primary/10 text-primary"
                                                : "border border-border bg-muted text-muted-foreground"}
                                        >
                                            {op.is_active ? "Active" : "Inactive"}
                                        </Badge>
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex items-center gap-3">
                                            <Badge
                                                variant="secondary"
                                                className={op.is_hidden
                                                    ? "border border-border bg-muted text-muted-foreground"
                                                    : "border border-border bg-emerald-500/10 text-emerald-500"}
                                            >
                                                {op.is_hidden ? "Hidden" : "Visible"}
                                            </Badge>
                                            {canManage && (
                                                <Switch
                                                    aria-label={`${op.is_hidden ? "Show" : "Hide"} ${op.name}`}
                                                    checked={!op.is_hidden}
                                                    disabled={updatingIds.has(op.source_id)}
                                                    onCheckedChange={(checked) => handleToggleHidden(op, checked !== true)}
                                                />
                                            )}
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            <div className="text-sm text-muted-foreground">
                Names and PMS active status are synced from your practice management system.
                Hidden operatories stay in this list but are removed from scheduling and appointment-type setup.
            </div>
        </div>
    )
}
