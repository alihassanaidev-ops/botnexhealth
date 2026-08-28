import { useEffect, useState, useCallback, useMemo, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Calendar as CalendarPicker } from "@/components/ui/calendar"
import { Progress } from "@/components/ui/progress"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toast } from "sonner"
import { addDays, differenceInCalendarDays, format, startOfDay } from "date-fns"
import type { DateRange } from "react-day-picker"
import { RefreshCcw, AlertTriangle, Clock, Calendar, CalendarDays, MapPin, UserCog, ChevronLeft, ChevronRight, Repeat } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import type { CachedProvider, CachedAvailability, CachedAppointmentType, CachedOperatory } from "@/types"
import { Input } from "@/components/ui/input"
import {
    getSetupOverview,
    listProviders,
    listAvailabilities,
    listAppointmentTypes,
    listOperatories,
    createAvailability,
    updateAvailability,
    clearAvailabilityOverride,
    previewBulkLinkRange,
    applyBulkLinkRange,
    updateProvider,
    triggerSync,
} from "@/lib/tenant-api"
import { useAuth } from "@/context/AuthContext"
import { useSelectedLocationId, useLocationContext } from "@/context/LocationContext"
import SchedulerCalendar from "@/components/scheduling/SchedulerCalendar"
import { UpcomingRangePicker } from "@/components/scheduling/UpcomingRangePicker"
import {
    byDateThenTime,
    defaultRange,
    isExpired,
    todayISO,
    isActive,
    isBookableWindow,
    isRecurring,
    matchesRange,
    type UpcomingRange,
} from "@/lib/availability-filter"

/** Dated work windows per page. Matches the Patients table's page size. */
const PAGE_SIZE = 25

const ISO_DATE = "yyyy-MM-dd"
/** Bulk range linking is capped server-side; `today + 14` spans 15 days inclusive. */
const BULK_RANGE_MAX_DAYS = 15
/** Range the picker opens with — matches the old fixed "next week" behaviour. */
const BULK_RANGE_DEFAULT_DAYS = 7

interface BulkProgress {
    batch: number
    batches: number
    done: number
    total: number
}

