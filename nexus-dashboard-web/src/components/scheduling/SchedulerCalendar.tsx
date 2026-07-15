import { useEffect, useMemo, useRef, useState } from "react"
import { ChevronLeft, ChevronRight, AlertTriangle, Globe } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { CalendarSkeleton } from "@/components/ui/skeletons"
import { listAvailabilities, updateAvailability } from "@/lib/tenant-api"
import type { CachedAvailability, CachedOperatory, CachedAppointmentType } from "@/types"

// Clean pastel scheduler (Google/Syncfusion-style tiles). Working windows, NOT
// bookable slots. Renders in the CLINIC timezone. Day view = operatory columns
// (falls back to provider columns when the PMS has no operatories); Week view =
// day columns for one selected resource.

const DAY_MIN_DEFAULT = 8 * 60
const DAY_MAX_DEFAULT = 18 * 60
const PX_MIN = 1.25
const COL_W = 150
const GUTTER_W = 58

// soft categorical identity palette (tinted at render, so it reads as pastel)
const PALETTE = ["#4F63D2", "#0E9AA7", "#C98A1B", "#C65A7A", "#8A5CD1", "#2E8B57", "#B4530A", "#5B6B8C", "#9333A8"]

const toMin = (t?: string | null) => {
    if (!t) return null
    const [h, m] = t.split(":").map(Number)
    return h * 60 + (m || 0)
}
const fromMin = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`
const to12 = (t: string) => {
    const [h, m] = t.split(":").map(Number)
    const ap = h < 12 ? "AM" : "PM"
    const h12 = h % 12 === 0 ? 12 : h % 12
    return `${h12}:${String(m).padStart(2, "0")} ${ap}`
}
const durLabel = (a: number, b: number) => {
    const d = b - a, h = Math.floor(d / 60), m = d % 60
    return `${h ? `${h}h` : ""}${m ? `${m}m` : ""}` || "0m"
}
const addDays = (isoDate: string, delta: number) => {
    const d = new Date(`${isoDate}T12:00:00`); d.setDate(d.getDate() + delta); return d.toLocaleDateString("en-CA")
}
const mondayOf = (isoDate: string) => {
    const d = new Date(`${isoDate}T12:00:00`); const dow = (d.getDay() + 6) % 7; d.setDate(d.getDate() - dow); return d.toLocaleDateString("en-CA")
}
const todayIn = (tz: string) => new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date())
const nowMinutesIn = (tz: string) => {
    const p = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date())
    const h = Number(p.find((x) => x.type === "hour")?.value ?? 0), m = Number(p.find((x) => x.type === "minute")?.value ?? 0)
    return (h % 24) * 60 + m
}
const weekdayName = (isoDate: string) => new Date(`${isoDate}T12:00:00`).toLocaleDateString("en-US", { weekday: "long" })
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
        if (last && it._s <= last.eMin) { last.eMin = Math.max(last.eMin, it._e); last.members.push(it.av) }
        else bands.push({ sMin: it._s, eMin: it._e, members: [it.av] })
    }
    return bands
}

type Col = { key: string; name: string; sub: string | null; dup: boolean; date: string; isToday: boolean }

export default function SchedulerCalendar({
    locationId, operatories, appointmentTypes, canManage, timezone,
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
    const [viewMode, setViewMode] = useState<"day" | "week">("day")
    const [groupBy, setGroupBy] = useState<"operatory" | "provider">("operatory")
    const [overlap, setOverlap] = useState<"merge" | "split">("merge")
    const [weekResource, setWeekResource] = useState<string>("")
    const [selected, setSelected] = useState<CachedAvailability | null>(null)
    const [editTypeIds, setEditTypeIds] = useState<string[]>([])
    const [saving, setSaving] = useState(false)
    const [, forceTick] = useState(0)

    useEffect(() => { const t = setInterval(() => forceTick((n) => n + 1), 60_000); return () => clearInterval(t) }, [])
    useEffect(() => { if (!navigatedRef.current) setDate(todayIn(tz)) }, [tz])
    const goToDate = (d: string) => { navigatedRef.current = true; setDate(d) }

    useEffect(() => {
        if (!locationId) return
        let cancelled = false
        setLoading(true); setError(null)
        listAvailabilities(locationId)
            .then((data) => { if (!cancelled) setWindows(data) })
            .catch((e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load schedule") })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [locationId])

    const operatoryName = useMemo(() => new Map(operatories.map((o) => [o.source_id, o.name])), [operatories])
    const hasOperatories = useMemo(() => windows.some((w) => w.operatory_source_id), [windows])
    const effectiveGroupBy = hasOperatories ? groupBy : "provider"

    const resourceKeyOf = (w: CachedAvailability) =>
        effectiveGroupBy === "operatory" ? (w.operatory_source_id || "") : (w.provider_source_id || "__none__")
    const matchesDate = (w: CachedAvailability, d: string) => {
        if (w.active === false) return false
        if (toMin(w.begin_time) == null || toMin(w.end_time) == null) return false
        if (w.specific_date) return w.specific_date === d
        return (w.days || []).includes(weekdayName(d))
    }

    // resource list (day columns / week selector)
    const resources = useMemo(() => {
        if (effectiveGroupBy === "operatory") {
            const ids: string[] = []
            for (const w of windows) { const id = w.operatory_source_id; if (id && !ids.includes(id)) ids.push(id) }
            const names = ids.map((id) => operatoryName.get(id))
            const dup = new Set(names.filter((n, i) => n && names.indexOf(n) !== i))
            return ids.map((id) => ({ key: id, name: operatoryName.get(id) || id, sub: id, dup: dup.has(operatoryName.get(id)) }))
        }
        const seen = new Map<string, string>()
        for (const w of windows) { const k = w.provider_source_id || "__none__"; if (!seen.has(k)) seen.set(k, w.provider_name || "Unassigned") }
        return [...seen.entries()].map(([key, name]) => ({ key, name, sub: null, dup: false }))
    }, [windows, effectiveGroupBy, operatoryName])

    useEffect(() => {
        if (resources.length && !resources.some((r) => r.key === weekResource)) setWeekResource(resources[0].key)
    }, [resources, weekResource])

    const weekDays = useMemo(() => { const mon = mondayOf(date); return Array.from({ length: 7 }, (_, i) => addDays(mon, i)) }, [date])

    const clinicToday = todayIn(tz)
    const columns: Col[] = useMemo(() => {
        if (viewMode === "week") {
            return weekDays.map((d) => ({ key: d, date: d, isToday: d === clinicToday, dup: false, sub: null,
                name: new Date(`${d}T12:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "numeric", day: "numeric" }) }))
        }
        return resources.map((r) => ({ key: r.key, name: r.name, sub: r.sub, dup: r.dup, date, isToday: date === clinicToday }))
    }, [viewMode, weekDays, resources, date, clinicToday])

    const columnWindows = (c: Col) => viewMode === "week"
        ? windows.filter((w) => matchesDate(w, c.date) && resourceKeyOf(w) === weekResource)
        : windows.filter((w) => matchesDate(w, date) && resourceKeyOf(w) === c.key)

    // color by the "other" dimension so tiles stay meaningful
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
    const labelFor = (av: CachedAvailability) => effectiveGroupBy === "operatory"
        ? (av.provider_name || "Unassigned")
        : (operatoryName.get(av.operatory_source_id || "") || av.operatory_source_id || "—")

    const visibleWindows = useMemo(() => viewMode === "week"
        ? windows.filter((w) => weekDays.some((d) => matchesDate(w, d)) && resourceKeyOf(w) === weekResource)
        : windows.filter((w) => matchesDate(w, date)),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [windows, viewMode, weekDays, date, weekResource, effectiveGroupBy])

    const [minMin, maxMin] = useMemo(() => {
        let lo = DAY_MIN_DEFAULT, hi = DAY_MAX_DEFAULT
        for (const w of visibleWindows) {
            const s = toMin(w.begin_time), e = toMin(w.end_time)
            if (s != null) lo = Math.min(lo, s); if (e != null) hi = Math.max(hi, e)
        }
        // Pad an empty hour before the first / after the last window so the
        // earliest label isn't jammed in the top corner and tiles have breathing room.
        return [Math.max(0, Math.floor(lo / 60) * 60 - 60), Math.min(24 * 60, Math.ceil(hi / 60) * 60 + 60)]
    }, [visibleWindows])
    const totalH = (maxMin - minMin) * PX_MIN
    const hours: number[] = []; for (let m = minMin; m <= maxMin; m += 60) hours.push(m)

    const nowM = nowMinutesIn(tz)
    const weekHasToday = weekDays.includes(clinicToday)
    const showNow = ((viewMode === "day" && date === clinicToday) || (viewMode === "week" && weekHasToday)) && nowM >= minMin && nowM <= maxMin

    function openWindow(av: CachedAvailability) { setSelected(av); setEditTypeIds(av.appointment_type_ids || []) }

    async function saveLinks() {
        if (!selected || !canManage) return
        setSaving(true)
        try {
            const updated = await updateAvailability(selected.source_id, { appointment_type_ids: editTypeIds }, locationId)
            const nameById = new Map(appointmentTypes.map((at) => [at.source_id, at.name]))
            // Only linked types changed; keep the known-good row (PATCH response is a
            // bare availability without provider_name / sometimes times).
            const typeIds = updated.appointment_type_ids ?? editTypeIds
            const merged: CachedAvailability = { ...selected, appointment_type_ids: typeIds, appointment_type_names: typeIds.map((id) => nameById.get(id) ?? id) }
            setWindows((prev) => prev.map((w) => (w.source_id === merged.source_id ? merged : w)))
            setSelected(merged)
            toast.success("Appointment types linked")
        } catch (e: unknown) {
            toast.error(e instanceof Error ? e.message : "Failed to link types")
        } finally { setSaving(false) }
    }

    // pastel tile
    const tileStyle = (av: CachedAvailability): React.CSSProperties => {
        if (!isLinked(av)) return {
            background: "hsl(var(--muted))",
            color: "hsl(var(--muted-foreground))",
            border: "1px solid hsl(var(--border))",
        }
        const hue = hueFor(av)
        return {
            background: `color-mix(in srgb, ${hue} 15%, hsl(var(--card)))`,
            border: `1px solid color-mix(in srgb, ${hue} 26%, hsl(var(--card)))`,
        }
    }
    const timeColor = (av: CachedAvailability) => isLinked(av) ? `color-mix(in srgb, ${hueFor(av)} 62%, hsl(var(--foreground)))` : "hsl(var(--muted-foreground))"

    const gridTemplate = viewMode === "week"
        ? { display: "grid", gridTemplateColumns: `${GUTTER_W}px repeat(7, minmax(0, 1fr))` } as React.CSSProperties
        : undefined

    const providersLegend = useMemo(() => {
        // legend reflects the color dimension (provider in operatory view, operatory in provider view)
        const seen: { key: string; name: string }[] = []
        for (const w of windows) {
            const k = effectiveGroupBy === "operatory" ? (w.provider_source_id || "__none__") : (w.operatory_source_id || "__none__")
            if (seen.some((s) => s.key === k)) continue
            const name = effectiveGroupBy === "operatory" ? (w.provider_name || "Unassigned") : (operatoryName.get(w.operatory_source_id || "") || w.operatory_source_id || "—")
            seen.push({ key: k, name })
        }
        return seen
    }, [windows, effectiveGroupBy, operatoryName])

    return (
        <div className="space-y-3">
            {/* Toolbar */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <Button variant="outline" size="sm" className="h-9 rounded-lg" onClick={() => goToDate(todayIn(tz))}>Today</Button>
                <div className="flex items-center gap-1">
                    <Button variant="outline" size="icon" className="h-9 w-9 rounded-lg" onClick={() => goToDate(addDays(date, viewMode === "week" ? -7 : -1))} aria-label="Previous"><ChevronLeft className="h-4 w-4" /></Button>
                    <Button variant="outline" size="icon" className="h-9 w-9 rounded-lg" onClick={() => goToDate(addDays(date, viewMode === "week" ? 7 : 1))} aria-label="Next"><ChevronRight className="h-4 w-4" /></Button>
                </div>
                <div className="text-base font-semibold tabular-nums">
                    {viewMode === "week"
                        ? `${new Date(`${weekDays[0]}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${new Date(`${weekDays[6]}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`
                        : new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}
                    <input type="date" value={date} onChange={(e) => e.target.value && goToDate(e.target.value)}
                        className="ml-2 h-6 w-[34px] cursor-pointer border-0 bg-transparent p-0 align-middle text-muted-foreground focus:outline-none" aria-label="Pick date" />
                </div>

                <div className="ml-auto flex items-center gap-2">
                    {/* Day / Week segmented */}
                    <div className="inline-flex rounded-lg border p-0.5">
                        {(["day", "week"] as const).map((v) => (
                            <button key={v} onClick={() => setViewMode(v)}
                                className={`rounded-md px-3.5 py-1.5 text-xs font-medium capitalize transition-colors ${viewMode === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>{v}</button>
                        ))}
                    </div>
                    <div className="flex items-center gap-1 text-[11px] text-muted-foreground" title="Grid is shown in the clinic's local time">
                        <Globe className="h-3.5 w-3.5" /><span>{tz.replace(/_/g, " ")}</span>
                    </div>
                </div>
            </div>

            {/* Secondary controls */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                {viewMode === "week" && (
                    <label className="flex items-center gap-1.5">
                        <span className="text-muted-foreground">{effectiveGroupBy === "operatory" ? "Operatory" : "Provider"}</span>
                        <select value={weekResource} onChange={(e) => setWeekResource(e.target.value)}
                            className="h-8 rounded-md border bg-background px-2 text-xs">
                            {resources.map((r) => <option key={r.key} value={r.key}>{r.name}{r.sub ? ` (${r.sub})` : ""}</option>)}
                        </select>
                    </label>
                )}
                {hasOperatories && (
                    <div className="inline-flex overflow-hidden rounded-md border">
                        {(["operatory", "provider"] as const).map((v) => (
                            <button key={v} onClick={() => setGroupBy(v)}
                                className={`px-2.5 py-1 capitalize ${groupBy === v ? "bg-secondary font-medium" : "bg-background text-muted-foreground"}`}>{v}</button>
                        ))}
                    </div>
                )}
                <div className="inline-flex overflow-hidden rounded-md border">
                    {([["merge", "Open hours"], ["split", "Split"]] as const).map(([v, lbl]) => (
                        <button key={v} onClick={() => setOverlap(v)}
                            className={`px-2.5 py-1 ${overlap === v ? "bg-secondary font-medium" : "bg-background text-muted-foreground"}`}>{lbl}</button>
                    ))}
                </div>
                <span className="text-muted-foreground">Working windows (chair open) — not booked appointments.</span>
            </div>

            {loading ? (
                <CalendarSkeleton cols={7} />
            ) : error ? (
                <div className="flex items-center gap-2 rounded-xl border border-destructive/40 p-4 text-sm text-destructive"><AlertTriangle className="h-4 w-4" />{error}</div>
            ) : columns.length === 0 ? (
                <div className="rounded-xl border py-16 text-center text-sm text-muted-foreground">No schedule found for this location.</div>
            ) : (
                <div className="rounded-xl border bg-card overflow-hidden">
                    <div className="overflow-x-auto">
                        {/* header */}
                        <div className="sticky top-0 z-10 border-b bg-card" style={gridTemplate || { display: "flex", minWidth: "max-content" }}>
                            <div className="flex-none" style={gridTemplate ? undefined : { width: GUTTER_W }} />
                            {columns.map((c) => (
                                <div key={c.key} className={`border-l px-3 py-2.5 ${c.isToday && viewMode === "week" ? "bg-primary/5" : ""}`} style={gridTemplate ? undefined : { width: COL_W }}>
                                    <div className={`truncate text-[13px] font-semibold ${c.isToday && viewMode === "week" ? "text-primary" : ""}`} title={c.name}>{c.name}</div>
                                    {c.sub && <div className={`font-mono text-[10px] ${c.dup ? "text-amber-600 dark:text-amber-500" : "text-muted-foreground"}`}>{c.sub}</div>}
                                </div>
                            ))}
                        </div>
                        {/* body */}
                        <div style={{ ...(gridTemplate || { display: "flex", minWidth: "max-content" }), paddingTop: 6, paddingBottom: 8 }}>
                            <div className="relative flex-none" style={gridTemplate ? undefined : { width: GUTTER_W }}>
                                {hours.map((m) => (
                                    <div key={m} className="relative pr-2 text-right font-mono text-[10.5px] text-muted-foreground" style={{ height: 60 * PX_MIN }}>
                                        <span className="relative -top-[7px]">{fromMin(m)}</span>
                                    </div>
                                ))}
                            </div>
                            {columns.map((c) => {
                                const items = columnWindows(c)
                                return (
                                    <div key={c.key} className={`relative border-l ${c.isToday && viewMode === "week" ? "bg-primary/[0.03]" : ""}`} style={{ height: totalH, ...(gridTemplate ? {} : { width: COL_W }) }}>
                                        {hours.map((m) => (
                                            <div key={m} className="pointer-events-none absolute inset-x-0 border-t border-border/50" style={{ top: (m - minMin) * PX_MIN }} />
                                        ))}
                                        {showNow && (viewMode === "day" ? c.isToday : c.date === clinicToday) && (
                                            <div className="pointer-events-none absolute inset-x-0 z-20 flex items-center" style={{ top: (nowM - minMin) * PX_MIN }}>
                                                <div className="h-2 w-2 -translate-x-1/2 rounded-full bg-red-500" /><div className="h-[1.5px] flex-1 bg-red-500" />
                                            </div>
                                        )}
                                        {items.length === 0 && (
                                            <div className="absolute inset-0 flex items-start justify-center pt-6 text-[11px] text-muted-foreground/40">—</div>
                                        )}
                                        {overlap === "merge"
                                            ? mergeBands(items.map((av) => ({ _s: toMin(av.begin_time)!, _e: toMin(av.end_time)!, av }))).map((b, i) => {
                                                const unlinkedN = b.members.filter((m) => !isLinked(m)).length
                                                const has = unlinkedN > 0
                                                const single = b.members.length === 1 ? b.members[0] : null
                                                const hue = single && isLinked(single) ? hueFor(single) : (has ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))")
                                                const linkedStyle = single ? tileStyle(single) : {
                                                    background: has ? "hsl(var(--muted))" : `color-mix(in srgb, hsl(var(--primary)) 12%, hsl(var(--card)))`,
                                                    border: has ? "1px solid hsl(var(--border))" : `1px solid color-mix(in srgb, hsl(var(--primary)) 26%, hsl(var(--card)))`,
                                                }
                                                return (
                                                    <button key={i}
                                                        onClick={() => single ? openWindow(single) : setSelected({ ...b.members[0], __band: b } as never)}
                                                        className="absolute overflow-hidden rounded-[10px] px-2 py-1.5 text-left shadow-sm transition-shadow hover:shadow-md"
                                                        style={{ top: (b.sMin - minMin) * PX_MIN + 1, height: (b.eMin - b.sMin) * PX_MIN - 3, left: 4, right: 4, ...linkedStyle }}>
                                                        <div className="font-mono text-[10.5px] font-semibold" style={{ color: single ? timeColor(single) : (has ? "hsl(var(--muted-foreground))" : "color-mix(in srgb, hsl(var(--primary)) 62%, hsl(var(--foreground)))") }}>
                                                            {to12(fromMin(b.sMin))} – {to12(fromMin(b.eMin))}
                                                        </div>
                                                        <div className="truncate text-[12px] font-medium text-foreground">{single ? labelFor(single) : `${b.members.length} windows open`}</div>
                                                        <div className="truncate text-[11px] text-muted-foreground">
                                                            {single ? (isLinked(single) ? (single.appointment_type_names || []).join(", ") : "No types linked") : (has ? `${unlinkedN} unlinked` : "all linked")}
                                                        </div>
                                                        {(single ? !isLinked(single) : has) && <span className="absolute right-1.5 top-1 text-[11px] font-bold text-amber-600 dark:text-amber-500">!</span>}
                                                        <span className="absolute bottom-1 right-1.5 h-1.5 w-1.5 rounded-full" style={{ background: hue }} />
                                                    </button>
                                                )
                                            })
                                            : assignLanes(items.map((av) => ({ ...av, _s: toMin(av.begin_time)!, _e: toMin(av.end_time)! }))).map((w) => {
                                                const wPct = 100 / w._lanes
                                                const short = (w._e - w._s) * PX_MIN < 34
                                                return (
                                                    <button key={w.id + w._lane} onClick={() => openWindow(w)}
                                                        className="absolute overflow-hidden rounded-[10px] px-2 py-1 text-left shadow-sm transition-shadow hover:shadow-md"
                                                        style={{ top: (w._s - minMin) * PX_MIN + 1, height: (w._e - w._s) * PX_MIN - 3, left: `calc(${w._lane * wPct}% + 3px)`, width: `calc(${wPct}% - 6px)`, ...tileStyle(w) }}>
                                                        <div className="truncate font-mono text-[10px] font-semibold" style={{ color: timeColor(w) }}>{to12(w.begin_time!)}</div>
                                                        {!short && <div className="truncate text-[11.5px] font-medium text-foreground">{labelFor(w)}</div>}
                                                        {!short && <div className="truncate text-[10.5px] text-muted-foreground">{isLinked(w) ? (w.appointment_type_names || []).join(", ") : "No types"}</div>}
                                                        {!isLinked(w) && <span className="absolute right-1 top-0.5 text-[11px] font-bold text-amber-600 dark:text-amber-500">!</span>}
                                                    </button>
                                                )
                                            })}
                                    </div>
                                )
                            })}
                        </div>
                    </div>
                </div>
            )}

            {/* Legend */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
                <span className="uppercase tracking-wide">{effectiveGroupBy === "operatory" ? "Providers" : "Operatories"}</span>
                {providersLegend.slice(0, 10).map((p, i) => (
                    <span key={p.key} className="inline-flex items-center gap-1.5">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} />{p.name}
                    </span>
                ))}
                <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/40" />No types linked</span>
            </div>

            <div className="text-[11px] text-muted-foreground">
                Times in clinic time ({tz.replace(/_/g, " ")}).{!hasOperatories && " This PMS exposes no operatories — showing provider columns."}
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
                                            <Field k="Open" v={`${to12(fromMin(band.sMin))} – ${to12(fromMin(band.eMin))}`} mono />
                                            <div>
                                                <div className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">{band.members.length} underlying window{band.members.length > 1 ? "s" : ""}</div>
                                                <ul className="space-y-1.5">
                                                    {[...band.members].sort((a, b) => toMin(a.begin_time)! - toMin(b.begin_time)!).map((m) => (
                                                        <li key={m.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs">
                                                            <span className="h-2.5 w-2.5 flex-none rounded-sm" style={{ background: isLinked(m) ? hueFor(m) : "hsl(var(--muted-foreground))" }} />
                                                            <span className="font-mono">{to12(m.begin_time!)}</span>
                                                            <span className="truncate text-muted-foreground">{labelFor(m)}</span>
                                                            {!isLinked(m) && <span className="font-bold text-amber-600 dark:text-amber-500">!</span>}
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
                                        <Field k="Time" v={`${to12(selected.begin_time!)} – ${to12(selected.end_time!)}`} sub={durLabel(s, e)} mono />
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
                                                    <Button size="sm" className="mt-3 w-full" disabled={saving} onClick={saveLinks}>{saving ? "Saving..." : "Save linked types"}</Button>
                                                </>
                                            ) : selected.appointment_type_names?.length ? (
                                                <div className="flex flex-wrap gap-1.5">
                                                    {selected.appointment_type_names.map((n) => <span key={n} className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs text-primary">{n}</span>)}
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
            <div className={`text-sm ${mono ? "font-mono" : ""}`}>{v} {sub && <span className="font-mono text-xs text-muted-foreground">{sub}</span>}</div>
        </div>
    )
}
