import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toast } from "sonner"
import { Checkbox } from "@/components/ui/checkbox"
import { Plus, RefreshCcw, Trash2, Clock, Tag, Pencil } from "lucide-react"
import type { CachedAppointmentType, CachedDescriptor } from "@/types"
import {
    listAppointmentTypes,
    listDescriptors,
    createAppointmentType,
    updateAppointmentType,
    deleteAppointmentType,
    triggerSync,
} from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"

export default function AppointmentTypes() {
    const { user } = useAuth()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
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

    // Edit dialog state
    const [editOpen, setEditOpen] = useState(false)
    const [editing, setEditing] = useState(false)
    const [editTarget, setEditTarget] = useState<CachedAppointmentType | null>(null)
    const [editName, setEditName] = useState("")
    const [editDuration, setEditDuration] = useState("")
    const [editDescriptorIds, setEditDescriptorIds] = useState<string[]>([])
    const [editDescriptorSearch, setEditDescriptorSearch] = useState("")

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
        if (!canManage) return
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
        if (!canManage) return
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

    const handleEdit = async () => {
        if (!canManage || !editTarget) return
        const trimmedName = editName.trim()
        if (!trimmedName) {
            toast.error("Name is required")
            return
        }

        const baselineDuration = editTarget.duration_minutes ?? null
        const parsedDuration = editDuration.trim() === "" ? null : Number(editDuration)
        if (parsedDuration !== null && (Number.isNaN(parsedDuration) || parsedDuration < 5)) {
            toast.error("Duration must be at least 5 minutes")
            return
        }

        const baselineDescriptorIds = (editTarget.source_metadata?.descriptor_ids || []) as string[]
        const normalizedBase = [...baselineDescriptorIds].sort().join(",")
        const normalizedEdit = [...editDescriptorIds].sort().join(",")

        const payload: {
            name?: string
            duration_minutes?: number
            descriptor_ids?: string[]
        } = {}

        if (trimmedName !== editTarget.name) payload.name = trimmedName
        if (parsedDuration !== null && parsedDuration !== baselineDuration) {
            payload.duration_minutes = parsedDuration
        }
        if (normalizedEdit !== normalizedBase) payload.descriptor_ids = editDescriptorIds

        if (Object.keys(payload).length === 0) {
            toast.info("No changes to save")
            return
        }

        setEditing(true)
        try {
            await updateAppointmentType(editTarget.source_id, payload)
            toast.success(`Updated "${trimmedName}"`)
            setEditOpen(false)
            resetEditForm()
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to update"
            toast.error(message)
        } finally {
            setEditing(false)
        }
    }

    const handleDelete = async () => {
        if (!canManage) return
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

    const openEditDialog = (type: CachedAppointmentType) => {
        setEditTarget(type)
        setEditName(type.name)
        setEditDuration(type.duration_minutes ? String(type.duration_minutes) : "")
        setEditDescriptorIds((type.source_metadata?.descriptor_ids || []) as string[])
        setEditDescriptorSearch("")
        setEditOpen(true)
    }

    const resetEditForm = () => {
        setEditTarget(null)
        setEditName("")
        setEditDuration("")
        setEditDescriptorIds([])
        setEditDescriptorSearch("")
    }

    const toggleDescriptor = (sourceId: string) => {
        setSelectedDescriptorIds((prev) =>
            prev.includes(sourceId)
                ? prev.filter((id) => id !== sourceId)
                : [...prev, sourceId]
        )
    }

    const toggleEditDescriptor = (sourceId: string) => {
        setEditDescriptorIds((prev) =>
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

    const filteredEditDescriptors = descriptors.filter((d) => {
        const query = editDescriptorSearch.toLowerCase()
        return (
            d.name.toLowerCase().includes(query) ||
            (d.code?.toLowerCase().includes(query) ?? false)
        )
    })

    return (
        <div className="flex-1 space-y-4 bg-gradient-to-b from-background via-background to-accent/20 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Appointment Types</h2>
                    <p className="text-muted-foreground">
                        Configure the types of appointments your practice offers.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    {canManage && (
                        <>
                            <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                                <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                            </Button>
                            <Button onClick={() => setCreateOpen(true)}>
                                <Plus className="mr-2 h-4 w-4" /> Create
                            </Button>
                        </>
                    )}
                </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-primary/20 bg-background/60 shadow-sm">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>Duration</TableHead>
                            <TableHead>EMR Descriptors</TableHead>
                            {canManage && <TableHead className="text-right">Actions</TableHead>}
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={canManage ? 4 : 3} className="h-24 text-center">
                                    <div className="flex justify-center text-muted-foreground">Loading...</div>
                                </TableCell>
                            </TableRow>
                        ) : types.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={canManage ? 4 : 3} className="h-32 text-center text-muted-foreground">
                                    <p>No appointment types found.</p>
                                    <p className="text-sm mt-1">
                                        {canManage
                                            ? 'Click "Sync" to fetch from your PMS, or "Create" to add a new one.'
                                            : "No appointment types are currently configured."}
                                    </p>
                                </TableCell>
                            </TableRow>
                        ) : (
                            types.map((type) => (
                            <TableRow key={type.source_id}>
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
                                {canManage && (
                                    <TableCell className="text-right">
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() => openEditDialog(type)}
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() => setDeleteTarget(type)}
                                        >
                                            <Trash2 className="h-4 w-4 text-destructive" />
                                        </Button>
                                    </TableCell>
                                )}
                            </TableRow>
                        ))
                        )}
                    </TableBody>
                </Table>
            </div>

            {canManage && (
                <>
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

                    {/* Edit Dialog */}
                    <Dialog
                        open={editOpen}
                        onOpenChange={(open) => {
                            setEditOpen(open)
                            if (!open) resetEditForm()
                        }}
                    >
                        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
                            <DialogHeader>
                                <DialogTitle>Edit Appointment Type</DialogTitle>
                                <DialogDescription>
                                    Update the appointment type details and linked EMR descriptors.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-4 py-2">
                                <div className="space-y-2">
                                    <Label htmlFor="edit-name">Name</Label>
                                    <Input
                                        id="edit-name"
                                        placeholder="e.g. Adult Cleaning"
                                        value={editName}
                                        onChange={(e) => setEditName(e.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="edit-duration">Duration (minutes)</Label>
                                    <Input
                                        id="edit-duration"
                                        type="number"
                                        min={5}
                                        max={480}
                                        value={editDuration}
                                        onChange={(e) => setEditDuration(e.target.value)}
                                    />
                                </div>
                                {descriptors.length > 0 && (
                                    <div className="space-y-2">
                                        <Label>
                                            <Tag className="h-3 w-3 inline mr-1" />
                                            EMR Descriptors ({editDescriptorIds.length} selected)
                                        </Label>
                                        <Input
                                            placeholder="Search descriptors..."
                                            value={editDescriptorSearch}
                                            onChange={(e) => setEditDescriptorSearch(e.target.value)}
                                        />
                                        <div className="border rounded-md max-h-48 overflow-y-auto">
                                            {filteredEditDescriptors.length === 0 ? (
                                                <p className="p-3 text-sm text-muted-foreground">No descriptors found.</p>
                                            ) : (
                                                filteredEditDescriptors.map((d) => (
                                                    <label
                                                        key={d.source_id}
                                                        className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                        onClick={() => toggleEditDescriptor(d.source_id)}
                                                    >
                                                        <Checkbox
                                                            checked={editDescriptorIds.includes(d.source_id)}
                                                            onCheckedChange={() => toggleEditDescriptor(d.source_id)}
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
                                            Descriptors map to your PMS procedure codes. Optional — you can clear or update them.
                                        </p>
                                    </div>
                                )}
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={() => setEditOpen(false)}>Cancel</Button>
                                <Button onClick={handleEdit} disabled={editing || !editName.trim()}>
                                    {editing ? "Saving..." : "Save Changes"}
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
                </>
            )}
        </div>
    )
}
