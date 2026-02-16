import { useEffect, useState, useCallback } from "react"
import {
    CalendarSearch,
    Phone,
    AlertTriangle,
    Clock,
    CheckCircle2,
    XCircle,
    CalendarClock,
    Siren,
    BellRing,
    ChevronDown,
    ChevronUp,
    ExternalLink,
    Search,
    X,
    User2,
    MessageSquareText,
    Headphones,
    Zap,
    ShieldAlert,
} from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { toast } from "sonner"
import type { CallRecord, CallsResponse } from "@/types"
import { listCalls } from "@/lib/tenant-api"

// ── Status Config ───────────────────────────────────────────────────────

interface StatusConfig {
    label: string
    icon: React.ElementType
    color: string
    bgColor: string
    borderColor: string
}

const STATUS_CONFIG: Record<string, StatusConfig> = {
    need_booking: {
        label: "Need Booking",
        icon: CalendarSearch,
        color: "text-blue-700 dark:text-blue-400",
        bgColor: "bg-blue-50 dark:bg-blue-950/40",
        borderColor: "border-blue-200 dark:border-blue-800",
    },
    need_cancellation: {
        label: "Need Cancellation",
        icon: XCircle,
        color: "text-red-700 dark:text-red-400",
        bgColor: "bg-red-50 dark:bg-red-950/40",
        borderColor: "border-red-200 dark:border-red-800",
    },
    need_reschedule: {
        label: "Need Reschedule",
        icon: CalendarClock,
        color: "text-amber-700 dark:text-amber-400",
        bgColor: "bg-amber-50 dark:bg-amber-950/40",
        borderColor: "border-amber-200 dark:border-amber-800",
    },
    need_emergency: {
        label: "Emergency",
        icon: Siren,
        color: "text-rose-700 dark:text-rose-400",
        bgColor: "bg-rose-50 dark:bg-rose-950/40",
        borderColor: "border-rose-200 dark:border-rose-800",
    },
    needs_follow_up: {
        label: "Needs Follow Up",
        icon: BellRing,
        color: "text-orange-700 dark:text-orange-400",
        bgColor: "bg-orange-50 dark:bg-orange-950/40",
        borderColor: "border-orange-200 dark:border-orange-800",
    },
    no_action: {
        label: "No Action",
        icon: CheckCircle2,
        color: "text-emerald-700 dark:text-emerald-400",
        bgColor: "bg-emerald-50 dark:bg-emerald-950/40",
        borderColor: "border-emerald-200 dark:border-emerald-800",
    },
}

const STATUS_KEYS = Object.keys(STATUS_CONFIG)

// ── Helpers ─────────────────────────────────────────────────────────────

function formatDate(dateStr: string | null): string {
    if (!dateStr) return "N/A"
    try {
        return new Date(dateStr).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
        })
    } catch {
        return dateStr
    }
}

function formatTime(timeStr: string | null): string {
    if (!timeStr) return ""
    if (/^\d{1,2}:\d{2}/.test(timeStr)) return timeStr
    try {
        return new Date(timeStr).toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
        })
    } catch {
        return timeStr
    }
}

// ── Call Card Component ─────────────────────────────────────────────────

