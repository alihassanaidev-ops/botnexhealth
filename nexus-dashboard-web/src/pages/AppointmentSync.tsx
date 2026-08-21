import { useCallback, useEffect, useRef, useState } from "react"
import {
    CalendarClock,
    CheckCircle2,
    ChevronLeft,
    ChevronRight,
    CircleDashed,
    RefreshCcw,
    Search,
    X,
} from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import { toast } from "sonner"
import {
    listAppointmentSyncStatus,
    type AppointmentSyncItem,
    type AppointmentSyncListResponse,
} from "@/lib/appointment-sync-api"
import { useInstitution } from "@/context/InstitutionContext"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 50
const ALL_STATUSES = "__all__"
const GOTRACKER_STATUSES = [
    { id: 1, label: "Booked" },
    { id: 2, label: "Booked + Waiting" },
    { id: 3, label: "Cancelled" },
    { id: 4, label: "Late" },
    { id: 5, label: "No Show" },
    { id: 6, label: "Office Cancel" },
    { id: 7, label: "Pending" },
    { id: 8, label: "Short Cancel" },
    { id: 9, label: "Waiting" },
]

const STATUS_STYLES: Record<string, string> = {
    booked: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300",
    booked_waiting: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300",
    cancelled: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    late: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300",
    no_show: "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-900 dark:bg-orange-950/40 dark:text-orange-300",
    office_cancel: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    pending: "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300",
    short_cancel: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300",
    waiting: "border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/70 dark:text-zinc-300",
}

function formatDateTime(value: string | null): string {
    if (!value) return "-"
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
    })
}

function formatStatusLabel(value: string | null): string {
    if (!value) return "Unknown"
    return value
        .split("_")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ")
}

function StatusBadge({ item }: { item: AppointmentSyncItem }) {
    const label = item.gotracker_status_label
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
                label ? STATUS_STYLES[label] ?? STATUS_STYLES.waiting : "border-border bg-muted text-muted-foreground",
            )}
        >
            {item.gotracker_status_id ? `${item.gotracker_status_id} · ` : ""}
            {formatStatusLabel(label)}
        </span>
    )
}

function LocalStatusBadge({ status }: { status: string }) {
    return (
        <span className="inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {formatStatusLabel(status)}
        </span>
    )
}

function FlagBadge({ value, label }: { value: boolean | null; label: string }) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                value === true
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
                    : "border-border bg-muted text-muted-foreground",
            )}
        >
            {value === true ? <CheckCircle2 className="h-3 w-3" /> : <CircleDashed className="h-3 w-3" />}
            {label}
        </span>
    )
}

function SourceLabel({ source }: { source: string | null }) {
    if (!source) return <span className="text-muted-foreground">-</span>
    return (
        <span className="capitalize text-muted-foreground">
            {source.replace(/_/g, " ")}
        </span>
    )
}

function SkeletonRows({ isGoTracker }: { isGoTracker: boolean }) {
    return (
        <>
            {Array.from({ length: 8 }).map((_, index) => (
                <TableRow key={index}>
                    <TableCell><Skeleton className="h-5 w-40" /></TableCell>
                    <TableCell><Skeleton className="h-5 w-36" /></TableCell>
                    <TableCell><Skeleton className="h-5 w-24" /></TableCell>
                    {isGoTracker && <TableCell><Skeleton className="h-5 w-32" /></TableCell>}
                    <TableCell><Skeleton className="h-5 w-28" /></TableCell>
                    <TableCell><Skeleton className="h-5 w-28" /></TableCell>
                </TableRow>
            ))}
        </>
    )
}

