import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toast } from "sonner"
import { Checkbox } from "@/components/ui/checkbox"
import { CalendarCheck, Plus, RefreshCcw, Trash2, Clock, Tag, Pencil, Users, MapPin } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import type { CachedAppointmentType, CachedDescriptor, CachedOperatory, CachedProvider, SetupOverview } from "@/types"
import {
    getSetupOverview,
    listAppointmentTypes,
    listDescriptors,
    listOperatories,
    listProviders,
    createAppointmentType,
    updateAppointmentType,
    deleteAppointmentType,
    triggerSync,
} from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"
import { useSelectedLocationId } from "@/context/LocationContext"

export default function AppointmentTypes() {
    const { user } = useAuth()
    const locationId = useSelectedLocationId()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [overview, setOverview] = useState<SetupOverview | null>(null)
    const [types, setTypes] = useState<CachedAppointmentType[]>([])
    const [descriptors, setDescriptors] = useState<CachedDescriptor[]>([])
    const [providers, setProviders] = useState<CachedProvider[]>([])
    const [operatories, setOperatories] = useState<CachedOperatory[]>([])
    const [loading, setLoading] = useState(true)
    const [syncing, setSyncing] = useState(false)

    // Create dialog state
    const [createOpen, setCreateOpen] = useState(false)
    const [creating, setCreating] = useState(false)
    const [newName, setNewName] = useState("")
    const [newDuration, setNewDuration] = useState(30)
    const [selectedDescriptorIds, setSelectedDescriptorIds] = useState<string[]>([])
    const [selectedProviderIds, setSelectedProviderIds] = useState<string[]>([])
    const [selectedOperatoryIds, setSelectedOperatoryIds] = useState<string[]>([])
    const [bookableOnline, setBookableOnline] = useState(true)
    const [descriptorSearch, setDescriptorSearch] = useState("")

    // Edit dialog state
    const [editOpen, setEditOpen] = useState(false)
    const [editing, setEditing] = useState(false)
    const [editTarget, setEditTarget] = useState<CachedAppointmentType | null>(null)
    const [editName, setEditName] = useState("")
    const [editDuration, setEditDuration] = useState("")
    const [editDescriptorIds, setEditDescriptorIds] = useState<string[]>([])
    const [editProviderIds, setEditProviderIds] = useState<string[]>([])
    const [editOperatoryIds, setEditOperatoryIds] = useState<string[]>([])
    const [editBookableOnline, setEditBookableOnline] = useState(true)
    const [editDescriptorSearch, setEditDescriptorSearch] = useState("")

    // Delete dialog state
    const [deleteTarget, setDeleteTarget] = useState<CachedAppointmentType | null>(null)
    const [deleting, setDeleting] = useState(false)
    const isGoTracker = overview?.pms_source === "gotracker"
    const canCreateAppointmentTypes = overview?.can_create_appointment_types ?? false

    const fetchData = useCallback(async () => {
        if (!locationId) return
        setLoading(true)
        try {
            const [overviewData, typesData, descriptorsData, providersData, operatoriesData] = await Promise.all([
                getSetupOverview(locationId),
                listAppointmentTypes(locationId),
                listDescriptors(locationId),
                listProviders(locationId),
                listOperatories(locationId),
            ])
            setOverview(overviewData)
            setTypes(typesData)
            setDescriptors(descriptorsData)
            setProviders(providersData)
            setOperatories(operatoriesData)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load data"
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
        if (isGoTracker && selectedProviderIds.length === 0) {
            toast.error("Select at least one provider")
            return
        }
        setCreating(true)
        try {
            await createAppointmentType({
                name: newName.trim(),
                duration_minutes: newDuration,
                descriptor_ids: isGoTracker ? [] : selectedDescriptorIds,
                provider_ids: isGoTracker ? selectedProviderIds : undefined,
                operatory_ids: isGoTracker ? selectedOperatoryIds : undefined,
                bookable_online: isGoTracker ? bookableOnline : undefined,
            }, locationId)
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

        if (isGoTracker && editProviderIds.length === 0) {
            toast.error("Select at least one provider")
            return
        }

        const baselineDescriptorIds = (editTarget.source_metadata?.descriptor_ids || []) as string[]
        const baselineProviderIds = (editTarget.source_metadata?.provider_ids || []) as string[]
        const baselineOperatoryIds = (editTarget.source_metadata?.operatory_ids || []) as string[]
        const baselineBookableOnline = editTarget.source_metadata?.bookable_online !== false
        const normalizedBase = [...baselineDescriptorIds].sort().join(",")
        const normalizedEdit = [...editDescriptorIds].sort().join(",")
        const normalizedProviderBase = [...baselineProviderIds].sort().join(",")
        const normalizedProviderEdit = [...editProviderIds].sort().join(",")
        const normalizedOperatoryBase = [...baselineOperatoryIds].sort().join(",")
        const normalizedOperatoryEdit = [...editOperatoryIds].sort().join(",")

        const payload: {
            name?: string
            duration_minutes?: number
            descriptor_ids?: string[]
            provider_ids?: string[]
            operatory_ids?: string[]
            bookable_online?: boolean
        } = {}

        if (trimmedName !== editTarget.name) payload.name = trimmedName
        if (parsedDuration !== null && parsedDuration !== baselineDuration) {
            payload.duration_minutes = parsedDuration
        }
        if (isGoTracker) {
            if (normalizedProviderEdit !== normalizedProviderBase) payload.provider_ids = editProviderIds
            if (normalizedOperatoryEdit !== normalizedOperatoryBase) payload.operatory_ids = editOperatoryIds
            if (editBookableOnline !== baselineBookableOnline) payload.bookable_online = editBookableOnline
        } else if (normalizedEdit !== normalizedBase) {
            payload.descriptor_ids = editDescriptorIds
        }

        if (Object.keys(payload).length === 0) {
            toast.info("No changes to save")
            return
        }

        setEditing(true)
        try {
            await updateAppointmentType(editTarget.source_id, payload, locationId)
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
            await deleteAppointmentType(deleteTarget.source_id, locationId)
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
        setSelectedProviderIds([])
        setSelectedOperatoryIds([])
        setBookableOnline(true)
        setDescriptorSearch("")
    }

    const openEditDialog = (type: CachedAppointmentType) => {
        setEditTarget(type)
        setEditName(type.name)
        setEditDuration(type.duration_minutes ? String(type.duration_minutes) : "")
        setEditDescriptorIds((type.source_metadata?.descriptor_ids || []) as string[])
        setEditProviderIds((type.source_metadata?.provider_ids || []) as string[])
        setEditOperatoryIds((type.source_metadata?.operatory_ids || []) as string[])
        setEditBookableOnline(type.source_metadata?.bookable_online !== false)
        setEditDescriptorSearch("")
        setEditOpen(true)
    }

    const resetEditForm = () => {
        setEditTarget(null)
        setEditName("")
        setEditDuration("")
        setEditDescriptorIds([])
        setEditProviderIds([])
        setEditOperatoryIds([])
        setEditBookableOnline(true)
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

    const toggleProvider = (sourceId: string) => {
        setSelectedProviderIds((prev) =>
            prev.includes(sourceId)
                ? prev.filter((id) => id !== sourceId)
                : [...prev, sourceId]
        )
    }

    const toggleOperatory = (sourceId: string) => {
        setSelectedOperatoryIds((prev) =>
            prev.includes(sourceId)
                ? prev.filter((id) => id !== sourceId)
                : [...prev, sourceId]
        )
    }

    const toggleEditProvider = (sourceId: string) => {
        setEditProviderIds((prev) =>
            prev.includes(sourceId)
                ? prev.filter((id) => id !== sourceId)
                : [...prev, sourceId]
        )
    }

    const toggleEditOperatory = (sourceId: string) => {
        setEditOperatoryIds((prev) =>
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

    const getProviderNames = (type: CachedAppointmentType): string => {
        const ids = type.source_metadata?.provider_ids || []
        if (ids.length === 0) return "-"
        return ids
            .map((id) => {
                const provider = providers.find((p) => p.source_id === id)
                return provider?.name || [provider?.first_name, provider?.last_name].filter(Boolean).join(" ") || id
            })
            .join(", ")
    }

    const getOperatoryNames = (type: CachedAppointmentType): string => {
        const ids = type.source_metadata?.operatory_ids || []
        if (ids.length === 0) return "All operatories"
        return ids
            .map((id) => {
                const operatory = operatories.find((op) => op.source_id === id)
                return operatory?.name || id
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
        <div className="relative flex-1 space-y-4 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <PageHeader
                icon={CalendarCheck}
                title="Appointment Types"
                description="Configure the types of appointments your practice offers."
                actions={canManage && (
                    <>
                        <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                            <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                        </Button>
                        {canCreateAppointmentTypes && (
                            <Button onClick={() => setCreateOpen(true)}>
                                <Plus className="mr-2 h-4 w-4" /> Create
                            </Button>
                        )}
                    </>
                )}
            />

            <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>Duration</TableHead>
                            <TableHead>{isGoTracker ? "Linked Providers" : "EMR Descriptors"}</TableHead>
                            {canManage && canCreateAppointmentTypes && <TableHead className="text-right">Actions</TableHead>}
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={canManage && canCreateAppointmentTypes ? 4 : 3} className="h-24 text-center">
                                    <div className="flex justify-center text-muted-foreground">Loading...</div>
                                </TableCell>
                            </TableRow>
                        ) : types.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={canManage && canCreateAppointmentTypes ? 4 : 3} className="h-32 text-center text-muted-foreground">
                                    <p>No appointment types found.</p>
                                    <p className="text-sm mt-1">
                                        {canManage && canCreateAppointmentTypes
                                            ? 'Click "Sync" to fetch from your PMS, or "Create" to add a new one.'
                                            : canManage
                                                ? 'Click "Sync" to fetch appointment types from your PMS.'
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
                                <TableCell className="max-w-[300px] text-sm text-muted-foreground">
                                    {isGoTracker ? (
                                        <div className="space-y-1">
                                            <div className="truncate">{getProviderNames(type)}</div>
                                            <div className="truncate text-xs">{getOperatoryNames(type)}</div>
                                        </div>
                                    ) : (
                                        <span className="truncate block">{getDescriptorNames(type)}</span>
                                    )}
                                </TableCell>
                                {canManage && canCreateAppointmentTypes && (
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
                                    {isGoTracker
                                        ? "Define a new appointment type and link the providers who can offer it."
                                        : "Define a new appointment type. Optionally link EMR descriptors to map to PMS procedure codes."}
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
                                {isGoTracker && (
                                    <>
                                        <div className="flex items-center gap-2 rounded-md border px-3 py-2">
                                            <Checkbox
                                                id="bookable-online"
                                                checked={bookableOnline}
                                                onCheckedChange={(checked) => setBookableOnline(Boolean(checked))}
                                            />
                                            <Label htmlFor="bookable-online" className="cursor-pointer">Bookable online</Label>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>
                                                <Users className="h-3 w-3 inline mr-1" />
                                                Providers ({selectedProviderIds.length} selected)
                                            </Label>
                                            <div className="border rounded-md max-h-48 overflow-y-auto">
                                                {providers.length === 0 ? (
                                                    <p className="p-3 text-sm text-muted-foreground">Sync providers before creating appointment types.</p>
                                                ) : (
                                                    providers.map((provider) => (
                                                        <label
                                                            key={provider.source_id}
                                                            className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                        >
                                                            <Checkbox
                                                                checked={selectedProviderIds.includes(provider.source_id)}
                                                                onCheckedChange={() => toggleProvider(provider.source_id)}
                                                            />
                                                            <span className="text-sm">
                                                                {provider.name || [provider.first_name, provider.last_name].filter(Boolean).join(" ") || provider.source_id}
                                                            </span>
                                                        </label>
                                                    ))
                                                )}
                                            </div>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>
                                                <MapPin className="h-3 w-3 inline mr-1" />
                                                Operatories ({selectedOperatoryIds.length} selected)
                                            </Label>
                                            <div className="border rounded-md max-h-40 overflow-y-auto">
                                                {operatories.length === 0 ? (
                                                    <p className="p-3 text-sm text-muted-foreground">No operatories synced. Leaving this empty allows all operatories.</p>
                                                ) : (
                                                    operatories.map((operatory) => (
                                                        <label
                                                            key={operatory.source_id}
                                                            className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                        >
                                                            <Checkbox
                                                                checked={selectedOperatoryIds.includes(operatory.source_id)}
                                                                onCheckedChange={() => toggleOperatory(operatory.source_id)}
                                                            />
                                                            <span className="text-sm">{operatory.name}</span>
                                                        </label>
                                                    ))
                                                )}
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                Leave operatories empty if this type should be allowed everywhere.
                                            </p>
                                        </div>
                                    </>
                                )}
                                {!isGoTracker && descriptors.length > 0 && (
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
                                <Button onClick={handleCreate} disabled={creating || !newName.trim() || (isGoTracker && selectedProviderIds.length === 0)}>
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
                                    {isGoTracker
                                        ? "Update the appointment type details and provider links."
                                        : "Update the appointment type details and linked EMR descriptors."}
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
                                {isGoTracker && (
                                    <>
                                        <div className="flex items-center gap-2 rounded-md border px-3 py-2">
                                            <Checkbox
                                                id="edit-bookable-online"
                                                checked={editBookableOnline}
                                                onCheckedChange={(checked) => setEditBookableOnline(Boolean(checked))}
                                            />
                                            <Label htmlFor="edit-bookable-online" className="cursor-pointer">Bookable online</Label>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>
                                                <Users className="h-3 w-3 inline mr-1" />
                                                Providers ({editProviderIds.length} selected)
                                            </Label>
                                            <div className="border rounded-md max-h-48 overflow-y-auto">
                                                {providers.length === 0 ? (
                                                    <p className="p-3 text-sm text-muted-foreground">Sync providers before updating links.</p>
                                                ) : (
                                                    providers.map((provider) => (
                                                        <label
                                                            key={provider.source_id}
                                                            className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                        >
                                                            <Checkbox
                                                                checked={editProviderIds.includes(provider.source_id)}
                                                                onCheckedChange={() => toggleEditProvider(provider.source_id)}
                                                            />
                                                            <span className="text-sm">
                                                                {provider.name || [provider.first_name, provider.last_name].filter(Boolean).join(" ") || provider.source_id}
                                                            </span>
                                                        </label>
                                                    ))
                                                )}
                                            </div>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>
                                                <MapPin className="h-3 w-3 inline mr-1" />
                                                Operatories ({editOperatoryIds.length} selected)
                                            </Label>
                                            <div className="border rounded-md max-h-40 overflow-y-auto">
                                                {operatories.length === 0 ? (
                                                    <p className="p-3 text-sm text-muted-foreground">No operatories synced. Empty means all operatories.</p>
                                                ) : (
                                                    operatories.map((operatory) => (
                                                        <label
                                                            key={operatory.source_id}
                                                            className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                        >
                                                            <Checkbox
                                                                checked={editOperatoryIds.includes(operatory.source_id)}
                                                                onCheckedChange={() => toggleEditOperatory(operatory.source_id)}
                                                            />
                                                            <span className="text-sm">{operatory.name}</span>
                                                        </label>
                                                    ))
                                                )}
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                Leave operatories empty if this type should be allowed everywhere.
                                            </p>
                                        </div>
                                    </>
                                )}
                                {!isGoTracker && descriptors.length > 0 && (
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
                                <Button onClick={handleEdit} disabled={editing || !editName.trim() || (isGoTracker && editProviderIds.length === 0)}>
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