function CallCard({
    call,
    isExpanded,
    onToggle,
}: {
    call: CallRecord
    isExpanded: boolean
    onToggle: () => void
}) {
    const statusKey = call.call_status_normalized || "no_action"
    const config = STATUS_CONFIG[statusKey] || STATUS_CONFIG.no_action
    const StatusIcon = config.icon
    const hasComplaint =
        call.complaining_patient &&
        call.complaining_patient.toLowerCase() !== "no" &&
        call.complaining_patient.toLowerCase() !== "false"

    return (
        <Card className={`transition-all hover:shadow-md ${hasComplaint ? "ring-1 ring-rose-300 dark:ring-rose-700" : ""}`}>
            {/* Complaint banner */}
            {hasComplaint && (
                <div className="flex items-center gap-2 bg-rose-50 dark:bg-rose-950/40 px-4 py-2 text-sm text-rose-700 dark:text-rose-400 border-b border-rose-200 dark:border-rose-800 rounded-t-lg">
                    <ShieldAlert className="h-4 w-4 shrink-0" />
                    <span className="font-medium">Complaint Alert:</span>
                    <span className="truncate">{call.complaining_patient}</span>
                </div>
            )}

            <div
                className="cursor-pointer p-4"
                onClick={onToggle}
            >
                {/* Header row */}
                <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-semibold text-sm">
                                {call.patient_name}
                            </span>
                            {call.new_patient?.toLowerCase() === "yes" && (
                                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/10 text-primary">
                                    New Patient
                                </Badge>
                            )}
                        </div>
                        <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground flex-wrap">
                            <span className="flex items-center gap-1">
                                <Clock className="h-3 w-3" />
                                {formatDate(call.call_date)}
                                {call.call_time && ` · ${formatTime(call.call_time)}`}
                            </span>
                            {call.call_duration && call.call_duration !== "0:00" && (
                                <span className="flex items-center gap-1">
                                    <Phone className="h-3 w-3" />
                                    {call.call_duration}
                                </span>
                            )}
                            {call.times_called && (
                                <span>{call.times_called} call(s)</span>
                            )}
                        </div>
                    </div>

                    <Badge
                        variant="outline"
                        className={`shrink-0 gap-1 ${config.color} ${config.bgColor} ${config.borderColor}`}
                    >
                        <StatusIcon className="h-3 w-3" />
                        {call.call_status || config.label}
                    </Badge>
                </div>

                {/* Summary preview */}
                {call.call_summary && !isExpanded && (
                    <p className="mt-2 text-xs text-muted-foreground line-clamp-2">
                        💬 {call.call_summary}
                    </p>
                )}

                {/* Footer */}
                <div className="flex items-center justify-between mt-3">
                    {call.next_action ? (
                        <span className="text-xs font-medium text-amber-600 dark:text-amber-400 flex items-center gap-1">
                            <AlertTriangle className="h-3 w-3" />
                            Action Required
                        </span>
                    ) : (
                        <span />
                    )}
                    <Button variant="ghost" size="sm" className="h-6 text-xs gap-1 text-muted-foreground">
                        {isExpanded ? (
                            <>
                                <ChevronUp className="h-3 w-3" />
                                Hide Details
                            </>
                        ) : (
                            <>
                                <ChevronDown className="h-3 w-3" />
                                View Details
                            </>
                        )}
                    </Button>
                </div>
            </div>

            {/* Expanded details */}
            {isExpanded && (
                <div className="border-t px-4 pb-4 pt-3 space-y-4">
                    {/* Next Action */}
                    {call.next_action && (
                        <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 p-3">
                            <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-400 flex items-center gap-1.5 mb-1">
                                <Zap className="h-3.5 w-3.5" />
                                Next Action To Do
                            </h4>
                            <p className="text-sm">{call.next_action}</p>
                        </div>
                    )}

                    {/* Call Summary */}
                    {call.call_summary && (
                        <div>
                            <h4 className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5 mb-1">
                                <MessageSquareText className="h-3.5 w-3.5" />
                                Call Summary
                            </h4>
                            <p className="text-sm">{call.call_summary}</p>
                        </div>
                    )}

                    {/* Patient Info Grid */}
                    <div>
                        <h4 className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5 mb-2">
                            <User2 className="h-3.5 w-3.5" />
                            Patient Information
                        </h4>
                        <div className="grid grid-cols-2 gap-2 text-sm">
                            <div>
                                <span className="text-xs text-muted-foreground">Name</span>
                                <p className="font-medium">{call.patient_name}</p>
                            </div>
                            {call.phone && (
                                <div>
                                    <span className="text-xs text-muted-foreground">Phone</span>
                                    <p className="font-medium">{call.phone}</p>
                                </div>
                            )}
                            {call.email && (
                                <div>
                                    <span className="text-xs text-muted-foreground">Email</span>
                                    <p className="font-medium">{call.email}</p>
                                </div>
                            )}
                            {call.new_patient && (
                                <div>
                                    <span className="text-xs text-muted-foreground">New Patient</span>
                                    <p className="font-medium">{call.new_patient}</p>
                                </div>
                            )}
                            {call.patient_intent && (
                                <div>
                                    <span className="text-xs text-muted-foreground">Intent</span>
                                    <p className="font-medium">{call.patient_intent}</p>
                                </div>
                            )}
                            {call.preferred_callback_time && (
                                <div>
                                    <span className="text-xs text-muted-foreground">Preferred Callback</span>
                                    <p className="font-medium">{call.preferred_callback_time}</p>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Insurance & Billing */}
                    {call.insurance_and_billing && (
                        <div>
                            <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">
                                💳 Insurance & Billing
                            </h4>
                            <div className="flex flex-wrap gap-1.5">
                                {call.insurance_and_billing.split(",").map((tag, i) => (
                                    <Badge key={i} variant="secondary" className="text-xs">
                                        {tag.trim()}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Transcript */}
                    {call.call_transcript && (
                        <div>
                            <h4 className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5 mb-1">
                                📝 Call Transcript
                            </h4>
                            <div className="text-xs bg-muted/50 rounded-lg p-3 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono">
                                {call.call_transcript}
                            </div>
                        </div>
                    )}

                    {/* Recording link */}
                    {call.recording_link && (
                        <a
                            href={call.recording_link}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <Button variant="outline" size="sm" className="gap-1.5">
                                <Headphones className="h-3.5 w-3.5" />
                                Listen to Recording
                                <ExternalLink className="h-3 w-3" />
                            </Button>
                        </a>
                    )}
                </div>
            )}
        </Card>
    )
}

// ── Main Page ───────────────────────────────────────────────────────────

export default function Calls() {
    const [data, setData] = useState<CallsResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

    // Filters
    const [statusFilter, setStatusFilter] = useState<string>("")
    const [searchQuery, setSearchQuery] = useState("")
    const [searchInput, setSearchInput] = useState("")

    const fetchCalls = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const result = await listCalls({
                status: statusFilter || undefined,
                search: searchQuery || undefined,
            })
            setData(result)
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to load calls"
            setError(message)
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [statusFilter, searchQuery])

    useEffect(() => {
        fetchCalls()
    }, [fetchCalls])

    // Auto-refresh every 30s
    useEffect(() => {
        const interval = setInterval(fetchCalls, 30_000)
        return () => clearInterval(interval)
    }, [fetchCalls])

    const toggleCard = (id: string) => {
        setExpandedIds((prev) => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    }

    const handleSearch = () => {
        setSearchQuery(searchInput)
    }

    const clearFilters = () => {
        setStatusFilter("")
        setSearchInput("")
        setSearchQuery("")
    }

    const filterByStatus = (status: string) => {
        setStatusFilter((prev) => (prev === status ? "" : status))
    }

    const priorityCalls = data?.calls.filter((c) => c.next_action) ?? []

    return (
        <div className="flex-1 space-y-6 p-8 pt-6">
            {/* Header */}
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Patient Calls</h2>
                <p className="text-muted-foreground">
                    Real-time call intelligence from GoHighLevel
                </p>
            </div>

            {/* Error */}
            {error && (
                <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    {error}
                </div>
            )}

            {/* Stats Cards Grid */}
            <div className="grid gap-3 grid-cols-2 md:grid-cols-3 lg:grid-cols-6">
                {STATUS_KEYS.map((key) => {
                    const cfg = STATUS_CONFIG[key]
                    const Icon = cfg.icon
                    const count = data?.counts[key] ?? 0
                    const isActive = statusFilter === key
                    return (
                        <Card
                            key={key}
                            className={`cursor-pointer transition-all hover:shadow-md ${isActive ? `ring-2 ring-primary shadow-md` : ""
                                }`}
                            onClick={() => filterByStatus(key)}
                        >
                            {loading ? (
                                <CardContent className="p-4 space-y-2">
                                    <Skeleton className="h-4 w-16" />
                                    <Skeleton className="h-8 w-10" />
                                </CardContent>
                            ) : (
                                <CardContent className="p-4">
                                    <div className="flex items-center justify-between mb-1">
                                        <span className={`text-xs font-medium ${cfg.color}`}>
                                            {cfg.label}
                                        </span>
                                        <div className={`rounded p-1 ${cfg.bgColor}`}>
                                            <Icon className={`h-3.5 w-3.5 ${cfg.color}`} />
                                        </div>
                                    </div>
                                    <div className="text-2xl font-bold tabular-nums">
                                        {count}
                                    </div>
                                </CardContent>
                            )}
                        </Card>
                    )
                })}
            </div>

            {/* Filters */}
            <Card>
                <CardContent className="p-4">
                    <div className="flex items-center gap-3 flex-wrap">
                        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
                            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
                            <Input
                                placeholder="Search by patient name…"
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                                className="h-9"
                            />
                        </div>
                        <Select value={statusFilter} onValueChange={setStatusFilter}>
                            <SelectTrigger className="w-[200px] h-9">
                                <SelectValue placeholder="All Statuses" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Statuses</SelectItem>
                                {STATUS_KEYS.map((key) => (
                                    <SelectItem key={key} value={key}>
                                        {STATUS_CONFIG[key].label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Button size="sm" variant="outline" onClick={handleSearch} className="h-9">
                            <Search className="h-3.5 w-3.5 mr-1.5" />
                            Search
                        </Button>
                        {(statusFilter || searchQuery) && (
                            <Button size="sm" variant="ghost" onClick={clearFilters} className="h-9 gap-1">
                                <X className="h-3.5 w-3.5" />
                                Clear
                            </Button>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Main Content Grid */}
            <div className="grid gap-6 lg:grid-cols-7">
                {/* Calls List — 5 cols */}
                <div className="lg:col-span-5 space-y-3">
                    <div className="flex items-center justify-between">
                        <h3 className="font-semibold flex items-center gap-2">
                            📋 Patient Calls
                            <Badge variant="secondary" className="tabular-nums">
                                {data?.total ?? 0}
                            </Badge>
                        </h3>
                    </div>

                    {loading ? (
                        <div className="space-y-3">
                            {[1, 2, 3, 4].map((i) => (
                                <Card key={i}>
                                    <CardContent className="p-4 space-y-3">
                                        <div className="flex justify-between">
                                            <Skeleton className="h-5 w-40" />
                                            <Skeleton className="h-5 w-24" />
                                        </div>
                                        <Skeleton className="h-4 w-full" />
                                        <Skeleton className="h-3 w-32" />
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    ) : data?.calls.length === 0 ? (
                        <Card>
                            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                                <Phone className="h-10 w-10 text-muted-foreground/40 mb-3" />
                                <p className="font-medium">No calls found</p>
                                <p className="text-sm text-muted-foreground mt-1">
                                    {statusFilter || searchQuery
                                        ? "Try adjusting your filters"
                                        : "Calls will appear here once your GHL integration is active"}
                                </p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="space-y-3">
                            {data?.calls.map((call) => (
                                <CallCard
                                    key={call.id}
                                    call={call}
                                    isExpanded={expandedIds.has(call.id)}
                                    onToggle={() => toggleCard(call.id)}
                                />
                            ))}
                        </div>
                    )}
                </div>

                {/* Priority Sidebar — 2 cols */}
                <div className="lg:col-span-2">
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-sm flex items-center gap-1.5">
                                <Zap className="h-4 w-4 text-amber-500" />
                                Priority Actions
                            </CardTitle>
                            <CardDescription className="text-xs">
                                Urgent items requiring attention
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {loading ? (
                                [1, 2, 3].map((i) => (
                                    <div key={i} className="space-y-2">
                                        <Skeleton className="h-4 w-28" />
                                        <Skeleton className="h-3 w-full" />
                                        <Separator />
                                    </div>
                                ))
                            ) : priorityCalls.length === 0 ? (
                                <div className="text-center py-6">
                                    <CheckCircle2 className="h-8 w-8 text-emerald-500/40 mx-auto mb-2" />
                                    <p className="text-xs text-muted-foreground">
                                        No urgent actions right now
                                    </p>
                                </div>
                            ) : (
                                priorityCalls.slice(0, 10).map((call, idx) => {
                                    const statusKey = call.call_status_normalized || "no_action"
                                    const cfg = STATUS_CONFIG[statusKey] || STATUS_CONFIG.no_action
                                    return (
                                        <div key={call.id}>
                                            <div className="space-y-1">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-sm font-medium truncate flex-1">
                                                        {call.patient_name}
                                                    </span>
                                                    <Badge
                                                        variant="outline"
                                                        className={`text-[10px] shrink-0 ${cfg.color} ${cfg.bgColor} ${cfg.borderColor}`}
                                                    >
                                                        {cfg.label}
                                                    </Badge>
                                                </div>
                                                <p className="text-xs text-muted-foreground line-clamp-2">
                                                    {call.next_action}
                                                </p>
                                            </div>
                                            {idx < priorityCalls.length - 1 && (
                                                <Separator className="mt-3" />
                                            )}
                                        </div>
                                    )
                                })
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    )
}