export default function AppointmentSync() {
    const { pmsType } = useInstitution()
    const isGoTracker = pmsType === "gotracker"
    const [data, setData] = useState<AppointmentSyncListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [search, setSearch] = useState("")
    const [debouncedSearch, setDebouncedSearch] = useState("")
    const [statusId, setStatusId] = useState<string>(ALL_STATUSES)
    const [page, setPage] = useState(0)
    const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

    useEffect(() => {
        if (searchTimer.current) clearTimeout(searchTimer.current)
        searchTimer.current = setTimeout(() => setDebouncedSearch(search), 400)
        return () => {
            if (searchTimer.current) clearTimeout(searchTimer.current)
        }
    }, [search])

    useEffect(() => {
        setPage(0)
    }, [debouncedSearch, statusId])

    const fetchRows = useCallback(async () => {
        setLoading(true)
        try {
            setData(await listAppointmentSyncStatus({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                search: debouncedSearch || undefined,
                gotracker_status_id: isGoTracker && statusId !== ALL_STATUSES ? Number(statusId) : undefined,
            }))
        } catch (error) {
            toast.error(error instanceof Error ? error.message : "Failed to load appointment sync status")
        } finally {
            setLoading(false)
        }
    }, [debouncedSearch, isGoTracker, page, statusId])

    useEffect(() => {
        void fetchRows()
    }, [fetchRows])

    const total = data?.total ?? 0
    const pageCount = Math.ceil(total / PAGE_SIZE)
    const from = total === 0 ? 0 : page * PAGE_SIZE + 1
    const to = Math.min((page + 1) * PAGE_SIZE, total)

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <PageHeader
                icon={CalendarClock}
                title="Appointment Sync"
                description="Current appointment synchronization snapshot known to ScaleNexus."
                actions={
                    <>
                        {!loading && data && (
                            <div className="text-right">
                                <p className="text-2xl font-bold tabular-nums">{total.toLocaleString()}</p>
                                <p className="text-xs text-muted-foreground">appointments</p>
                            </div>
                        )}
                        <Button variant="outline" size="sm" onClick={fetchRows} disabled={loading} className="gap-1.5">
                            <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </>
                }
            />

            <div className="flex flex-wrap items-center gap-2">
                <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        placeholder="Search patient or appointment..."
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        className="h-8 w-[240px] pl-8 lg:w-[340px]"
                    />
                </div>
                {isGoTracker && (
                    <Select value={statusId} onValueChange={setStatusId}>
                        <SelectTrigger className="h-8 w-[220px]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value={ALL_STATUSES}>All GoTracker statuses</SelectItem>
                            {GOTRACKER_STATUSES.map((status) => (
                                <SelectItem key={status.id} value={String(status.id)}>
                                    {status.id} · {status.label}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                )}
                {(search || (isGoTracker && statusId !== ALL_STATUSES)) && (
                    <Button
                        variant="ghost"
                        onClick={() => {
                            setSearch("")
                            setStatusId(ALL_STATUSES)
                        }}
                        className="h-8 px-2 text-muted-foreground"
                    >
                        Reset <X className="ml-2 h-4 w-4" />
                    </Button>
                )}
            </div>

            <Card>
                <CardContent className="p-0">
                    <div className="overflow-x-auto">
                        <Table className="w-full text-sm">
                            <TableHeader className="border-b border-border bg-muted">
                                <TableRow>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Patient</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Appointment</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{isGoTracker ? "GoTracker status" : "Sync status"}</TableHead>
                                    {isGoTracker && <TableHead className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Flags</TableHead>}
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Source</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Last seen</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading ? (
                                    <SkeletonRows isGoTracker={isGoTracker} />
                                ) : !data || data.items.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={isGoTracker ? 6 : 5} className="px-4 py-16 text-center">
                                            <div className="flex flex-col items-center gap-3 text-muted-foreground">
                                                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
                                                    <CalendarClock className="h-6 w-6 opacity-40" />
                                                </div>
                                                <div>
                                                    <p className="text-sm font-medium text-foreground/70">No appointment sync rows</p>
                                                    <p className="mt-0.5 text-xs">
                                                        {search ? "Try a different search." : "Appointments synchronized from your PMS will appear here."}
                                                    </p>
                                                </div>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    data.items.map((item) => (
                                        <TableRow key={item.id} className="hover:bg-muted/40">
                                            <TableCell className="px-4 py-3">
                                                <div className="font-medium">{item.patient_name ?? "Unknown patient"}</div>
                                                <div className="font-mono text-xs text-muted-foreground">{item.patient_id ?? "-"}</div>
                                            </TableCell>
                                            <TableCell className="px-4 py-3">
                                                <div className="font-medium">{formatDateTime(item.start_time)}</div>
                                                <div className="font-mono text-xs text-muted-foreground">{item.appointment_id}</div>
                                            </TableCell>
                                            <TableCell className="px-4 py-3">
                                                {isGoTracker ? (
                                                    <>
                                                        <StatusBadge item={item} />
                                                        <div className="mt-1 text-xs capitalize text-muted-foreground">
                                                            Local: {item.local_status}
                                                        </div>
                                                    </>
                                                ) : <LocalStatusBadge status={item.local_status} />}
                                            </TableCell>
                                            {isGoTracker && (
                                                <TableCell className="px-4 py-3">
                                                    <div className="flex flex-wrap gap-1.5">
                                                        <FlagBadge value={item.is_confirmed} label="Confirmed" />
                                                        <FlagBadge value={item.is_preconfirmed} label="Preconfirmed" />
                                                    </div>
                                                </TableCell>
                                            )}
                                            <TableCell className="px-4 py-3 text-xs">
                                                <SourceLabel source={item.last_status_source} />
                                                <div className="mt-1 text-muted-foreground">
                                                    Writeback: {formatDateTime(item.last_writeback_at)}
                                                </div>
                                            </TableCell>
                                            <TableCell className="px-4 py-3 text-xs text-muted-foreground">
                                                <div>{formatDateTime(item.last_status_synced_at ?? item.last_synced_at)}</div>
                                                <div className="mt-1">{item.last_event ?? "-"}</div>
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>
                    <div className="flex items-center justify-between border-t border-border px-4 py-3 text-sm text-muted-foreground">
                        <span>
                            Showing {from.toLocaleString()}-{to.toLocaleString()} of {total.toLocaleString()}
                        </span>
                        <div className="flex items-center gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={page === 0 || loading}
                                onClick={() => setPage((value) => Math.max(0, value - 1))}
                            >
                                <ChevronLeft className="h-4 w-4" />
                            </Button>
                            <span className="min-w-24 text-center text-xs">
                                Page {pageCount === 0 ? 0 : page + 1} of {pageCount}
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={page + 1 >= pageCount || loading}
                                onClick={() => setPage((value) => value + 1)}
                            >
                                <ChevronRight className="h-4 w-4" />
                            </Button>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
