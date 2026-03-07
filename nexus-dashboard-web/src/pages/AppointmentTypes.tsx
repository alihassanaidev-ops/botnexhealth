import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toast } from "sonner"
import { Checkbox } from "@/components/ui/checkbox"
import { Plus, RefreshCcw, Trash2, Clock, Tag } from "lucide-react"
import type { CachedAppointmentType, CachedDescriptor } from "@/types"
import {
    listAppointmentTypes,
    listDescriptors,
    createAppointmentType,
    deleteAppointmentType,
    triggerSync,
} from "@/lib/tenant-api"

export default function AppointmentTypes() {
    const [types, setTypes] = useState<CachedAppointmentType[]>([])
    const [descriptors, setDescriptors] = useState<CachedDescriptor[]>([])
    const [loading, setLoading] = useState(true)
    const [syncing, setSyncing] = useState(false)

    // Create dialog state
    const [createOpen, setCreateOpen] = useState(false)
    const [creating, setCreating] = useState(false)
    const [newName, setNewName] = useState("")
    const [newDuration, setNewDuration] = useState(30)
    const [selectedDescriptorIds, setSelectedDescriptorIds] = useState<string[]>([])
    const [descriptorSearch, setDescriptorSearch] = useState("")

    // Delete dialog state
    const [deleteTarget, setDeleteTarget] = useState<CachedAppointmentType | null>(null)
    const [deleting, setDeleting] = useState(false)

    const fetchData = useCallback(async () => {
        setLoading(true)
        try {
            const [typesData, descriptorsData] = await Promise.all([
                listAppointmentTypes(),
                listDescriptors(),
            ])
            setTypes(typesData)
            setDescriptors(descriptorsData)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load data"
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
                toast.success(
                    `Synced: ${result.appointment_types_synced} appointment types, ${result.descriptors_synced} descriptors`
                )
                await fetchData()
            } else {
                toast.error(`Sync had errors: ${result.errors.join(", ")}`)
            }
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Sync failed"
            toast.error(message)
        } finally {
            setSyncing(false)
        }
    }

    const handleCreate = async () => {
        if (!newName.trim()) {
            toast.error("Name is required")
            return
        }
        setCreating(true)
        try {
            await createAppointmentType({
                name: newName.trim(),
                duration_minutes: newDuration,
                descriptor_ids: selectedDescriptorIds,
            })
            toast.success(`Created appointment type "${newName.trim()}"`)
            setCreateOpen(false)
            resetCreateForm()
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to create"
            toast.error(message)
        } finally {
            setCreating(false)
        }
    }

    const handleDelete = async () => {
        if (!deleteTarget) return
        setDeleting(true)
        try {
            await deleteAppointmentType(deleteTarget.source_id)
            toast.success(`Deleted "${deleteTarget.name}"`)
            setDeleteTarget(null)
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to delete"
            toast.error(message)
        } finally {
            setDeleting(false)
        }
    }

    const resetCreateForm = () => {
        setNewName("")
        setNewDuration(30)
        setSelectedDescriptorIds([])
        setDescriptorSearch("")
    }

    const toggleDescriptor = (sourceId: string) => {
        setSelectedDescriptorIds((prev) =>
            prev.includes(sourceId)
                ? prev.filter((id) => id !== sourceId)
                : [...prev, sourceId]
        )
    }

    const getDescriptorNames = (type: CachedAppointmentType): string => {
        const ids = type.source_metadata?.descriptor_ids || []
        if (ids.length === 0) return "-"
        return ids
            .map((id) => {
                const d = descriptors.find((desc) => desc.source_id === id)
                return d ? (d.code ? `${d.code} - ${d.name}` : d.name) : id
            })
            .join(", ")
    }

    const filteredDescriptors = descriptors.filter((d) => {
        const query = descriptorSearch.toLowerCase()
        return (
            d.name.toLowerCase().includes(query) ||
            (d.code?.toLowerCase().includes(query) ?? false)
        )
    })

    return (
        <div className="flex-1 space-y-4 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Appointment Types</h2>
                    <p className="text-muted-foreground">
                        Configure the types of appointments your practice offers.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                    </Button>
                    <Button onClick={() => setCreateOpen(true)}>
                        <Plus className="mr-2 h-4 w-4" /> Create
                    </Button>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Configured Types</CardTitle>
                    <CardDescription>
                        {types.length} appointment type{types.length !== 1 ? "s" : ""} configured.
                        {" "}Types must be linked to provider availabilities to generate bookable slots.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-8 text-muted-foreground">Loading...</div>
                    ) : types.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                            <p>No appointment types found.</p>
                            <p className="text-sm mt-1">Click "Sync" to fetch from your PMS, or "Create" to add a new one.</p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Name</TableHead>
                                    <TableHead>Duration</TableHead>
                                    <TableHead>EMR Descriptors</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {types.map((type) => (
                                    <TableRow key={type.id}>
                                        <TableCell className="font-medium">{type.name}</TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-1">
                                                <Clock className="h-3 w-3 text-muted-foreground" />
                                                {type.duration_minutes ? `${type.duration_minutes} min` : "-"}
                                            </div>
                                        </TableCell>
                                        <TableCell className="max-w-[300px] truncate text-sm text-muted-foreground">
                                            {getDescriptorNames(type)}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => setDeleteTarget(type)}
                                            >
                                                <Trash2 className="h-4 w-4 text-destructive" />
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Create Dialog */}
            <Dialog open={createOpen} onOpenChange={(open) => { setCreateOpen(open); if (!open) resetCreateForm() }}>
                <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Create Appointment Type</DialogTitle>
                        <DialogDescription>
                            Define a new appointment type. Optionally link EMR descriptors to map to PMS procedure codes.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <div className="space-y-2">
                            <Label htmlFor="name">Name</Label>
                            <Input
                                id="name"
                                placeholder="e.g. Adult Cleaning"
                                value={newName}
                                onChange={(e) => setNewName(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="duration">Duration (minutes)</Label>
                            <Input
                                id="duration"
                                type="number"
                                min={5}
                                max={480}
                                value={newDuration}
                                onChange={(e) => setNewDuration(Number(e.target.value))}
                            />
                        </div>
                        {descriptors.length > 0 && (
                            <div className="space-y-2">
                                <Label>
                                    <Tag className="h-3 w-3 inline mr-1" />
                                    EMR Descriptors ({selectedDescriptorIds.length} selected)
                                </Label>
                                <Input
                                    placeholder="Search descriptors..."
                                    value={descriptorSearch}
                                    onChange={(e) => setDescriptorSearch(e.target.value)}
                                />
                                <div className="border rounded-md max-h-48 overflow-y-auto">
                                    {filteredDescriptors.length === 0 ? (
                                        <p className="p-3 text-sm text-muted-foreground">No descriptors found.</p>
                                    ) : (
                                        filteredDescriptors.map((d) => (
                                            <label
                                                key={d.source_id}
                                                className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                onClick={() => toggleDescriptor(d.source_id)}
                                            >
                                                <Checkbox
                                                    checked={selectedDescriptorIds.includes(d.source_id)}
                                                    onCheckedChange={() => toggleDescriptor(d.source_id)}
                                                />
                                                <span className="text-sm">
                                                    {d.code && <span className="font-mono text-xs mr-1">{d.code}</span>}
                                                    {d.name}
                                                </span>
                                            </label>
                                        ))
                                    )}
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Descriptors map to your PMS procedure codes. Optional — you can create without them.
                                </p>
                            </div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
                        <Button onClick={handleCreate} disabled={creating || !newName.trim()}>
                            {creating ? "Creating..." : "Create"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete Confirmation */}
            <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete Appointment Type</DialogTitle>
                        <DialogDescription>
                            Are you sure you want to delete "{deleteTarget?.name}"? This may affect existing
                            schedules and booking configurations.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteTarget(null)}>Cancel</Button>
                        <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
                            {deleting ? "Deleting..." : "Delete"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
