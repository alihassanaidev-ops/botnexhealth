import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { toast } from "sonner"
import { RefreshCcw } from "lucide-react"
import type { CachedOperatory } from "@/types"
import { listOperatories, triggerSync } from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"

export default function Operatories() {
    const { user } = useAuth()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [operatories, setOperatories] = useState<CachedOperatory[]>([])
    const [loading, setLoading] = useState(true)
    const [syncing, setSyncing] = useState(false)

    const fetchData = useCallback(async () => {
        setLoading(true)
        try {
            const data = await listOperatories()
            setOperatories(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load operatories"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchData()
    }, [fetchData])

    const handleSync = async () => {
        if (!canManage) return
        setSyncing(true)
        try {
            const result = await triggerSync()
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

    return (
        <div className="relative flex-1 space-y-4 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Operatories</h2>
                    <p className="text-muted-foreground">
                        Rooms and chairs synced from your PMS. Read-only — manage operatories in your practice management system.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    {canManage && (
                        <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                            <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                        </Button>
                    )}
                </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm mt-4">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>PMS ID</TableHead>
                            <TableHead>Status</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={3} className="h-24 text-center">
                                    <div className="flex justify-center text-muted-foreground">Loading...</div>
                                </TableCell>
                            </TableRow>
                        ) : operatories.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={3} className="h-32 text-center text-muted-foreground">
                                    <p>No operatories found.</p>
                                    <p className="text-sm mt-1">
                                        {canManage ? 'Click "Sync" to fetch from your PMS.' : "No operatories are currently configured."}
                                    </p>
                                </TableCell>
                            </TableRow>
                        ) : (
                            operatories.map((op) => (
                                <TableRow key={op.source_id || op.id}>
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
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            <div className="text-sm text-muted-foreground">
                Operatories are synced from your practice management system.
                To add or modify operatories, contact your PMS administrator.
            </div>
        </div>
    )
}
