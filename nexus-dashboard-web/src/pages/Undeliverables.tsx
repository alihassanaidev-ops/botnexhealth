import { useCallback, useEffect, useMemo, useState } from "react"
import { format } from "date-fns"
import { AlertTriangle, ChevronLeft, ChevronRight, RefreshCw, RotateCcw, XCircle } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { TableSkeleton } from "@/components/ui/skeletons"
import { Textarea } from "@/components/ui/textarea"
import { useAuth } from "@/context/AuthContext"
import {
    dismissUndeliverable,
    listUndeliverables,
    retryUndeliverable,
    type DismissalReason,
    type UndeliverableEvent,
    type UndeliverableScope,
    type UndeliverableStatus,
} from "@/lib/undeliverables-api"

const PAGE_SIZE = 50

const reasonLabels: Record<DismissalReason, string> = {
    resolved_elsewhere: "Resolved outside the platform",
    duplicate: "Duplicate event",
    not_actionable: "No action needed",
    superseded: "Superseded by newer work",
    other: "Other",
}

function sourceLabel(source: string): string {
    return source.replace(/_/g, " ").replace(/\b\w/g, (letter: string) => letter.toUpperCase())
}

function statusBadge(status: UndeliverableStatus) {
    if (status === "open") return <Badge variant="destructive">Needs review</Badge>
    if (status === "replayed") return <Badge className="bg-emerald-600">Retried</Badge>
    return <Badge variant="secondary">Dismissed</Badge>
}

