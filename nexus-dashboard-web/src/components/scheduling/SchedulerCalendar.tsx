import { useEffect, useMemo, useRef, useState } from "react"
import { ChevronLeft, ChevronRight, AlertTriangle, Loader2, Globe } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { listAvailabilities, updateAvailability } from "@/lib/tenant-api"
import type { CachedAvailability, CachedOperatory, CachedAppointmentType } from "@/types"

// Operatory-column day grid (Open Dental-style). Working windows, NOT bookable
// slots. Renders in the CLINIC timezone. Degrades to provider columns when the
// PMS exposes no operatories.

const DAY_MIN_DEFAULT = 8 * 60
const DAY_MAX_DEFAULT = 18 * 60
const PX_MIN = 1.2
const COL_W = 152
const GUTTER_W = 60
const AMBER = "#d97706"

// categorical identity palette (kept distinct from the app's violet primary)
const PALETTE = ["#4F63D2", "#0E9AA7", "#C98A1B", "#C65A7A", "#8A5CD1", "#2E8B57", "#B4530A", "#5B6B8C", "#9333A8"]

const toMin = (t?: string | null) => {
    if (!t) return null
    const [h, m] = t.split(":").map(Number)
    return h * 60 + (m || 0)
}
const fromMin = (m: number) =>
    `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`
const durLabel = (a: number, b: number) => {
    const d = b - a, h = Math.floor(d / 60), m = d % 60
    return `${h ? `${h}h` : ""}${m ? `${m}m` : ""}` || "0m"
}
const addDays = (isoDate: string, delta: number) => {
    const d = new Date(`${isoDate}T12:00:00`)
    d.setDate(d.getDate() + delta)
    return d.toLocaleDateString("en-CA")
}
// "today" in a given IANA zone as YYYY-MM-DD
const todayIn = (tz: string) => new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date())
// current wall-clock minutes in a given zone
const nowMinutesIn = (tz: string) => {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date())
    const h = Number(parts.find((p) => p.type === "hour")?.value ?? 0)
    const m = Number(parts.find((p) => p.type === "minute")?.value ?? 0)
    return (h % 24) * 60 + m
}
const isLinked = (av: CachedAvailability) => !!(av.appointment_type_ids && av.appointment_type_ids.length)

type Band = { sMin: number; eMin: number; members: CachedAvailability[] }

function assignLanes(items: (CachedAvailability & { _s: number; _e: number })[]) {
    const sorted = [...items].sort((a, b) => a._s - b._s || a._e - b._e)
    const laneEnd: number[] = []
    const out = sorted.map((it) => {
        let lane = laneEnd.findIndex((end) => end <= it._s)
        if (lane === -1) { lane = laneEnd.length; laneEnd.push(0) }
        laneEnd[lane] = it._e
        return { ...it, _lane: lane }
    })
    return out.map((it) => ({ ...it, _lanes: laneEnd.length }))
}

function mergeBands(items: { _s: number; _e: number; av: CachedAvailability }[]): Band[] {
    const sorted = [...items].sort((a, b) => a._s - b._s)
    const bands: Band[] = []
    for (const it of sorted) {
        const last = bands[bands.length - 1]
        if (last && it._s <= last.eMin) {
            last.eMin = Math.max(last.eMin, it._e)
            last.members.push(it.av)
        } else {
            bands.push({ sMin: it._s, eMin: it._e, members: [it.av] })
        }
    }
    return bands
}

