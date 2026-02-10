import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { toast } from "sonner"
import { RefreshCcw } from "lucide-react"
import type { CachedOperatory } from "@/types"
import { listOperatories, triggerSync } from "@/lib/tenant-api"

export default function Operatories() {
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
        <div className="flex-1 space-y-4 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Operatories</h2>
                    <p className="text-muted-foreground">
                        Rooms and chairs synced from your PMS. Read-only — manage operatories in your practice management system.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                    </Button>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Rooms & Chairs</CardTitle>
                    <CardDescription>
                        {operatories.length} operator{operatories.length !== 1 ? "ies" : "y"} found.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-8 text-muted-foreground">Loading...</div>
                    ) : operatories.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                            <p>No operatories found.</p>
                            <p className="text-sm mt-1">Click "Sync" to fetch from your PMS.</p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Name</TableHead>
                                    <TableHead>PMS ID</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Source</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {operatories.map((op) => (
                                    <TableRow key={op.id}>
                                        <TableCell className="font-medium">{op.name}</TableCell>
                                        <TableCell className="font-mono text-sm">{op.source_id}</TableCell>
                                        <TableCell>
                                            <Badge variant={op.is_active ? "default" : "secondary"}>
                                                {op.is_active ? "Active" : "Inactive"}
                                            </Badge>
                                        </TableCell>
                                        <TableCell>
                                            <Badge variant="outline">{op.source}</Badge>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            <div className="text-sm text-muted-foreground">
                Operatories are synced from your practice management system.
                To add or modify operatories, contact your PMS administrator.
            </div>
        </div>
    )
}