export default function Undeliverables() {
    const { user } = useAuth()
    const scope: UndeliverableScope = user?.role === "SUPER_ADMIN" ? "platform" : "institution"
    const canReplay = user?.role === "SUPER_ADMIN" || user?.role === "INSTITUTION_ADMIN"
    const [items, setItems] = useState<UndeliverableEvent[]>([])
    const [statusFilter, setStatusFilter] = useState<UndeliverableStatus | "all">("open")
    const [page, setPage] = useState(1)
    const [pages, setPages] = useState(0)
    const [total, setTotal] = useState(0)
    const [loading, setLoading] = useState(true)
    const [busyId, setBusyId] = useState<string | null>(null)
    const [dismissTarget, setDismissTarget] = useState<UndeliverableEvent | null>(null)
    const [dismissReason, setDismissReason] = useState<DismissalReason>("resolved_elsewhere")
    const [dismissNote, setDismissNote] = useState("")

    const description = useMemo(
        () => scope === "platform"
            ? "Review permanently failed work across practices, then retry or dismiss it with a durable record."
            : "Review permanently failed work for your practice, then retry or dismiss it with a durable record.",
        [scope],
    )

    const load = useCallback(async (nextPage = page) => {
        setLoading(true)
        try {
            const result = await listUndeliverables(scope, {
                page: nextPage,
                size: PAGE_SIZE,
                status: statusFilter,
            })
            setItems(result.items)
            setTotal(result.total)
            setPages(result.pages)
            setPage(result.page)
        } catch {
            toast.error("Failed to load undeliverable work.")
        } finally {
            setLoading(false)
        }
    }, [page, scope, statusFilter])

    useEffect(() => {
        void load(1)
        // `page` is deliberately not a dependency: filter changes reset it,
        // while pagination calls load directly with the requested page.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, statusFilter])

    async function retry(item: UndeliverableEvent) {
        if (busyId || !canReplay || !item.replay_supported) return
        setBusyId(item.id)
        try {
            await retryUndeliverable(scope, item.id)
            toast.success("The event was queued once for retry.")
            await load(page)
        } catch {
            toast.error("The event could not be retried.")
        } finally {
            setBusyId(null)
        }
    }

    async function dismiss() {
        if (!dismissTarget || busyId) return
        setBusyId(dismissTarget.id)
        try {
            await dismissUndeliverable(scope, dismissTarget.id, {
                reason: dismissReason,
                ...(dismissNote.trim() ? { note: dismissNote.trim() } : {}),
            })
            toast.success("The event was dismissed and the reason was recorded.")
            setDismissTarget(null)
            setDismissNote("")
            setDismissReason("resolved_elsewhere")
            await load(page)
        } catch {
            toast.error("The event could not be dismissed.")
        } finally {
            setBusyId(null)
        }
    }

    return (
        <div className="space-y-6">
            <PageHeader
                icon={AlertTriangle}
                title="Undeliverable work"
                description={description}
                actions={
                    <div className="flex items-center gap-2">
                        <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as UndeliverableStatus | "all")}>
                            <SelectTrigger className="w-40" aria-label="Status filter">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="open">Needs review</SelectItem>
                                <SelectItem value="replayed">Retried</SelectItem>
                                <SelectItem value="discarded">Dismissed</SelectItem>
                                <SelectItem value="all">All statuses</SelectItem>
                            </SelectContent>
                        </Select>
                        <Button variant="outline" onClick={() => void load(page)} disabled={loading}>
                            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </div>
                }
            />

            <Card>
                <CardHeader>
                    <CardTitle>Operator queue</CardTitle>
                    <CardDescription>
                        Retry only after correcting the recorded cause. Unsupported event types remain available for investigation and dismissal.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading && items.length === 0 ? (
                        <TableSkeleton rows={6} cols={6} />
                    ) : items.length === 0 ? (
                        <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
                            No undeliverable work matches this status.
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <div className="overflow-x-auto rounded-lg border">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Failed</TableHead>
                                            <TableHead>Source</TableHead>
                                            <TableHead>Failure</TableHead>
                                            <TableHead>Campaign run</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {items.map((item) => (
                                            <TableRow key={item.id}>
                                                <TableCell className="whitespace-nowrap align-top text-sm">
                                                    {format(new Date(item.created_at), "MMM d, yyyy h:mm a")}
                                                    <div className="mt-1 text-xs text-muted-foreground">{item.attempts} attempt{item.attempts === 1 ? "" : "s"}</div>
                                                </TableCell>
                                                <TableCell className="align-top">
                                                    <div className="font-medium">{sourceLabel(item.source)}</div>
                                                    <div className="font-mono text-xs text-muted-foreground">{item.event_type}</div>
                                                </TableCell>
                                                <TableCell className="max-w-md align-top">
                                                    <p className="text-sm text-destructive">{item.last_error}</p>
                                                    {item.redacted_payload && (
                                                        <details className="mt-2 text-xs text-muted-foreground">
                                                            <summary className="cursor-pointer select-none">Redacted context</summary>
                                                            <pre className="mt-2 max-w-md overflow-auto rounded bg-muted p-2 whitespace-pre-wrap break-all">
                                                                {JSON.stringify(item.redacted_payload, null, 2)}
                                                            </pre>
                                                        </details>
                                                    )}
                                                    {item.resolution_reason && (
                                                        <p className="mt-2 text-xs text-muted-foreground">
                                                            Dismissal: {reasonLabels[item.resolution_reason]}
                                                            {item.resolution_note ? ` — ${item.resolution_note}` : ""}
                                                        </p>
                                                    )}
                                                </TableCell>
                                                <TableCell className="align-top font-mono text-xs">
                                                    {item.originating_run_id ?? "Not a campaign event"}
                                                </TableCell>
                                                <TableCell className="align-top">{statusBadge(item.status)}</TableCell>
                                                <TableCell className="align-top">
                                                    {item.status === "open" && (
                                                        <div className="flex justify-end gap-2">
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                onClick={() => void retry(item)}
                                                                disabled={busyId !== null || !canReplay || !item.replay_supported}
                                                                title={!canReplay
                                                                    ? "An institution administrator is required to retry"
                                                                    : !item.replay_supported
                                                                        ? "This event type cannot be retried automatically"
                                                                        : "Retry once"}
                                                            >
                                                                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                                                                Retry
                                                            </Button>
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                onClick={() => setDismissTarget(item)}
                                                                disabled={busyId !== null}
                                                            >
                                                                <XCircle className="mr-1.5 h-3.5 w-3.5" />
                                                                Dismiss
                                                            </Button>
                                                        </div>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>

                            <div className="flex items-center justify-between text-sm text-muted-foreground">
                                <span>{total} event{total === 1 ? "" : "s"}</span>
                                <div className="flex items-center gap-2">
                                    <Button variant="outline" size="sm" onClick={() => void load(page - 1)} disabled={loading || page <= 1}>
                                        <ChevronLeft className="mr-1 h-4 w-4" /> Previous
                                    </Button>
                                    <span>Page {page} of {Math.max(pages, 1)}</span>
                                    <Button variant="outline" size="sm" onClick={() => void load(page + 1)} disabled={loading || page >= pages}>
                                        Next <ChevronRight className="ml-1 h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={dismissTarget !== null} onOpenChange={(open) => !open && setDismissTarget(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Dismiss undeliverable event</DialogTitle>
                        <DialogDescription>
                            This removes the event from the review queue without retrying it. A reason is required and recorded in the audit trail.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <div className="space-y-2">
                            <Label htmlFor="dismiss-reason">Reason</Label>
                            <Select value={dismissReason} onValueChange={(value) => setDismissReason(value as DismissalReason)}>
                                <SelectTrigger id="dismiss-reason"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {Object.entries(reasonLabels).map(([value, label]) => (
                                        <SelectItem key={value} value={value}>{label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="dismiss-note">Note (optional)</Label>
                            <Textarea
                                id="dismiss-note"
                                value={dismissNote}
                                onChange={(event) => setDismissNote(event.target.value)}
                                maxLength={1000}
                                placeholder="Add context for the next operator."
                            />
                            <p className="text-xs text-muted-foreground">The note is encrypted and is not copied into audit metadata.</p>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDismissTarget(null)}>Cancel</Button>
                        <Button variant="destructive" onClick={() => void dismiss()} disabled={busyId !== null}>Dismiss event</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