export default function SchedulerCalendar({
    locationId,
    operatories,
    appointmentTypes,
    canManage,
    timezone,
}: {
    locationId?: string
    operatories: CachedOperatory[]
    appointmentTypes: CachedAppointmentType[]
    canManage: boolean
    timezone?: string
}) {
    const tz = timezone || Intl.DateTimeFormat().resolvedOptions().timeZone

    const [windows, setWindows] = useState<CachedAvailability[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [date, setDate] = useState<string>(() => todayIn(timezone || Intl.DateTimeFormat().resolvedOptions().timeZone))
    const navigatedRef = useRef(false)
    const [groupBy, setGroupBy] = useState<"operatory" | "provider">("operatory")
    const [overlap, setOverlap] = useState<"merge" | "split">("merge")
    const [selected, setSelected] = useState<CachedAvailability | null>(null)
    const [editTypeIds, setEditTypeIds] = useState<string[]>([])
    const [saving, setSaving] = useState(false)
    const [, forceTick] = useState(0)

    // keep the "now" line fresh; re-render every 60s
    useEffect(() => {
        const t = setInterval(() => forceTick((n) => n + 1), 60_000)
        return () => clearInterval(t)
    }, [])

    // if the clinic tz resolves after mount and the user hasn't navigated, re-default to clinic today
    useEffect(() => {
        if (!navigatedRef.current) setDate(todayIn(tz))
    }, [tz])

    const goToDate = (d: string) => { navigatedRef.current = true; setDate(d) }

    useEffect(() => {
        if (!locationId) return
        let cancelled = false
        setLoading(true)
        setError(null)
        listAvailabilities(locationId)
            .then((data) => { if (!cancelled) setWindows(data) })
            .catch((e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load schedule") })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [locationId])

    const operatoryName = useMemo(() => new Map(operatories.map((o) => [o.source_id, o.name])), [operatories])
    const hasOperatories = useMemo(() => windows.some((w) => w.operatory_source_id), [windows])
    const effectiveGroupBy = hasOperatories ? groupBy : "provider"
    const weekday = useMemo(() => new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "long" }), [date])

    const dayWindows = useMemo(() => windows.filter((w) => {
        if (w.active === false) return false
        if (toMin(w.begin_time) == null || toMin(w.end_time) == null) return false
        if (w.specific_date) return w.specific_date === date
        return (w.days || []).includes(weekday)
    }), [windows, date, weekday])

    const columns = useMemo(() => {
        if (effectiveGroupBy === "operatory") {
            const ids: string[] = []
            for (const w of windows) { const id = w.operatory_source_id; if (id && !ids.includes(id)) ids.push(id) }
            const names = ids.map((id) => operatoryName.get(id))
            const dupNames = new Set(names.filter((n, i) => n && names.indexOf(n) !== i))
            return ids.map((id) => { const name = operatoryName.get(id) || id; return { key: id, name, sub: id, dup: dupNames.has(name) } })
        }
        const seen = new Map<string, string>()
        for (const w of windows) { const key = w.provider_source_id || "__none__"; if (!seen.has(key)) seen.set(key, w.provider_name || "Unassigned") }
        return [...seen.entries()].map(([key, name]) => ({ key, name, sub: null as string | null, dup: false }))
    }, [windows, effectiveGroupBy, operatoryName])

    const colorKeys = useMemo(() => {
        const keys: string[] = []
        for (const w of windows) {
            const k = effectiveGroupBy === "operatory" ? (w.provider_source_id || "__none__") : (w.operatory_source_id || "__none__")
            if (!keys.includes(k)) keys.push(k)
        }
        return keys
    }, [windows, effectiveGroupBy])
    const hueFor = (av: CachedAvailability) => {
        const k = effectiveGroupBy === "operatory" ? (av.provider_source_id || "__none__") : (av.operatory_source_id || "__none__")
        return PALETTE[Math.max(0, colorKeys.indexOf(k)) % PALETTE.length]
    }
    const colWindows = (key: string) => dayWindows.filter((w) =>
        effectiveGroupBy === "operatory" ? w.operatory_source_id === key : (w.provider_source_id || "__none__") === key)

    const [minMin, maxMin] = useMemo(() => {
        let lo = DAY_MIN_DEFAULT, hi = DAY_MAX_DEFAULT
        for (const w of dayWindows) {
            const s = toMin(w.begin_time), e = toMin(w.end_time)
            if (s != null) lo = Math.min(lo, s)
            if (e != null) hi = Math.max(hi, e)
        }
        return [Math.floor(lo / 60) * 60, Math.ceil(hi / 60) * 60]
    }, [dayWindows])
    const totalH = (maxMin - minMin) * PX_MIN

    const isToday = date === todayIn(tz)
    const nowM = nowMinutesIn(tz)
    const showNow = isToday && nowM >= minMin && nowM <= maxMin

    const labelFor = (av: CachedAvailability) => effectiveGroupBy === "operatory"
        ? (av.provider_name || "Unassigned")
        : (operatoryName.get(av.operatory_source_id || "") || av.operatory_source_id || "—")

    function openWindow(av: CachedAvailability) { setSelected(av); setEditTypeIds(av.appointment_type_ids || []) }

    async function saveLinks() {
        if (!selected || !canManage) return
        setSaving(true)
        try {
            const updated = await updateAvailability(selected.source_id, { appointment_type_ids: editTypeIds }, locationId)
            const nameById = new Map(appointmentTypes.map((at) => [at.source_id, at.name]))
            // Only linked types changed. NexHealth's PATCH response is a bare
            // availability (no synthesized provider_name / sometimes no times), so
            // keep the known-good row and override only the type fields.
            const typeIds = updated.appointment_type_ids ?? editTypeIds
            const merged: CachedAvailability = {
                ...selected,
                appointment_type_ids: typeIds,
                appointment_type_names: typeIds.map((id) => nameById.get(id) ?? id),
            }
            setWindows((prev) => prev.map((w) => (w.source_id === merged.source_id ? merged : w)))
            setSelected(merged)
            toast.success("Appointment types linked")
        } catch (e: unknown) {
            toast.error(e instanceof Error ? e.message : "Failed to link types")
        } finally {
            setSaving(false)
        }
    }

    const hours: number[] = []
    for (let m = minMin; m <= maxMin; m += 60) hours.push(m)

    // block visual: linked = solid identity hue; unlinked = amber, dashed, muted
    function splitBlockStyle(av: CachedAvailability): React.CSSProperties {
        if (isLinked(av)) {
            const hue = hueFor(av)
            return {
                background: `color-mix(in srgb, ${hue} 16%, hsl(var(--card)))`,
                border: `1px solid color-mix(in srgb, ${hue} 34%, hsl(var(--card)))`,
                borderLeft: `3px solid ${hue}`,
                color: `color-mix(in srgb, ${hue} 60%, hsl(var(--foreground)))`,
            }
        }
        return {
            background: `color-mix(in srgb, ${AMBER} 8%, hsl(var(--card)))`,
            border: `1px dashed color-mix(in srgb, ${AMBER} 45%, hsl(var(--card)))`,
            borderLeft: `3px dashed ${AMBER}`,
            color: "hsl(var(--muted-foreground))",
        }
    }

    return (
        <div className="space-y-3">
            {/* Controls */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-card p-3">
                <div className="flex items-center gap-1.5">
                    <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => goToDate(addDays(date, -1))} aria-label="Previous day">
                        <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <div className="flex flex-col items-center">
                        <span className="text-sm font-semibold leading-tight tabular-nums">
                            {new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                        </span>
                        <input type="date" value={date} onChange={(e) => e.target.value && goToDate(e.target.value)}
                            className="h-5 w-[120px] cursor-pointer border-0 bg-transparent p-0 text-center text-[11px] text-muted-foreground focus:outline-none" />
                    </div>
                    <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => goToDate(addDays(date, 1))} aria-label="Next day">
                        <ChevronRight className="h-4 w-4" />
                    </Button>
                    {!isToday && <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={() => goToDate(todayIn(tz))}>Today</Button>}
                </div>

                {hasOperatories && (
                    <div className="flex items-center gap-2">
                        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Group by</span>
                        <div className="inline-flex overflow-hidden rounded-md border">
                            {(["operatory", "provider"] as const).map((v) => (
                                <button key={v} onClick={() => setGroupBy(v)}
                                    className={`px-3 py-1.5 text-xs capitalize ${groupBy === v ? "bg-primary text-primary-foreground font-medium" : "bg-background text-muted-foreground"}`}>{v}</button>
                            ))}
                        </div>
                    </div>
                )}
                <div className="flex items-center gap-2">
                    <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Overlaps</span>
                    <div className="inline-flex overflow-hidden rounded-md border">
                        {([["merge", "Open hours"], ["split", "Split"]] as const).map(([v, lbl]) => (
                            <button key={v} onClick={() => setOverlap(v)}
                                className={`px-3 py-1.5 text-xs ${overlap === v ? "bg-primary text-primary-foreground font-medium" : "bg-background text-muted-foreground"}`}>{lbl}</button>
                        ))}
                    </div>
                </div>
                <div className="ml-auto flex items-center gap-1.5 text-[11px] text-muted-foreground" title="The grid is shown in the clinic's local time">
                    <Globe className="h-3.5 w-3.5" />
                    <span className="tabular-nums">{tz.replace(/_/g, " ")}</span>
                </div>
            </div>

            <div className="flex items-start gap-2 rounded-md border-l-[3px] border-l-primary bg-card px-3 py-2 text-xs text-muted-foreground">
                <span>These are <b className="text-foreground">working windows</b> (when a chair is open), not booked appointments. Bookable slots = windows minus buffer, cutoff, and existing appointments.</span>
            </div>

            {/* Legend */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1.5"><span className="h-3 w-4 rounded-sm border-l-[3px]" style={{ background: "color-mix(in srgb, #4F63D2 16%, hsl(var(--card)))", borderColor: "#4F63D2" }} />Linked (generates slots) — color = {effectiveGroupBy === "operatory" ? "provider" : "operatory"}</span>
                <span className="inline-flex items-center gap-1.5"><span className="h-3 w-4 rounded-sm border-l-[3px] border-dashed" style={{ background: `color-mix(in srgb, ${AMBER} 8%, hsl(var(--card)))`, borderColor: AMBER }} />No types linked — won't generate slots</span>
            </div>

            {loading ? (
                <div className="flex justify-center py-16 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>
            ) : error ? (
                <div className="flex items-center gap-2 rounded-lg border border-destructive/40 p-4 text-sm text-destructive"><AlertTriangle className="h-4 w-4" />{error}</div>
            ) : columns.length === 0 ? (
                <div className="rounded-lg border py-12 text-center text-sm text-muted-foreground">No schedule found for this location.</div>
            ) : (
                <div className="rounded-lg border bg-card overflow-hidden">
                    <div className="overflow-x-auto">
                        {/* header */}
                        <div className="sticky top-0 z-10 flex min-w-max border-b bg-muted/40">
                            <div className="sticky left-0 z-10 flex-none bg-muted/40" style={{ width: GUTTER_W }} />
                            {columns.map((c) => (
                                <div key={c.key} className="flex-none border-l px-2.5 py-2" style={{ width: COL_W }}>
                                    <div className="truncate text-[13px] font-semibold" title={c.name}>{c.name}</div>
                                    {c.sub && <div className={`font-mono text-[10px] ${c.dup ? "text-amber-600 dark:text-amber-500" : "text-muted-foreground"}`}>{c.sub}</div>}
                                </div>
                            ))}
                        </div>
                        {/* body */}
                        <div className="flex min-w-max">
                            <div className="sticky left-0 z-[5] flex-none border-r bg-card" style={{ width: GUTTER_W }}>
                                {hours.map((m) => (
                                    <div key={m} className="relative pr-2 text-right font-mono text-[11px] text-muted-foreground" style={{ height: 60 * PX_MIN }}>
                                        <span className="relative -top-[7px]">{fromMin(m)}</span>
                                    </div>
                                ))}
                                {showNow && (
                                    <div className="absolute right-1 z-20 -translate-y-1/2 rounded-sm bg-red-600 px-1 py-px font-mono text-[9px] font-semibold text-white tabular-nums"
                                        style={{ top: (nowM - minMin) * PX_MIN }}>{fromMin(nowM)}</div>
                                )}
                            </div>
                            <div className="relative flex">
                                {showNow && (
                                    <div className="pointer-events-none absolute inset-x-0 z-20 flex items-center" style={{ top: (nowM - minMin) * PX_MIN }}>
                                        <div className="h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-red-600" />
                                        <div className="h-px flex-1 bg-red-600" />
                                    </div>
                                )}
                                {columns.map((c) => {
                                    const items = colWindows(c.key)
                                    return (
                                        <div key={c.key} className="relative flex-none border-l" style={{ width: COL_W, height: totalH }}>
                                            {hours.map((m) => (
                                                <div key={m} className="pointer-events-none absolute inset-x-0 border-t border-border/60" style={{ top: (m - minMin) * PX_MIN }} />
                                            ))}
                                            {items.length === 0 && (
                                                <div className="absolute inset-x-0 top-2 text-center text-[11px] uppercase tracking-wider text-muted-foreground/50">idle</div>
                                            )}
                                            {overlap === "merge"
                                                ? mergeBands(items.map((av) => ({ _s: toMin(av.begin_time)!, _e: toMin(av.end_time)!, av }))).map((b, i) => {
                                                    const unlinkedN = b.members.filter((m) => !isLinked(m)).length
                                                    const hasUnlinked = unlinkedN > 0
                                                    const accent = hasUnlinked ? AMBER : "hsl(var(--primary))"
                                                    return (
                                                        <button key={i}
                                                            onClick={() => b.members.length === 1 ? openWindow(b.members[0]) : setSelected({ ...b.members[0], __band: b } as never)}
                                                            className="absolute rounded-md border px-1.5 py-1 text-left"
                                                            style={{
                                                                top: (b.sMin - minMin) * PX_MIN, height: (b.eMin - b.sMin) * PX_MIN - 3, left: 3, right: 3,
                                                                background: `color-mix(in srgb, ${accent} 9%, hsl(var(--card)))`,
                                                                borderColor: `color-mix(in srgb, ${accent} 40%, hsl(var(--card)))`,
                                                                borderStyle: hasUnlinked ? "dashed" : "solid",
                                                                borderLeft: `3px ${hasUnlinked ? "dashed" : "solid"} ${accent}`,
                                                            }}>
                                                            <div className="font-mono text-[10.5px] font-semibold text-foreground">{fromMin(b.sMin)}–{fromMin(b.eMin)}</div>
                                                            <div className="truncate text-[11px] text-muted-foreground">
                                                                {b.members.length} open{hasUnlinked ? ` · ${unlinkedN} unlinked` : ""}
                                                            </div>
                                                        </button>
                                                    )
                                                })
                                                : assignLanes(items.map((av) => ({ ...av, _s: toMin(av.begin_time)!, _e: toMin(av.end_time)! }))).map((w) => {
                                                    const wPct = 100 / w._lanes
                                                    const unlinked = !isLinked(w)
                                                    return (
                                                        <button key={w.id + w._lane} onClick={() => openWindow(w)}
                                                            className="absolute overflow-hidden rounded-md px-1.5 py-1 text-left transition-[filter] hover:brightness-[1.04]"
                                                            style={{
                                                                top: (w._s - minMin) * PX_MIN, height: (w._e - w._s) * PX_MIN - 2,
                                                                left: `calc(${w._lane * wPct}% + 2px)`, width: `calc(${wPct}% - 4px)`,
                                                                ...splitBlockStyle(w),
                                                            }}>
                                                            <div className="font-mono text-[10.5px] font-semibold">{w.begin_time}–{w.end_time}</div>
                                                            <div className="truncate text-[11px] opacity-90">{labelFor(w)}</div>
                                                            {unlinked && <span className="absolute right-1 top-0.5 text-[11px] font-extrabold" style={{ color: AMBER }}>!</span>}
                                                        </button>
                                                    )
                                                })}
                                        </div>
                                    )
                                })}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            <div className="text-xs text-muted-foreground">
                Times shown in clinic time ({tz.replace(/_/g, " ")}) · duplicate room names disambiguated by <span className="font-mono">nh-id</span>.
                {!hasOperatories && " This PMS exposes no operatories — showing provider columns."}
            </div>

            {/* Detail drawer */}
            {selected && (
                <>
                    <div className="fixed inset-0 z-40 bg-black/40" onClick={() => setSelected(null)} />
                    <aside className="fixed right-0 top-0 z-50 flex h-full w-[340px] max-w-[90vw] flex-col border-l bg-card shadow-2xl">
                        <div className="flex items-start justify-between border-b p-4">
                            <h2 className="text-sm font-semibold">{(selected as never as { __band?: Band }).__band ? "Open-hours band" : "Working window"}</h2>
                            <button onClick={() => setSelected(null)} className="text-xl leading-none text-muted-foreground hover:text-foreground" aria-label="Close">×</button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-4">
                            {(() => {
                                const band = (selected as never as { __band?: Band }).__band
                                if (band) {
                                    return (
                                        <div className="space-y-4">
                                            <Field k="Room" v={effectiveGroupBy === "operatory" ? (operatoryName.get(selected.operatory_source_id || "") || "") : (selected.provider_name || "")} sub={effectiveGroupBy === "operatory" ? (selected.operatory_source_id || undefined) : undefined} />
                                            <Field k="Open" v={`${fromMin(band.sMin)} – ${fromMin(band.eMin)}`} mono />
                                            <div>
                                                <div className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">{band.members.length} underlying window{band.members.length > 1 ? "s" : ""}</div>
                                                <ul className="space-y-1.5">
                                                    {[...band.members].sort((a, b) => toMin(a.begin_time)! - toMin(b.begin_time)!).map((m) => (
                                                        <li key={m.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs">
                                                            <span className="h-2.5 w-2.5 flex-none rounded-sm" style={{ background: isLinked(m) ? hueFor(m) : AMBER }} />
                                                            <span className="font-mono">{m.begin_time}–{m.end_time}</span>
                                                            <span className="truncate text-muted-foreground">{labelFor(m)}</span>
                                                            {!isLinked(m) && <span className="font-bold" style={{ color: AMBER }}>!</span>}
                                                            <button className="ml-auto text-primary underline-offset-2 hover:underline" onClick={() => openWindow(m)}>open</button>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        </div>
                                    )
                                }
                                const s = toMin(selected.begin_time)!, e = toMin(selected.end_time)!
                                return (
                                    <div className="space-y-4">
                                        <Field k="Time" v={`${selected.begin_time} – ${selected.end_time}`} sub={durLabel(s, e)} mono />
                                        <Field k="Operatory" v={operatoryName.get(selected.operatory_source_id || "") || "—"} sub={selected.operatory_source_id || undefined} />
                                        <Field k="Provider" v={selected.provider_name || "Unassigned"} />
                                        <div>
                                            <div className="mb-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">Appointment types</div>
                                            {canManage ? (
                                                <>
                                                    {appointmentTypes.length === 0
                                                        ? <p className="text-xs text-muted-foreground">No appointment types configured.</p>
                                                        : (
                                                            <div className="max-h-56 overflow-y-auto rounded-md border">
                                                                {appointmentTypes.map((at) => (
                                                                    <label key={at.source_id} className="flex cursor-pointer items-center gap-2 border-b px-3 py-2 text-sm last:border-b-0 hover:bg-muted/50">
                                                                        <Checkbox checked={editTypeIds.includes(at.source_id)}
                                                                            onCheckedChange={() => setEditTypeIds((p) => p.includes(at.source_id) ? p.filter((x) => x !== at.source_id) : [...p, at.source_id])} />
                                                                        <span className="truncate">{at.name}</span>
                                                                        {at.duration_minutes && <span className="ml-auto text-xs text-muted-foreground">{at.duration_minutes}m</span>}
                                                                    </label>
                                                                ))}
                                                            </div>
                                                        )}
                                                    {!isLinked(selected) && (
                                                        <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-700 dark:text-amber-400">
                                                            ⚠ No types linked — this window won't generate bookable slots.
                                                        </div>
                                                    )}
                                                    <Button size="sm" className="mt-3 w-full" disabled={saving} onClick={saveLinks}>
                                                        {saving ? "Saving..." : "Save linked types"}
                                                    </Button>
                                                </>
                                            ) : selected.appointment_type_names?.length ? (
                                                <div className="flex flex-wrap gap-1.5">
                                                    {selected.appointment_type_names.map((n) => (
                                                        <span key={n} className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs text-primary">{n}</span>
                                                    ))}
                                                </div>
                                            ) : <p className="text-xs text-muted-foreground">None linked.</p>}
                                        </div>
                                    </div>
                                )
                            })()}
                        </div>
                    </aside>
                </>
            )}
        </div>
    )
}

function Field({ k, v, sub, mono }: { k: string; v: string; sub?: string; mono?: boolean }) {
    return (
        <div>
            <div className="mb-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">{k}</div>
            <div className={`text-sm ${mono ? "font-mono" : ""}`}>
                {v} {sub && <span className="font-mono text-xs text-muted-foreground">{sub}</span>}
            </div>
        </div>
    )
}
