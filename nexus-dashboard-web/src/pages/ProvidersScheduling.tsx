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
import type { CachedProvider, CachedAvailability, CachedAppointmentType, CachedOperatory } from "@/types"
import { Input } from "@/components/ui/input"
import {
    listProviders,
    listAvailabilities,
    listAppointmentTypes,
    listOperatories,
    createAvailability,
    updateAvailability,
    updateProvider,
    triggerSync,
} from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"

export default function ProvidersScheduling() {
    const { user } = useAuth()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [providers, setProviders] = useState<CachedProvider[]>([])
    const [availabilities, setAvailabilities] = useState<CachedAvailability[]>([])
    const [appointmentTypes, setAppointmentTypes] = useState<CachedAppointmentType[]>([])
    const [operatories, setOperatories] = useState<CachedOperatory[]>([])
    const [selectedProviderId, setSelectedProviderId] = useState<string>("")
    const [selectedApptTypeId, setSelectedApptTypeId] = useState<string>("all")
    const [loading, setLoading] = useState(true)
    const [loadingAvailabilities, setLoadingAvailabilities] = useState(false)
    const [syncing, setSyncing] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const initialLoadDone = useRef(false)

    // Edit target linking state
    const [editTarget, setEditTarget] = useState<CachedAvailability | null>(null)
    const [editTypeIds, setEditTypeIds] = useState<string[]>([])

    // Create new custom work window state
    const [createDialogOpen, setCreateDialogOpen] = useState(false)
    const [newWindow, setNewWindow] = useState({
        appointment_type_ids: [] as string[],
        operatory_id: "",
        days: [] as string[],
        start_time: "09:00",
        end_time: "17:00",
    })

    const [saving, setSaving] = useState(false)
    const [bufferMinutes, setBufferMinutes] = useState<number>(0)
    const [cutoffTime, setCutoffTime] = useState<string>("")
    const [minAge, setMinAge] = useState<number | "">("")
    const [maxAge, setMaxAge] = useState<number | "">("")
    const [savingSettings, setSavingSettings] = useState(false)

    // Load providers + appointment types once on mount
    const fetchData = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const [p, at, ops] = await Promise.all([
                listProviders(),
                listAppointmentTypes(),
                listOperatories(),
            ])
            setProviders(p)
            setAppointmentTypes(at)
            setOperatories(ops)
            // Auto-select first provider on initial load
            if (p.length > 0 && !initialLoadDone.current) {
                setSelectedProviderId(p.find(pr => pr.is_active)?.source_id || p[0].source_id)
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

    // Reset appointment type filter + sync settings when provider changes
    useEffect(() => {
        setSelectedApptTypeId("all")
        const p = providers.find((pr) => pr.source_id === selectedProviderId)
        setBufferMinutes(p?.buffer_minutes ?? 0)
        setCutoffTime(p?.same_day_cutoff_time ?? "")
        setMinAge(p?.min_age ?? "")
        setMaxAge(p?.max_age ?? "")
    }, [selectedProviderId, providers])

    const selectedProvider = providers.find((p) => p.source_id === selectedProviderId)

    const handleSync = async () => {
        if (!canManage) return
        setSyncing(true)
        try {
            const result = await triggerSync()
            if (result.success) {
                toast.success(
                    `Synced: ${result.providers_synced} providers, ${result.appointment_types_synced} appointment types`
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

    const handleSaveSettings = async () => {
        if (!canManage || !selectedProvider) return
        // Cross-validate age range
        if (minAge !== "" && maxAge !== "" && minAge > maxAge) {
            toast.error("Min age cannot be greater than max age")
            return
        }
        setSavingSettings(true)
        try {
            await updateProvider(selectedProvider.id, {
                buffer_minutes: bufferMinutes,
                same_day_cutoff_time: cutoffTime || null,
                min_age: minAge === "" ? null : minAge,
                max_age: maxAge === "" ? null : maxAge,
            })
            toast.success("Provider settings saved")
            await fetchData()
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to update settings"
            toast.error(message)
        } finally {
            setSavingSettings(false)
        }
    }

    const settingsChanged =
        bufferMinutes !== (selectedProvider?.buffer_minutes ?? 0) ||
        cutoffTime !== (selectedProvider?.same_day_cutoff_time ?? "") ||
        minAge !== (selectedProvider?.min_age ?? "") ||
        maxAge !== (selectedProvider?.max_age ?? "")

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

    const handleSaveEdit = async () => {
        if (!canManage) return
        if (!editTarget) return
        setSaving(true)
        try {
            await updateAvailability(editTarget.source_id, {
                appointment_type_ids: editTypeIds,
            })
            toast.success("Work window updated")
            setEditTarget(null)
            await fetchAvailabilities()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to update"
            toast.error(message)
        } finally {
            setSaving(false)
        }
    }

    const handleCreateWorkWindow = async () => {
        if (!canManage) return
        if (!selectedProviderId) return
        if (newWindow.appointment_type_ids.length === 0) {
            toast.error("Please select at least one appointment type")
            return
        }
        if (!newWindow.operatory_id) {
            toast.error("Please select an operatory")
            return
        }
        if (newWindow.days.length === 0) {
            toast.error("Please select at least one day")
            return
        }
        if (!newWindow.start_time || !newWindow.end_time) {
            toast.error("Please provide start and end times")
            return
        }

        setSaving(true)
        try {
            await createAvailability({
                provider_id: selectedProviderId,
                ...newWindow
            })
            toast.success("Work window created successfully")
            setCreateDialogOpen(false)
            // Reset form
            setNewWindow({
                appointment_type_ids: [],
                operatory_id: "",
                days: [],
                start_time: "09:00",
                end_time: "17:00",
            })
            await fetchAvailabilities()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to create work window"
            toast.error(message)
        } finally {
            setSaving(false)
        }
    }

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
        <div className="flex-1 space-y-4 bg-gradient-to-b from-background via-background to-accent/20 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Providers & Scheduling</h2>
                    <p className="text-muted-foreground">
                        Link appointment types to provider availabilities so your scheduling engine can generate bookable slots.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    {canManage && (
                        <>
                            <Button variant="default" onClick={() => setCreateDialogOpen(true)} disabled={loading || !selectedProviderId}>
                                Create Work Window
                            </Button>
                            <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                                <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                            </Button>
                        </>
                    )}
                </div>
            </div>

            {error && (
                <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                        {error}. Please try refreshing the page{canManage ? " or click Sync." : "."}
                    </AlertDescription>
                </Alert>
            )}

            {unlinkedCount > 0 && !loading && !error && (
                <Alert className="flex items-center gap-2 border-indigo-500/40 border-dotted bg-[rgb(255,244,227)] text-indigo-700 [&>svg]:static [&>svg]:left-auto [&>svg]:top-auto [&>svg]:translate-y-0 [&>svg+div]:translate-y-0 [&>svg~*]:pl-0 dark:bg-[rgb(255,244,227)]/10 dark:text-indigo-300">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <AlertDescription className="m-0 leading-5">
                        {unlinkedCount} work window{unlinkedCount !== 1 ? "s" : ""} without linked
                        appointment types. These won't generate bookable slots.
                    </AlertDescription>
                </Alert>
            )}

            {loading ? (
                <div className="flex justify-center py-8 text-muted-foreground">Loading...</div>
            ) : providers.length === 0 ? (
                <Card>
                    <CardContent className="py-8 text-center text-muted-foreground">
                        <p>
                            {canManage
                                ? 'No providers found. Click "Sync" to fetch from your PMS.'
                                : "No providers found for your location."}
                        </p>
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

                    {/* Provider Scheduling Rules */}
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base">Scheduling Rules</CardTitle>
                            <CardDescription>
                                Configure booking restrictions for this provider.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {/* Buffer Time */}
                            <div className="space-y-1">
                                <label className="text-sm font-medium">Booking Buffer Time</label>
                                <p className="text-xs text-muted-foreground">
                                    Minimum lead time before a slot can be booked. Slots within this window from now are hidden.
                                </p>
                                <div className="flex items-center gap-3 pt-1">
                                    <Clock className="h-4 w-4 text-muted-foreground shrink-0" />
                                    <Input
                                        type="number"
                                        min={0}
                                        max={1440}
                                        value={bufferMinutes}
                                        onChange={(e) => setBufferMinutes(Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
                                        className="w-24"
                                        disabled={!canManage}
                                    />
                                    <span className="text-sm text-muted-foreground">minutes</span>
                                </div>
                            </div>

                            {/* Same-Day Cutoff */}
                            <div className="space-y-1">
                                <label className="text-sm font-medium">Same-Day Cutoff Time</label>
                                <p className="text-xs text-muted-foreground">
                                    If no appointments are booked for this provider by this time, all remaining same-day slots are hidden.
                                    Leave empty to disable.
                                </p>
                                <div className="flex items-center gap-3 pt-1">
                                    <Calendar className="h-4 w-4 text-muted-foreground shrink-0" />
                                    <input
                                        type="time"
                                        value={cutoffTime}
                                        onChange={(e) => setCutoffTime(e.target.value)}
                                        className="flex h-10 w-32 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                                        disabled={!canManage}
                                    />
                                    {cutoffTime && canManage && (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => setCutoffTime("")}
                                            className="text-muted-foreground"
                                        >
                                            Clear
                                        </Button>
                                    )}
                                </div>
                            </div>

                            {/* Age Group */}
                            <div className="space-y-1">
                                <label className="text-sm font-medium">Patient Age Group</label>
                                <p className="text-xs text-muted-foreground">
                                    Restrict this provider to patients within a specific age range.
                                    Leave empty for no restriction.
                                </p>
                                <div className="flex items-center gap-3 pt-1">
                                    <Input
                                        type="number"
                                        min={0}
                                        max={150}
                                        placeholder="Min"
                                        value={minAge}
                                        onChange={(e) => setMinAge(e.target.value === "" ? "" : Math.max(0, Math.min(150, Number(e.target.value) || 0)))}
                                        className="w-20"
                                        disabled={!canManage}
                                    />
                                    <span className="text-sm text-muted-foreground">to</span>
                                    <Input
                                        type="number"
                                        min={0}
                                        max={150}
                                        placeholder="Max"
                                        value={maxAge}
                                        onChange={(e) => setMaxAge(e.target.value === "" ? "" : Math.max(0, Math.min(150, Number(e.target.value) || 0)))}
                                        className="w-20"
                                        disabled={!canManage}
                                    />
                                    <span className="text-sm text-muted-foreground">years</span>
                                </div>
                            </div>

                            {canManage && (
                                <Button
                                    size="sm"
                                    onClick={handleSaveSettings}
                                    disabled={savingSettings || !settingsChanged}
                                >
                                    {savingSettings ? "Saving..." : "Save Settings"}
                                </Button>
                            )}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>
                                Work Windows for {selectedProvider?.name || `${selectedProvider?.first_name} ${selectedProvider?.last_name}`}
                            </CardTitle>
                            <CardDescription>
                                {filteredAvailabilities.length} schedule{filteredAvailabilities.length !== 1 ? "s" : ""} found
                                {selectedApptTypeId !== "all" ? " (filtered)" : ""}.
                                {canManage
                                    ? ' Click "Edit Linking" to associate appointment types, or create a custom Work Window.'
                                    : " Read-only view."}
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            {loadingAvailabilities ? (
                                <div className="flex justify-center py-6 text-muted-foreground">Loading work windows...</div>
                            ) : filteredAvailabilities.length === 0 ? (
                                <p className="text-center py-6 text-muted-foreground">
                                    {selectedApptTypeId !== "all"
                                        ? "No work windows match this appointment type."
                                        : canManage
                                            ? "No work windows found for this provider. Add one above."
                                            : "No work windows found for this provider."}
                                </p>
                            ) : (
                                <div className="space-y-3">
                                    {filteredAvailabilities.map((av) => {
                                        const hasTypes = av.appointment_type_ids && av.appointment_type_ids.length > 0
                                        const isPastDate = !!av.specific_date && av.specific_date < new Date().toISOString().slice(0, 10)
                                        const isWarning = !hasTypes && !isPastDate

                                        const mutedClass = isWarning ? "text-indigo-500 dark:text-indigo-300" : "text-muted-foreground"
                                        const normalClass = isWarning ? "text-indigo-700 dark:text-indigo-200" : ""

                                        return (
                                            <div
                                                key={av.id}
                                                className={`rounded-lg border p-4 transition-colors ${isPastDate
                                                        ? "border-border/40 bg-muted/20 opacity-50"
                                                        : isWarning
                                                            ? "border-indigo-500/40 border-dotted bg-[rgb(255,244,227)] dark:bg-[rgb(255,244,227)]/10"
                                                            : "border-primary/20 bg-background/70 hover:border-primary/35"
                                                    }`}
                                            >
                                                <div className="flex items-start justify-between">
                                                    <div className="space-y-1">
                                                        <div className="flex items-center gap-2">
                                                            <Clock className={`h-4 w-4 ${mutedClass}`} />
                                                            <span className={`font-medium ${normalClass}`}>
                                                                {av.begin_time} - {av.end_time}
                                                            </span>
                                                            {isPastDate && (
                                                                <Badge variant="outline" className="text-xs text-muted-foreground/60 border-border/40">
                                                                    Expired
                                                                </Badge>
                                                            )}
                                                            {av.synced && (
                                                                <Badge
                                                                    variant={isWarning ? "outline" : "secondary"}
                                                                    className={`text-xs ${isWarning
                                                                            ? "border-indigo-500/40 text-indigo-700 dark:text-indigo-300 bg-indigo-500/10"
                                                                            : ""
                                                                        }`}
                                                                >
                                                                    Synced from PMS
                                                                </Badge>
                                                            )}
                                                            {!av.synced && (
                                                                <Badge
                                                                    variant="outline"
                                                                    className={`text-xs ${isWarning ? "border-indigo-500/40 text-indigo-700 dark:text-indigo-300" : ""
                                                                        }`}
                                                                >
                                                                    Manual
                                                                </Badge>
                                                            )}
                                                        </div>
                                                        {av.days && av.days.length > 0 && (
                                                            <div className={`flex items-center gap-1.5 text-sm ${mutedClass}`}>
                                                                <Calendar className="h-3 w-3" />
                                                                {av.days.join(", ")}
                                                            </div>
                                                        )}
                                                        {av.specific_date && (
                                                            <div className={`flex items-center gap-1.5 text-sm ${mutedClass}`}>
                                                                <Calendar className="h-3 w-3" />
                                                                Specific date: {av.specific_date}
                                                            </div>
                                                        )}
                                                        <div className={`text-sm ${normalClass}`}>
                                                            <span className={mutedClass}>Appointment Types: </span>
                                                            {hasTypes ? (
                                                                <span>{av.appointment_type_names?.join(", ")}</span>
                                                            ) : (
                                                                <span className="text-indigo-700 dark:text-indigo-300 font-medium">
                                                                    None linked
                                                                </span>
                                                            )}
                                                        </div>
                                                    </div>
                                                    {canManage && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            className={isWarning ? "border-indigo-500/40 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-500/10 shrink-0" : "shrink-0"}
                                                            onClick={() => openEditDialog(av)}
                                                        >
                                                            Edit Linking
                                                        </Button>
                                                    )}
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

            {canManage && (
                <>
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
                                <Button onClick={handleSaveEdit} disabled={saving}>
                                    {saving ? "Saving..." : "Save"}
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>

                    {/* Create Work Window Dialog */}
                    <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
                        <DialogContent className="max-w-md">
                            <DialogHeader>
                                <DialogTitle>Create Custom Work Window</DialogTitle>
                                <DialogDescription>
                                    Create a manual schedule block. This will not be pushed back to your PMS, but it will be used to offer booking slots.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-4 py-2">
                                {/* Time */}
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Start Time</label>
                                        <input
                                            type="time"
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                                            value={newWindow.start_time}
                                            onChange={(e) => setNewWindow({ ...newWindow, start_time: e.target.value })}
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">End Time</label>
                                        <input
                                            type="time"
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                                            value={newWindow.end_time}
                                            onChange={(e) => setNewWindow({ ...newWindow, end_time: e.target.value })}
                                        />
                                    </div>
                                </div>

                                {/* Days */}
                                <div className="space-y-2 pt-2">
                                    <label className="text-sm font-medium">Days</label>
                                    <div className="grid grid-cols-4 gap-2">
                                        {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day) => (
                                            <label key={day} className="flex items-center space-x-2 text-sm">
                                                <Checkbox
                                                    checked={newWindow.days.includes(day)}
                                                    onCheckedChange={(checked) => {
                                                        setNewWindow(prev => ({
                                                            ...prev,
                                                            days: checked
                                                                ? [...prev.days, day]
                                                                : prev.days.filter(d => d !== day)
                                                        }))
                                                    }}
                                                />
                                                <span>{day.substring(0, 3)}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>

                                {/* Operatory */}
                                <div className="space-y-2 pt-2">
                                    <label className="text-sm font-medium">Operatory</label>
                                    <Select value={newWindow.operatory_id} onValueChange={(v) => setNewWindow({ ...newWindow, operatory_id: v })}>
                                        <SelectTrigger>
                                            <SelectValue placeholder="Select Operatory" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {operatories.map((op) => (
                                                <SelectItem key={op.source_id} value={op.source_id}>
                                                    {op.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                {/* Appointment Types */}
                                <div className="space-y-2 pt-2">
                                    <label className="text-sm font-medium">Appointment Types</label>
                                    <div className="border rounded-md max-h-40 overflow-y-auto">
                                        {appointmentTypes.map((at) => (
                                            <label
                                                key={at.source_id}
                                                className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                            >
                                                <Checkbox
                                                    checked={newWindow.appointment_type_ids.includes(at.source_id)}
                                                    onCheckedChange={(checked) => {
                                                        setNewWindow(prev => ({
                                                            ...prev,
                                                            appointment_type_ids: checked
                                                                ? [...prev.appointment_type_ids, at.source_id]
                                                                : prev.appointment_type_ids.filter(id => id !== at.source_id)
                                                        }))
                                                    }}
                                                />
                                                <span className="text-sm truncate" title={at.name}>{at.name}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
                                <Button onClick={handleCreateWorkWindow} disabled={saving}>
                                    {saving ? "Creating..." : "Create Work Window"}
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                </>
            )}
        </div>
    )
}
