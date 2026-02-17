import { useEffect, useState, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toast } from "sonner"
import { RefreshCcw, AlertTriangle, Clock, Calendar } from "lucide-react"
import type { CachedProvider, CachedAvailability, CachedAppointmentType } from "@/types"
import {
    listProviders,
    listAvailabilities,
    listAppointmentTypes,
    updateAvailability,
    triggerSync,
} from "@/lib/tenant-api"

export default function ProvidersScheduling() {
    const [providers, setProviders] = useState<CachedProvider[]>([])
    const [availabilities, setAvailabilities] = useState<CachedAvailability[]>([])
    const [appointmentTypes, setAppointmentTypes] = useState<CachedAppointmentType[]>([])
    const [selectedProviderId, setSelectedProviderId] = useState<string>("")
    const [selectedApptTypeId, setSelectedApptTypeId] = useState<string>("all")
    const [loading, setLoading] = useState(true)
    const [loadingAvailabilities, setLoadingAvailabilities] = useState(false)
    const [syncing, setSyncing] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const initialLoadDone = useRef(false)

    // Edit dialog state
    const [editTarget, setEditTarget] = useState<CachedAvailability | null>(null)
    const [editTypeIds, setEditTypeIds] = useState<string[]>([])
    const [saving, setSaving] = useState(false)

    // Load providers + appointment types once on mount
    const fetchData = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const [p, at] = await Promise.all([
                listProviders(),
                listAppointmentTypes(),
            ])
            setProviders(p)
            setAppointmentTypes(at)
            // Auto-select first provider on initial load
            if (p.length > 0 && !initialLoadDone.current) {
                setSelectedProviderId(p[0].source_id)
                initialLoadDone.current = true
            }
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to load data"
            setError(message)
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [])

    // Fetch availabilities when provider changes
    const fetchAvailabilities = useCallback(async () => {
        if (!selectedProviderId) return
        setLoadingAvailabilities(true)
        try {
            const data = await listAvailabilities(undefined, selectedProviderId)
            setAvailabilities(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load availabilities"
            toast.error(message)
        } finally {
            setLoadingAvailabilities(false)
        }
    }, [selectedProviderId])

    useEffect(() => {
        fetchData()
    }, [fetchData])

    useEffect(() => {
        fetchAvailabilities()
    }, [fetchAvailabilities])

    // Reset appointment type filter when provider changes
    useEffect(() => {
        setSelectedApptTypeId("all")
    }, [selectedProviderId])

    const handleSync = async () => {
        setSyncing(true)
        try {
            const result = await triggerSync()
            if (result.success) {
                toast.success(
                    `Synced: ${result.providers_synced} providers, ${result.availabilities_synced} availabilities`
                )
                await fetchData()
                await fetchAvailabilities()
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

    const openEditDialog = (av: CachedAvailability) => {
        setEditTarget(av)
        setEditTypeIds(av.appointment_type_ids || [])
    }

    const toggleTypeId = (typeId: string) => {
        setEditTypeIds((prev) =>
            prev.includes(typeId)
                ? prev.filter((id) => id !== typeId)
                : [...prev, typeId]
        )
    }

    const handleSave = async () => {
        if (!editTarget) return
        setSaving(true)
        try {
            await updateAvailability(editTarget.source_id, {
                appointment_type_ids: editTypeIds,
            })
            toast.success("Availability updated")
            setEditTarget(null)
            await fetchAvailabilities()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to update"
            toast.error(message)
        } finally {
            setSaving(false)
        }
    }

    const selectedProvider = providers.find((p) => p.source_id === selectedProviderId)

    // Filter availabilities by selected appointment type
    const filteredAvailabilities = selectedApptTypeId === "all"
        ? availabilities
        : availabilities.filter(
            (av) => av.appointment_type_ids?.includes(selectedApptTypeId)
        )

    const unlinkedCount = availabilities.filter(
        (av) => !av.appointment_type_ids || av.appointment_type_ids.length === 0
    ).length

    // Collect appointment types that appear in this provider's availabilities
    const availableApptTypeIds = new Set(availabilities.flatMap((av) => av.appointment_type_ids || []))
    const relevantApptTypes = appointmentTypes.filter((at) => availableApptTypeIds.has(at.source_id))

    return (
        <div className="flex-1 space-y-4 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Providers & Scheduling</h2>
                    <p className="text-muted-foreground">
                        Link appointment types to provider availabilities so NexHealth can generate bookable slots.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                    </Button>
                </div>
            </div>

            {error && (
                <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                        {error}. Please try refreshing the page or click Sync.
                    </AlertDescription>
                </Alert>
            )}

            {unlinkedCount > 0 && !loading && !error && (
                <Alert className="border-yellow-200 bg-yellow-50 text-yellow-800">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                        {unlinkedCount} availability schedule{unlinkedCount !== 1 ? "s" : ""} without linked
                        appointment types. These won't generate bookable slots.
                    </AlertDescription>
                </Alert>
            )}

            {loading ? (
                <div className="flex justify-center py-8 text-muted-foreground">Loading...</div>
            ) : providers.length === 0 ? (
                <Card>
                    <CardContent className="py-8 text-center text-muted-foreground">
                        <p>No providers found. Click "Sync" to fetch from your PMS.</p>
                    </CardContent>
                </Card>
            ) : (
                <>
                    {/* Filters: Provider → Appointment Type */}
                    <div className="flex items-center gap-4 flex-wrap">
                        <div className="flex items-center gap-2">
                            <label className="text-sm font-medium whitespace-nowrap">Provider:</label>
                            <Select value={selectedProviderId} onValueChange={setSelectedProviderId}>
                                <SelectTrigger className="w-[280px]">
                                    <SelectValue placeholder="Select provider" />
                                </SelectTrigger>
                                <SelectContent>
                                    {providers.map((p) => (
                                        <SelectItem key={p.source_id} value={p.source_id}>
                                            {p.name || `${p.first_name} ${p.last_name}`}
                                            {p.specialty ? ` (${p.specialty})` : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="flex items-center gap-2">
                            <label className="text-sm font-medium whitespace-nowrap">Appointment Type:</label>
                            <Select value={selectedApptTypeId} onValueChange={setSelectedApptTypeId}>
                                <SelectTrigger className="w-[260px]">
                                    <SelectValue placeholder="All Types" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All Types</SelectItem>
                                    {relevantApptTypes.map((at) => (
                                        <SelectItem key={at.source_id} value={at.source_id}>
                                            {at.name}
                                            {at.duration_minutes ? ` (${at.duration_minutes} min)` : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <Card>
                        <CardHeader>
                            <CardTitle>
                                Availabilities for {selectedProvider?.name || `${selectedProvider?.first_name} ${selectedProvider?.last_name}`}
                            </CardTitle>
                            <CardDescription>
                                {filteredAvailabilities.length} schedule{filteredAvailabilities.length !== 1 ? "s" : ""} found
                                {selectedApptTypeId !== "all" ? " (filtered)" : ""}.
                                Click "Edit Linking" to associate appointment types.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            {loadingAvailabilities ? (
                                <div className="flex justify-center py-6 text-muted-foreground">Loading availabilities...</div>
                            ) : filteredAvailabilities.length === 0 ? (
                                <p className="text-center py-6 text-muted-foreground">
                                    {selectedApptTypeId !== "all"
                                        ? "No availabilities match this appointment type."
                                        : "No availabilities found for this provider."}
                                </p>
                            ) : (
                                <div className="space-y-3">
                                    {filteredAvailabilities.map((av) => {
                                        const hasTypes = av.appointment_type_ids && av.appointment_type_ids.length > 0
                                        return (
                                            <div
                                                key={av.id}
                                                className={`rounded-lg border p-4 ${!hasTypes ? "border-yellow-200 bg-yellow-50/50" : ""}`}
                                            >
                                                <div className="flex items-start justify-between">
                                                    <div className="space-y-1">
                                                        <div className="flex items-center gap-2">
                                                            <Clock className="h-4 w-4 text-muted-foreground" />
                                                            <span className="font-medium">
                                                                {av.begin_time} - {av.end_time}
                                                            </span>
                                                            {av.synced && (
                                                                <Badge variant="secondary" className="text-xs">
                                                                    Synced from PMS
                                                                </Badge>
                                                            )}
                                                            {!av.synced && (
                                                                <Badge variant="outline" className="text-xs">
                                                                    Manual
                                                                </Badge>
                                                            )}
                                                        </div>
                                                        {av.days && av.days.length > 0 && (
                                                            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                                                <Calendar className="h-3 w-3" />
                                                                {av.days.join(", ")}
                                                            </div>
                                                        )}
                                                        {av.specific_date && (
                                                            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                                                <Calendar className="h-3 w-3" />
                                                                Specific date: {av.specific_date}
                                                            </div>
                                                        )}
                                                        <div className="text-sm">
                                                            <span className="text-muted-foreground">Appointment Types: </span>
                                                            {hasTypes ? (
                                                                <span>{av.appointment_type_names?.join(", ")}</span>
                                                            ) : (
                                                                <span className="text-yellow-700 font-medium">
                                                                    None linked
                                                                </span>
                                                            )}
                                                        </div>
                                                    </div>
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={() => openEditDialog(av)}
                                                    >
                                                        Edit Linking
                                                    </Button>
                                                </div>
                                            </div>
                                        )
                                    })}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </>
            )}

            {/* Edit Linking Dialog */}
            <Dialog open={!!editTarget} onOpenChange={() => setEditTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Link Appointment Types</DialogTitle>
                        <DialogDescription>
                            {editTarget?.begin_time} - {editTarget?.end_time}
                            {editTarget?.days ? ` (${editTarget.days.join(", ")})` : ""}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2 py-2">
                        {appointmentTypes.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                No appointment types configured. Create some first.
                            </p>
                        ) : (
                            <div className="border rounded-md max-h-64 overflow-y-auto">
                                {appointmentTypes.map((at) => (
                                    <label
                                        key={at.source_id}
                                        className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                        onClick={() => toggleTypeId(at.source_id)}
                                    >
                                        <Checkbox
                                            checked={editTypeIds.includes(at.source_id)}
                                            onCheckedChange={() => toggleTypeId(at.source_id)}
                                        />
                                        <span className="text-sm">{at.name}</span>
                                        {at.duration_minutes && (
                                            <span className="text-xs text-muted-foreground ml-auto">
                                                {at.duration_minutes} min
                                            </span>
                                        )}
                                    </label>
                                ))}
                            </div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setEditTarget(null)}>Cancel</Button>
                        <Button onClick={handleSave} disabled={saving}>
                            {saving ? "Saving..." : "Save"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