export default function ProvidersScheduling() {
    const { user } = useAuth()
    const locationId = useSelectedLocationId()
    const { selectedLocation } = useLocationContext()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [providers, setProviders] = useState<CachedProvider[]>([])
    const [availabilities, setAvailabilities] = useState<CachedAvailability[]>([])
    const [appointmentTypes, setAppointmentTypes] = useState<CachedAppointmentType[]>([])
    const [operatories, setOperatories] = useState<CachedOperatory[]>([])
    const [selectedProviderId, setSelectedProviderId] = useState<string>("")
    const [selectedApptTypeId, setSelectedApptTypeId] = useState<string>("all")
    const [selectedOperatoryId, setSelectedOperatoryId] = useState<string>("all")
    const [showExpired, setShowExpired] = useState(false)
    const [showRecurring, setShowRecurring] = useState(false)
    // Opens on the coming week: that's what a front-desk operator is working on,
    // and it keeps the default view to a page or two instead of thousands of
    // pre-expanded rows. Wider presets are one click away in the picker.
    const [dateRange, setDateRange] = useState<UpcomingRange>(() => defaultRange())
    const [page, setPage] = useState(0)
    const [view, setView] = useState<"list" | "calendar">("list")
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
    const [canLinkAvailability, setCanLinkAvailability] = useState(false)
    const [pmsSource, setPmsSource] = useState<string | null>(null)
    const [canCreateWorkWindows, setCanCreateWorkWindows] = useState(false)
    const [canClearWorkingWindowOverride, setCanClearWorkingWindowOverride] = useState(false)
    // NexHealth returns PMS notes and lunch breaks in the same collection as
    // real working windows. Only v3 labels them, so on v2 every row reports as
    // bookable and this toggle is inert. Shown by default: seeing "Lunch" on a
    // row is what tells an operator it is not bookable time.
    const [showNonBookable, setShowNonBookable] = useState(true)
    const [bulkDialogOpen, setBulkDialogOpen] = useState(false)
    const [bulkTypeIds, setBulkTypeIds] = useState<string[]>([])
    const [bulkOperatoryIds, setBulkOperatoryIds] = useState<string[]>([])
    const bulkRangeMin = useMemo(() => startOfDay(new Date()), [])
    const bulkRangeMax = useMemo(
        () => addDays(bulkRangeMin, BULK_RANGE_MAX_DAYS - 1),
        [bulkRangeMin],
    )
    const [bulkRange, setBulkRange] = useState<DateRange | undefined>(() => ({
        from: startOfDay(new Date()),
        to: addDays(startOfDay(new Date()), BULK_RANGE_DEFAULT_DAYS - 1),
    }))
    const [bulkRunning, setBulkRunning] = useState(false)
    const [bulkProgress, setBulkProgress] = useState<BulkProgress | null>(null)
    const [bulkPauseRemaining, setBulkPauseRemaining] = useState(0)
    // Flipped on unmount (or a cancel) so the batch loop stops between batches
    // instead of firing more PMS writes into a dead component.
    const bulkCancelledRef = useRef(false)

    // Load providers + appointment types once on mount
    const fetchData = useCallback(async () => {
        if (!locationId) return
        setLoading(true)
        setError(null)
        try {
            const [overview, p, at, ops] = await Promise.all([
                getSetupOverview(locationId),
                listProviders(locationId),
                listAppointmentTypes(locationId),
                listOperatories(locationId),
            ])
            setCanLinkAvailability(overview.can_link_availability)
            setPmsSource(overview.pms_source)
            setCanCreateWorkWindows(overview.can_create_work_windows)
            setCanClearWorkingWindowOverride(overview.can_clear_working_window_override)
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
    }, [locationId])

    // Fetch availabilities when provider changes
    const fetchAvailabilities = useCallback(async () => {
        if (!selectedProviderId || !locationId) return
        setLoadingAvailabilities(true)
        setAvailabilities([])
        try {
            const data = await listAvailabilities(locationId, selectedProviderId, {
                includeClosed: pmsSource === "gotracker",
            })
            setAvailabilities(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load availabilities"
            toast.error(message)
        } finally {
            setLoadingAvailabilities(false)
        }
    }, [selectedProviderId, locationId, pmsSource])

    useEffect(() => {
        fetchData()
    }, [fetchData])

    useEffect(() => {
        fetchAvailabilities()
    }, [fetchAvailabilities])

    // Stop the throttled batch loop if the page goes away mid-run.
    useEffect(() => () => { bulkCancelledRef.current = true }, [])

    // Reset appointment type/operatory filters + sync settings when provider changes
    useEffect(() => {
        setSelectedApptTypeId("all")
        setSelectedOperatoryId("all")
        const p = providers.find((pr) => pr.source_id === selectedProviderId)
        setBufferMinutes(p?.buffer_minutes ?? 0)
        setCutoffTime(p?.same_day_cutoff_time ?? "")
        setMinAge(p?.min_age ?? "")
        setMaxAge(p?.max_age ?? "")
    }, [selectedProviderId, providers])

    const selectedProvider = providers.find((p) => p.source_id === selectedProviderId)

    const bulkRangeDayCount =
        bulkRange?.from && bulkRange?.to
            ? differenceInCalendarDays(bulkRange.to, bulkRange.from) + 1
            : 0
    const bulkRangeLabel = bulkRangeDayCount
        ? `${format(bulkRange!.from!, "MMM d")} - ${format(bulkRange!.to!, "MMM d, yyyy")} (${bulkRangeDayCount} day${bulkRangeDayCount === 1 ? "" : "s"})`
        : "Pick a start and end day"

    const handleSync = async () => {
        if (!canManage || !locationId) return
        setSyncing(true)
        try {
            const result = await triggerSync(locationId)
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
            const updated = await updateProvider(selectedProvider.id, {
                buffer_minutes: bufferMinutes,
                same_day_cutoff_time: cutoffTime || null,
                min_age: minAge === "" ? null : minAge,
                max_age: maxAge === "" ? null : maxAge,
            }, locationId)
            // Merge the server-confirmed provider instead of refetching every
            // provider/type/operatory — the PATCH already returns the fresh row.
            setProviders((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
            toast.success("Provider settings saved")
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

    const toggleBulkTypeId = (typeId: string) => {
        setBulkTypeIds((prev) =>
            prev.includes(typeId)
                ? prev.filter((id) => id !== typeId)
                : [...prev, typeId]
        )
    }

    const toggleBulkOperatoryId = (operatoryId: string) => {
        setBulkOperatoryIds((prev) =>
            prev.includes(operatoryId)
                ? prev.filter((id) => id !== operatoryId)
                : [...prev, operatoryId]
        )
    }

    // Idle wait between write batches, surfaced as a live countdown so the
    // admin can see the run is pacing itself rather than stalled.
    const pauseBetweenBatches = (seconds: number) =>
        new Promise<void>((resolve) => {
            let remaining = seconds
            setBulkPauseRemaining(remaining)
            const timer = window.setInterval(() => {
                remaining -= 1
                if (remaining <= 0 || bulkCancelledRef.current) {
                    window.clearInterval(timer)
                    setBulkPauseRemaining(0)
                    resolve()
                    return
                }
                setBulkPauseRemaining(remaining)
            }, 1000)
        })

    const handleBulkLinkRange = async () => {
        if (!canManage || !selectedProviderId || !locationId) return
        if (bulkTypeIds.length === 0) {
            toast.error("Please select at least one appointment type")
            return
        }
        if (bulkOperatoryIds.length === 0) {
            toast.error("Please select at least one operatory")
            return
        }
        if (!bulkRange?.from || !bulkRange?.to) {
            toast.error("Please select a date range")
            return
        }

        bulkCancelledRef.current = false
        setBulkRunning(true)
        setBulkProgress(null)
        setBulkPauseRemaining(0)
        try {
            // One read for the whole range; the batches below only write, so a
            // wide range costs the PMS quota a single listing call.
            const preview = await previewBulkLinkRange({
                provider_id: selectedProviderId,
                start_date: format(bulkRange.from, ISO_DATE),
                end_date: format(bulkRange.to, ISO_DATE),
                operatory_ids: bulkOperatoryIds,
            }, locationId)

            const ids = preview.windows.map((w) => w.source_id).filter(Boolean)
            if (ids.length === 0) {
                toast.warning("No dated work windows in that range matched the selected provider and operatories")
                return
            }

            const batches: string[][] = []
            for (let i = 0; i < ids.length; i += preview.batch_size) {
                batches.push(ids.slice(i, i + preview.batch_size))
            }

            let updated = 0
            const errors: string[] = []
            for (let i = 0; i < batches.length; i++) {
                if (bulkCancelledRef.current) break
                setBulkProgress({ batch: i + 1, batches: batches.length, done: updated, total: ids.length })
                const result = await applyBulkLinkRange({
                    availability_ids: batches[i],
                    appointment_type_ids: bulkTypeIds,
                }, locationId)
                updated += result.updated_count
                errors.push(...result.errors)
                setBulkProgress({ batch: i + 1, batches: batches.length, done: updated, total: ids.length })
                if (i < batches.length - 1 && !bulkCancelledRef.current) {
                    await pauseBetweenBatches(preview.batch_pause_seconds)
                }
            }

            if (bulkCancelledRef.current) return

            if (updated > 0) {
                toast.success(
                    `Linked ${updated} work window${updated === 1 ? "" : "s"} across ` +
                    `${preview.day_count} day${preview.day_count === 1 ? "" : "s"}`
                )
            }
            if (errors.length > 0) {
                toast.error(`${errors.length} work window${errors.length === 1 ? "" : "s"} failed to update`)
            }
            setBulkDialogOpen(false)
            setBulkTypeIds([])
            setBulkOperatoryIds([])
            await fetchAvailabilities()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to link the selected date range"
            toast.error(message)
        } finally {
            setBulkRunning(false)
            setBulkProgress(null)
            setBulkPauseRemaining(0)
        }
    }

    const handleSaveEdit = async () => {
        if (!canManage) return
        if (!editTarget) return
        setSaving(true)
        try {
            const updated = await updateAvailability(editTarget.source_id, {
                appointment_type_ids: editTypeIds,
            }, locationId)
            // Merge the server-confirmed row in place rather than refetching the
            // provider's entire (potentially thousands-of-rows) availability set.
            // NexHealth's PATCH may not echo appointment-type *names*, so resolve
            // them locally from the already-loaded appointment type list.
            const nameBySourceId = new Map(appointmentTypes.map((at) => [at.source_id, at.name]))
            // Only the linked types changed. The PATCH response is a bare NexHealth
            // availability (no synthesized provider_name, sometimes no operatory/times),
            // so keep the known-good row and override only the type fields — spreading
            // `updated` would blank those fields until the next full refetch.
            const typeIds = updated.appointment_type_ids ?? editTypeIds
            const merged: CachedAvailability = {
                ...editTarget,
                appointment_type_ids: typeIds,
                appointment_type_names: typeIds.map((id) => nameBySourceId.get(id) ?? id),
                types_overridden: updated.types_overridden,
            }
            setAvailabilities((prev) =>
                prev.map((a) => (a.source_id === merged.source_id ? merged : a))
            )
            toast.success("Work window updated")
            setEditTarget(null)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to update"
            toast.error(message)
        } finally {
            setSaving(false)
        }
    }

    const handleClearEditOverride = async () => {
        if (!canManage || !editTarget) return
        setSaving(true)
        try {
            const updated = await clearAvailabilityOverride(editTarget.source_id, locationId)
            const nameBySourceId = new Map(appointmentTypes.map((at) => [at.source_id, at.name]))
            const typeIds = updated.appointment_type_ids ?? []
            const merged: CachedAvailability = {
                ...editTarget,
                appointment_type_ids: typeIds,
                appointment_type_names: typeIds.map((id) => nameBySourceId.get(id) ?? id),
                types_overridden: updated.types_overridden,
            }
            setAvailabilities((prev) =>
                prev.map((availability) =>
                    availability.source_id === merged.source_id ? merged : availability
                )
            )
            toast.success("Restored the PMS appointment-type links")
            setEditTarget(null)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to restore PMS links"
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
            const created = await createAvailability({
                provider_id: selectedProviderId,
                ...newWindow
            }, locationId)
            // Append the created row instead of refetching everything. Resolve
            // type names locally and keep the chosen operatory id for display
            // (operatory name is resolved from the operatories list at render).
            const nameBySourceId = new Map(appointmentTypes.map((at) => [at.source_id, at.name]))
            const typeIds = created.appointment_type_ids ?? newWindow.appointment_type_ids
            const enriched: CachedAvailability = {
                ...created,
                id: created.id || created.source_id,
                operatory_source_id: created.operatory_source_id ?? newWindow.operatory_id,
                appointment_type_ids: typeIds,
                appointment_type_names: typeIds.map((id) => nameBySourceId.get(id) ?? id),
            }
            setAvailabilities((prev) => [...prev, enriched])
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
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to create work window"
            toast.error(message)
        } finally {
            setSaving(false)
        }
    }

    // Use local date (browser TZ) — the user is at the practice.
    const todayLocal = todayISO()
    // useCallback so the memoised derivations below actually memoise: a fresh
    // function identity each render defeated them entirely.
    const isAvailabilityExpired = useCallback(
        (av: CachedAvailability) => isExpired(av, todayLocal),
        [todayLocal]
    )

    // Filter availabilities by selected appointment type (and expired state
    // unless showExpired is on), then sort by date.
    // NexHealth returns rows in insertion order, which renders as random
    // dates from the operator's perspective — easy to mis-link an
    // appointment type to a far-future row instead of the soonest one.
    // Sort: specific_date ascending, then begin_time. Rows without a
    // specific_date (pure recurring rules) sort first.
    // Memoised: this used to recompute (filter + sort over the provider's whole
    // availability set) on every render, including every keystroke in the
    // Scheduling Rules inputs above.
    const visibleAvailabilities = useMemo(
        () =>
            availabilities
                .filter(isActive)
                // PMS notes and lunch breaks arrive in the same collection as
                // real working windows; only v3 labels them, so on v2 this is
                // a no-op.
                .filter((av) => showNonBookable || isBookableWindow(av))
                .filter((av) => showRecurring || !isRecurring(av))
                .filter((av) => showExpired || matchesRange(av, dateRange))
                .filter(
                    (av) =>
                        !canLinkAvailability ||
                        selectedApptTypeId === "all" ||
                        av.appointment_type_ids?.includes(selectedApptTypeId)
                )
                .filter(
                    (av) =>
                        selectedOperatoryId === "all" ||
                        av.operatory_source_id === selectedOperatoryId
                ),
        [
            availabilities, showExpired, showRecurring, dateRange, showNonBookable,
            canLinkAvailability, selectedApptTypeId, selectedOperatoryId,
        ]
    )

    // Recurring rules are pinned above the paginated list rather than sorted
    // into it. Sorted in, they'd take the top of page 1 and push the dated rows
    // the operator is filtering for onto page 2 — which reads as "the filter did
    // nothing".
    const recurringWindows = useMemo(
        () => visibleAvailabilities.filter(isRecurring),
        [visibleAvailabilities]
    )
    const datedWindows = useMemo(
        () => visibleAvailabilities.filter((av) => !isRecurring(av)).sort(byDateThenTime),
        [visibleAvailabilities]
    )

    const pageCount = Math.max(1, Math.ceil(datedWindows.length / PAGE_SIZE))
    const pagedWindows = useMemo(
        () => datedWindows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
        [datedWindows, page]
    )
    // Group the visible page by date so each day gets its own heading — a flat
    // run of rows reads as one long list with the dates buried inside each card.
    const pagedGroups = useMemo(() => {
        const groups: { date: string; rows: CachedAvailability[] }[] = []
        for (const av of pagedWindows) {
            const key = av.specific_date ?? ""
            const last = groups[groups.length - 1]
            if (last && last.date === key) last.rows.push(av)
            else groups.push({ date: key, rows: [av] })
        }
        return groups
    }, [pagedWindows])

    const rangeFrom = datedWindows.length === 0 ? 0 : page * PAGE_SIZE + 1
    const rangeTo = Math.min((page + 1) * PAGE_SIZE, datedWindows.length)
    const totalShown = recurringWindows.length + datedWindows.length

    // Kept for the row markup and the existing empty-state copy.
    const filteredAvailabilities = visibleAvailabilities

    // Narrowing a filter while on page 4 would otherwise strand the operator on
    // an out-of-range page showing nothing.
    useEffect(() => {
        setPage(0)
    }, [
        selectedProviderId, selectedApptTypeId, selectedOperatoryId,
        dateRange, showNonBookable, showExpired, showRecurring,
    ])

    // These toggles widen rather than narrow from their defaults, so they don't
    // count toward the "(filtered)" label or the Clear button.
    const hasNarrowingFilter =
        selectedApptTypeId !== "all" ||
        selectedOperatoryId !== "all" ||
        dateRange.endDate !== null

    const resetFilters = () => {
        setSelectedApptTypeId("all")
        setSelectedOperatoryId("all")
        setDateRange(defaultRange())
    }

    const unlinkedCount = useMemo(
        () =>
            canLinkAvailability
                ? availabilities.filter(
                    (av) =>
                        // An inactive window generates no slots, and a note or
                        // break has no appointment type to link — warning about
                        // either sends the operator chasing something unfixable.
                        isActive(av) &&
                        isBookableWindow(av) &&
                        !isAvailabilityExpired(av) &&
                        (!av.appointment_type_ids || av.appointment_type_ids.length === 0)
                ).length
                : 0,
        [availabilities, canLinkAvailability, isAvailabilityExpired]
    )

    // Collect appointment types that appear in this provider's availabilities
    const availableApptTypeIds = new Set(availabilities.flatMap((av) => av.appointment_type_ids || []))
    const relevantApptTypes = canLinkAvailability
        ? appointmentTypes.filter((at) => availableApptTypeIds.has(at.source_id))
        : appointmentTypes

    // Collect operatories that appear in this provider's availabilities.
    // Names alone can collide (e.g. two rooms both named "DR. KADRI"), so the
    // filter label always includes the ID to disambiguate.
    const availableOperatoryIds = new Set(
        availabilities.map((av) => av.operatory_source_id).filter((id): id is string => !!id)
    )
    const relevantOperatories = operatories.filter((op) => availableOperatoryIds.has(op.source_id))

    // NexHealth doesn't embed an operatory name on the availability itself (only
    // operatory_id), so resolve the display name from the operatories list by
    // source_id. Names can collide, so rows still show the ID alongside.
    const operatoryNameBySourceId = new Map(operatories.map((op) => [op.source_id, op.name]))
    const appointmentTypeNameBySourceId = new Map(
        appointmentTypes.map((appointmentType) => [appointmentType.source_id, appointmentType.name])
    )

    const allBulkOperatoriesSelected =
        relevantOperatories.length > 0 &&
        relevantOperatories.every((op) => bulkOperatoryIds.includes(op.source_id))

    const bulkOperatoryLabel =
        bulkOperatoryIds.length === 0
            ? "None selected"
            : allBulkOperatoriesSelected
                ? "All visible operatories"
                : bulkOperatoryIds.length === 1
                    ? operatoryNameBySourceId.get(bulkOperatoryIds[0]) ?? bulkOperatoryIds[0]
                    : `${bulkOperatoryIds.length} operatories selected`

    const openBulkDialog = () => {
        const visibleIds = relevantOperatories.map((op) => op.source_id)
        setBulkOperatoryIds(
            selectedOperatoryId !== "all" && visibleIds.includes(selectedOperatoryId)
                ? [selectedOperatoryId]
                : visibleIds
        )
        setBulkDialogOpen(true)
    }

    // One row, rendered by both the recurring section and the paginated
    // dated list, so the two cannot drift apart visually.
    const renderWindow = (av: CachedAvailability) => {
        const isClosed = av.status === "closed"
        const hasTypes = av.appointment_type_ids && av.appointment_type_ids.length > 0
        const appointmentTypeNames = (av.appointment_type_ids || []).map(
            (id) => appointmentTypeNameBySourceId.get(id) ?? id
        )
        const isPastDate = isAvailabilityExpired(av)
        const isWarning = canLinkAvailability && !isClosed && !hasTypes && !isPastDate

        const mutedClass = isClosed
            ? "text-slate-500 dark:text-slate-400"
            : isWarning ? "text-indigo-500 dark:text-indigo-300" : "text-muted-foreground"
        const normalClass = isClosed ? "text-slate-700 dark:text-slate-300" : isWarning ? "text-indigo-700 dark:text-indigo-200" : ""

        return (
            <div
                key={av.id}
                className={`rounded-lg border p-4 transition-colors ${isClosed
                        ? "border-slate-400/40 border-dashed bg-slate-500/5"
                        : isPastDate
                        ? "border-border/40 bg-muted/20 opacity-50"
                        : isWarning
                            ? "border-indigo-500/40 border-dotted bg-[rgb(255,244,227)] dark:bg-[rgb(255,244,227)]/10"
                            : "border-border bg-background/70 hover:border-border"
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
                            {isClosed && (
                                <Badge
                                    variant="outline"
                                    className="text-xs border-slate-500/50 text-slate-600 dark:text-slate-300"
                                    title="Derived from the gaps between PMS working windows; this period cannot be edited"
                                >
                                    Closed — read-only
                                </Badge>
                            )}
                            {av.label_name && (
                            <Badge
                                variant="outline"
                                className="text-xs border-amber-500/50 text-amber-700 dark:text-amber-300"
                                title="Not bookable time — this row describes the schedule rather than offering appointments"
                            >
                                {av.label_name}
                            </Badge>
                        )}
                        {!isClosed && av.synced && (
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
                            {!isClosed && !av.synced && (
                                <Badge
                                    variant="outline"
                                    className={`text-xs ${isWarning ? "border-indigo-500/40 text-indigo-700 dark:text-indigo-300" : ""
                                        }`}
                                >
                                    Manual
                                </Badge>
                            )}
                            {av.types_overridden && (
                                <Badge variant="outline" className="text-xs border-violet-500/50 text-violet-700 dark:text-violet-300">
                                    Type override
                                </Badge>
                            )}
                        </div>
                        {av.operatory_source_id && (
                            <div className={`flex items-center gap-1.5 text-sm ${mutedClass}`}>
                                <MapPin className="h-3 w-3" />
                                Operatory: {operatoryNameBySourceId.get(av.operatory_source_id) ?? av.operatory_name ?? "Unknown"}
                                <span className="opacity-60">({av.operatory_source_id})</span>
                            </div>
                        )}
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
                        {canLinkAvailability && !isClosed && (
                            <div className={`text-sm ${normalClass}`}>
                                <span className={mutedClass}>Appointment Types: </span>
                                {hasTypes ? (
                                    <span>{appointmentTypeNames.join(", ")}</span>
                                ) : (
                                    <span className="text-indigo-700 dark:text-indigo-300 font-medium">
                                        None linked
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                    {canManage && canLinkAvailability && !isClosed && (
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
    }

    return (
        <div className="relative flex-1 space-y-4 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <PageHeader
                icon={UserCog}
                title="Providers & Scheduling"
                description={
                    canLinkAvailability
                        ? "Link appointment types to provider availabilities so your scheduling engine can generate bookable slots."
                        : "Review live bookable slots from your PMS and configure provider scheduling rules."
                }
                actions={
                    <>
                        <div className="inline-flex overflow-hidden rounded-md border">
                            {(["calendar", "list"] as const).map((v) => (
                                <button
                                    key={v}
                                    onClick={() => setView(v)}
                                    className={`px-3 py-1.5 text-xs capitalize ${view === v ? "bg-primary text-primary-foreground font-medium" : "bg-background text-muted-foreground hover:text-foreground"}`}
                                >
                                    {v}
                                </button>
                            ))}
                        </div>
                        {canManage && view === "list" && (
                            <>
                                {canLinkAvailability && (
                                    <Button
                                        variant="outline"
                                        onClick={openBulkDialog}
                                        disabled={loading || !selectedProviderId}
                                    >
                                        <CalendarDays className="h-4 w-4" />
                                        Link date range
                                    </Button>
                                )}
                                {canCreateWorkWindows && (
                                    <Button variant="default" onClick={() => setCreateDialogOpen(true)} disabled={loading || !selectedProviderId}>
                                        Create Work Window
                                    </Button>
                                )}
                                <Button variant="outline" size="icon" onClick={handleSync} disabled={syncing}>
                                    <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                                </Button>
                            </>
                        )}
                    </>
                }
            />

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
            ) : view === "calendar" ? (
                <SchedulerCalendar
                    locationId={locationId}
                    operatories={operatories}
                    appointmentTypes={appointmentTypes}
                    canManage={canManage}
                    timezone={selectedLocation?.timezone ?? undefined}
                />
            ) : (
                <>
                    {/* Provider scopes the whole page: it drives the availability
                        fetch and the Scheduling Rules card below, so it stays here.
                        The list-only filters live in the Work Windows card header. */}
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
                        <CardHeader className="gap-4 space-y-0">
                            <div className="space-y-1.5">
                                <CardTitle>
                                    {canLinkAvailability ? "Work Windows" : "Live Slots"} for {selectedProvider?.name || `${selectedProvider?.first_name} ${selectedProvider?.last_name}`}
                                </CardTitle>
                                <CardDescription>
                                    {totalShown} {canLinkAvailability ? "schedule" : "slot"}{totalShown !== 1 ? "s" : ""} shown
                                    {hasNarrowingFilter ? " (filtered)" : ""}.
                                    {canLinkAvailability && canManage
                                        ? ' Click "Edit Linking" to associate appointment types, or use "Link Date Range" to bulk-link matching windows.'
                                        : canLinkAvailability
                                            ? " Read-only view."
                                            : " These are read directly from your PMS."}
                                </CardDescription>
                            </div>

                            {/* Filters sit directly above the rows they act on. They used to
                                live at the top of the page, separated from this list by the
                                whole Scheduling Rules card. */}
                            <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/30 p-3">
                                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                    Filters
                                </span>

                                {canLinkAvailability && (
                                    <Select value={selectedApptTypeId} onValueChange={setSelectedApptTypeId}>
                                        <SelectTrigger className="h-9 w-[220px]" aria-label="Filter by appointment type">
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
                                )}

                                <Select value={selectedOperatoryId} onValueChange={setSelectedOperatoryId}>
                                    <SelectTrigger className="h-9 w-[220px]" aria-label="Filter by operatory">
                                        <SelectValue placeholder="All Operatories" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="all">All Operatories</SelectItem>
                                        {relevantOperatories.map((op) => (
                                            <SelectItem key={op.source_id} value={op.source_id}>
                                                {op.name} ({op.source_id})
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>

                                <UpcomingRangePicker value={dateRange} onChange={setDateRange} />

                                <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                                    <Checkbox
                                        checked={showExpired}
                                        onCheckedChange={(checked) => setShowExpired(checked === true)}
                                    />
                                    Show expired
                                </label>

                                {canLinkAvailability && (
                                    <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                                        <Checkbox
                                            checked={showRecurring}
                                            onCheckedChange={(checked) => setShowRecurring(checked === true)}
                                        />
                                        Show recurring
                                    </label>
                                )}

                                {canLinkAvailability && (
                                    <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                                        <Checkbox
                                            checked={showNonBookable}
                                            onCheckedChange={(checked) => setShowNonBookable(checked === true)}
                                        />
                                            Show closed periods, notes &amp; breaks
                                    </label>
                                )}

                                {hasNarrowingFilter && (
                                    <Button variant="ghost" size="sm" className="h-9 text-xs" onClick={resetFilters}>
                                        Clear filters
                                    </Button>
                                )}
                            </div>
                        </CardHeader>
                        <CardContent>
                            {loadingAvailabilities ? (
                                <div className="flex justify-center py-6 text-muted-foreground">
                                    Loading {canLinkAvailability ? "work windows" : "live slots"}...
                                </div>
                            ) : filteredAvailabilities.length === 0 ? (
                                <p className="text-center py-6 text-muted-foreground">
                                    {canLinkAvailability
                                        ? selectedApptTypeId !== "all"
                                            ? "No work windows match this appointment type."
                                            : canManage
                                                ? canCreateWorkWindows
                                                    ? "No work windows found for this provider. Add one above."
                                                    : "No work windows found for this provider. Refresh from your PMS."
                                                : "No work windows found for this provider."
                                        : "No live slots found for this provider in the next 7 days."}
                                </p>
                            ) : (
                                <div className="space-y-6">
                                    {recurringWindows.length > 0 && (
                                        <div className="space-y-3">
                                            <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                                                <Repeat className="h-4 w-4 shrink-0" />
                                                Recurring weekly windows
                                                <span className="font-normal">— repeat every week, so they apply to any date range</span>
                                            </div>
                                            {recurringWindows.map(renderWindow)}
                                        </div>
                                    )}

                                    {datedWindows.length > 0 && (
                                        <div className="space-y-3">
                                            {recurringWindows.length > 0 && (
                                                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                                                    <Calendar className="h-4 w-4 shrink-0" />
                                                    Dated windows
                                                </div>
                                            )}
                                            {pagedGroups.map((group) => (
                                                <div key={group.date || "undated"} className="space-y-3">
                                                    <div className="flex items-center gap-2 border-b pb-1.5 text-sm font-semibold">
                                                        <CalendarDays className="h-4 w-4 shrink-0 text-muted-foreground" />
                                                        {group.date
                                                            ? new Date(`${group.date}T12:00:00`).toLocaleDateString("en-US", {
                                                                weekday: "long", month: "long", day: "numeric", year: "numeric",
                                                            })
                                                            : "No specific date"}
                                                        <span className="font-normal text-muted-foreground">
                                                            — {group.rows.length} window{group.rows.length !== 1 ? "s" : ""}
                                                        </span>
                                                    </div>
                                                    {group.rows.map(renderWindow)}
                                                </div>
                                            ))}

                                            {datedWindows.length > PAGE_SIZE && (
                                                <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4 text-sm text-muted-foreground">
                                                    <span>
                                                        Showing <span className="font-medium text-foreground">{rangeFrom}–{rangeTo}</span> of{" "}
                                                        <span className="font-medium text-foreground">{datedWindows.length}</span> dated windows
                                                    </span>
                                                    <div className="flex items-center gap-2">
                                                        <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="gap-1">
                                                            <ChevronLeft className="h-4 w-4" /> Previous
                                                        </Button>
                                                        <span className="tabular-nums">Page {page + 1} of {pageCount}</span>
                                                        <Button variant="outline" size="sm" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)} className="gap-1">
                                                            Next <ChevronRight className="h-4 w-4" />
                                                        </Button>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </>
            )}

            {canManage && canLinkAvailability && (
                <>
                    {/* Bulk Link Date Range Dialog */}
                    <Dialog
                        open={bulkDialogOpen}
                        onOpenChange={(next) => { if (!bulkRunning) setBulkDialogOpen(next) }}
                    >
                        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                            <DialogHeader>
                                <DialogTitle>Link Date Range</DialogTitle>
                                <DialogDescription>
                                    Apply appointment types to real PMS work windows on the days you pick, from
                                    today up to {BULK_RANGE_MAX_DAYS} days ahead.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-3 py-2">
                                <div className="rounded-md border border-border/70 p-3 text-sm text-muted-foreground">
                                    <div>Provider: {selectedProvider?.name || `${selectedProvider?.first_name} ${selectedProvider?.last_name}`}</div>
                                    <div>Operatories: {bulkOperatoryLabel}</div>
                                    <div>Range: {bulkRangeLabel}</div>
                                </div>
                                <div className="grid gap-4 sm:grid-cols-2">
                                    <div className="space-y-1">
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-medium">Days to link</p>
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-7 px-2 text-xs"
                                                onClick={() => setBulkRange(undefined)}
                                                disabled={bulkRunning || !bulkRange?.from}
                                            >
                                                Clear
                                            </Button>
                                        </div>
                                        {/* `max` counts the gap between the ends, so 14 means 15 days inclusive. */}
                                        <CalendarPicker
                                            mode="range"
                                            max={BULK_RANGE_MAX_DAYS - 1}
                                            selected={bulkRange}
                                            onSelect={setBulkRange}
                                            defaultMonth={bulkRangeMin}
                                            startMonth={bulkRangeMin}
                                            endMonth={bulkRangeMax}
                                            disabled={bulkRunning || { before: bulkRangeMin, after: bulkRangeMax }}
                                            className="rounded-md border"
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Click a day to start a range, then a later day to extend it.
                                            Clear to start over.
                                        </p>
                                    </div>
                                    <div className="space-y-4">
                                        <div className="space-y-1">
                                            <div className="flex items-center justify-between gap-2">
                                                <p className="text-sm font-medium">Operatories</p>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="h-7 px-2 text-xs"
                                                    onClick={() =>
                                                        setBulkOperatoryIds(
                                                            allBulkOperatoriesSelected
                                                                ? []
                                                                : relevantOperatories.map((op) => op.source_id)
                                                        )
                                                    }
                                                    disabled={bulkRunning || relevantOperatories.length === 0}
                                                >
                                                    {allBulkOperatoriesSelected ? "Clear" : "Select all"}
                                                </Button>
                                            </div>
                                            {relevantOperatories.length === 0 ? (
                                                <p className="text-sm text-muted-foreground">
                                                    No visible operatories found for this provider.
                                                </p>
                                            ) : (
                                                <div className="border rounded-md max-h-36 overflow-y-auto">
                                                    {relevantOperatories.map((op) => (
                                                        <label
                                                            key={op.source_id}
                                                            className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer border-b last:border-b-0"
                                                        >
                                                            <Checkbox
                                                                checked={bulkOperatoryIds.includes(op.source_id)}
                                                                onCheckedChange={() => toggleBulkOperatoryId(op.source_id)}
                                                                disabled={bulkRunning}
                                                            />
                                                            <span className="min-w-0 flex-1 truncate text-sm">{op.name}</span>
                                                            <span className="shrink-0 text-xs text-muted-foreground">
                                                                {op.source_id}
                                                            </span>
                                                        </label>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <div className="space-y-1">
                                            <p className="text-sm font-medium">Appointment types</p>
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
                                                        >
                                                            <Checkbox
                                                                checked={bulkTypeIds.includes(at.source_id)}
                                                                onCheckedChange={() => toggleBulkTypeId(at.source_id)}
                                                                disabled={bulkRunning}
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
                                    </div>
                                </div>
                                {bulkRunning && (
                                    <div className="space-y-2 rounded-md border border-border/70 p-3">
                                        {bulkProgress ? (
                                            <>
                                                <div className="flex items-center justify-between text-sm">
                                                    <span>Batch {bulkProgress.batch} of {bulkProgress.batches}</span>
                                                    <span className="text-muted-foreground">
                                                        {bulkProgress.done} / {bulkProgress.total} linked
                                                    </span>
                                                </div>
                                                <Progress value={(bulkProgress.done / bulkProgress.total) * 100} />
                                            </>
                                        ) : (
                                            <p className="text-sm">Checking which work windows fall in this range...</p>
                                        )}
                                        <p className="text-xs text-muted-foreground">
                                            {bulkPauseRemaining > 0
                                                ? `Pausing ${bulkPauseRemaining}s before the next batch to stay inside the PMS API quota.`
                                                : "Keep this dialog open until the run finishes."}
                                        </p>
                                    </div>
                                )}
                            </div>
                            <DialogFooter>
                                <Button
                                    variant="outline"
                                    onClick={() => setBulkDialogOpen(false)}
                                    disabled={bulkRunning}
                                >
                                    Cancel
                                </Button>
                                <Button
                                    onClick={handleBulkLinkRange}
                                    disabled={
                                        bulkRunning ||
                                        bulkOperatoryIds.length === 0 ||
                                        bulkTypeIds.length === 0 ||
                                        !bulkRange?.from ||
                                        !bulkRange?.to
                                    }
                                >
                                    {bulkRunning ? "Linking..." : "Apply"}
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>

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
                                {canClearWorkingWindowOverride && editTarget?.types_overridden && (
                                    <Button variant="outline" onClick={handleClearEditOverride} disabled={saving}>
                                        Use standing rules
                                    </Button>
                                )}
                                <Button variant="outline" onClick={() => setEditTarget(null)}>Cancel</Button>
                                <Button onClick={handleSaveEdit} disabled={saving}>
                                    {saving ? "Saving..." : "Save"}
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>

                    {/* Create Work Window Dialog */}
                    {canCreateWorkWindows && (
                    <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
                        <DialogContent className="max-w-md">
                            <DialogHeader>
                                <DialogTitle>Create Custom Work Window</DialogTitle>
                                <DialogDescription>
                                    Create a schedule block for this provider.
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
                    )}
                </>
            )}
        </div>
    )
}
