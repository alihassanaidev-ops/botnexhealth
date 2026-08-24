/**
 * Shared conversation inbox.
 *
 * Shows SMS and email side by side, because they are the same conversation with
 * a patient and splitting them into two queues means someone answers twice or
 * not at all.
 *
 * Group admins never reach the conversation view — the API refuses it for that
 * role — so they get the activity summary instead: volumes and response times,
 * no patient content.
 */
import { useCallback, useEffect, useMemo, useState } from "react"
import {
    AlertTriangle,
    CheckCircle2,
    Inbox as InboxIcon,
    Loader2,
    Mail,
    MessageSquare,
    RefreshCw,
    UserCheck,
} from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { CardsSkeleton } from "@/components/ui/skeletons"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/context/AuthContext"
import {
    assignInboxThread,
    getInboxActivity,
    getInboxThread,
    listInboxThreads,
    resolveInboxThread,
    type InboxActivity,
    type InboxChannel,
    type InboxThread,
    type InboxThreadDetail,
} from "@/lib/inbox-api"

function errorMessage(err: unknown, fallback: string): string {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
        ?.detail
    return typeof detail === "string" ? detail : fallback
}

function ChannelIcon({ channel }: { channel: InboxChannel }) {
    return channel === "email" ? (
        <Mail className="h-3.5 w-3.5" />
    ) : (
        <MessageSquare className="h-3.5 w-3.5" />
    )
}

function formatDuration(seconds: number | null): string {
    if (seconds === null) return "—"
    const hours = seconds / 3600
    if (hours < 1) return `${Math.round(seconds / 60)}m`
    if (hours < 48) return `${hours.toFixed(1)}h`
    return `${(hours / 24).toFixed(1)}d`
}

/** Group oversight view — figures only, by design. */
function ActivitySummary() {
    const [activity, setActivity] = useState<InboxActivity | null>(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        getInboxActivity(30)
            .then(setActivity)
            .catch(() => toast.error("Failed to load activity"))
            .finally(() => setLoading(false))
    }, [])

    if (loading) return <CardsSkeleton />
    if (!activity) return null

    return (
        <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
                {[
                    { label: "Conversations", value: activity.threads },
                    { label: "Still open", value: activity.open_threads },
                    { label: "Awaiting a reply", value: activity.unresolved_handoffs },
                ].map((stat) => (
                    <Card key={stat.label}>
                        <CardContent className="py-4">
                            <p className="text-xs text-muted-foreground">{stat.label}</p>
                            <p className="text-2xl font-semibold">{stat.value}</p>
                        </CardContent>
                    </Card>
                ))}
            </div>

            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-base">By practice</CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Last {activity.days} days. Message content stays with the practice.
                    </p>
                </CardHeader>
                <CardContent className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="text-xs text-muted-foreground">
                            <tr className="border-b border-border">
                                <th className="px-2 py-1.5 text-left font-medium">Practice</th>
                                <th className="px-2 py-1.5 text-left font-medium">Location</th>
                                <th className="px-2 py-1.5 text-left font-medium">Channel</th>
                                <th className="px-2 py-1.5 text-right font-medium">Total</th>
                                <th className="px-2 py-1.5 text-right font-medium">Open</th>
                                <th className="px-2 py-1.5 text-right font-medium">Avg. to resolve</th>
                            </tr>
                        </thead>
                        <tbody>
                            {activity.breakdown.length === 0 && (
                                <tr>
                                    <td colSpan={6} className="py-6 text-center text-muted-foreground">
                                        No conversations in this period.
                                    </td>
                                </tr>
                            )}
                            {activity.breakdown.map((row, i) => (
                                <tr key={i} className="border-b border-border last:border-0">
                                    <td className="px-2 py-1.5 font-mono text-xs">
                                        {row.institution_id.slice(0, 8)}
                                    </td>
                                    <td className="px-2 py-1.5 font-mono text-xs">
                                        {row.location_id ? row.location_id.slice(0, 8) : "—"}
                                    </td>
                                    <td className="px-2 py-1.5">{row.channel}</td>
                                    <td className="px-2 py-1.5 text-right">{row.threads}</td>
                                    <td className="px-2 py-1.5 text-right">{row.open_threads}</td>
                                    <td className="px-2 py-1.5 text-right">
                                        {formatDuration(row.avg_resolution_seconds)}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </CardContent>
            </Card>
        </div>
    )
}

export default function Inbox() {
    const { user } = useAuth()
    const isGroupAdmin = user?.role === "GROUP_ADMIN"

    const [threads, setThreads] = useState<InboxThread[]>([])
    const [loading, setLoading] = useState(true)
    const [selectedId, setSelectedId] = useState<string | null>(null)
    const [detail, setDetail] = useState<InboxThreadDetail | null>(null)
    const [detailLoading, setDetailLoading] = useState(false)
    const [busy, setBusy] = useState(false)
    const [channel, setChannel] = useState<"all" | InboxChannel>("all")
    const [unresolvedOnly, setUnresolvedOnly] = useState(true)

    const canAssign =
        user?.role === "SUPER_ADMIN" ||
        user?.role === "INSTITUTION_ADMIN" ||
        user?.role === "LOCATION_ADMIN"

    const load = useCallback(async () => {
        setLoading(true)
        try {
            setThreads(
                await listInboxThreads({
                    channel: channel === "all" ? undefined : channel,
                    unresolved_only: unresolvedOnly,
                }),
            )
        } catch (err) {
            toast.error(errorMessage(err, "Failed to load the inbox"))
        } finally {
            setLoading(false)
        }
    }, [channel, unresolvedOnly])

    useEffect(() => {
        if (!isGroupAdmin) void load()
    }, [isGroupAdmin, load])

    const openThread = async (id: string) => {
        setSelectedId(id)
        setDetailLoading(true)
        try {
            setDetail(await getInboxThread(id))
        } catch (err) {
            toast.error(errorMessage(err, "Failed to open the conversation"))
            setDetail(null)
        } finally {
            setDetailLoading(false)
        }
    }

    const resolve = async () => {
        if (!selectedId) return
        setBusy(true)
        try {
            await resolveInboxThread(selectedId, "resolved_by_staff")
            toast.success("Conversation resolved")
            setDetail(null)
            setSelectedId(null)
            await load()
        } catch (err) {
            toast.error(errorMessage(err, "Could not resolve"))
        } finally {
            setBusy(false)
        }
    }

    const claim = async () => {
        if (!selectedId || !user) return
        setBusy(true)
        try {
            await assignInboxThread(selectedId, String(user.id))
            toast.success("Assigned to you")
            await openThread(selectedId)
            await load()
        } catch (err) {
            toast.error(errorMessage(err, "Could not assign"))
        } finally {
            setBusy(false)
        }
    }

    const selected = useMemo(
        () => threads.find((t) => t.id === selectedId) ?? null,
        [threads, selectedId],
    )

    if (isGroupAdmin) {
        return (
            <div className="space-y-6">
                <PageHeader
                    icon={InboxIcon}
                    title="Patient Conversations"
                    description="Volumes and response times across your practices."
                />
                <ActivitySummary />
            </div>
        )
    }

    return (
        <div className="space-y-6">
            <PageHeader
                icon={InboxIcon}
                title="Inbox"
                description="Patient replies across email and SMS, in one place."
                actions={
                    <Button variant="outline" onClick={() => void load()} disabled={loading}>
                        <RefreshCw className="mr-2 h-4 w-4" />
                        Refresh
                    </Button>
                }
            />

            <div className="flex flex-wrap items-center gap-4">
                <Select value={channel} onValueChange={(v) => setChannel(v as typeof channel)}>
                    <SelectTrigger className="w-40">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All channels</SelectItem>
                        <SelectItem value="email">Email</SelectItem>
                        <SelectItem value="sms">SMS</SelectItem>
                    </SelectContent>
                </Select>
                <div className="flex items-center gap-2">
                    <Switch
                        id="unresolved"
                        checked={unresolvedOnly}
                        onCheckedChange={setUnresolvedOnly}
                    />
                    <Label htmlFor="unresolved" className="text-sm">
                        Needs attention only
                    </Label>
                </div>
            </div>

            {loading ? (
                <CardsSkeleton />
            ) : (
                <div className="grid gap-6 lg:grid-cols-[1fr,1.4fr]">
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-base">
                                Conversations ({threads.length})
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="max-h-[36rem] space-y-1 overflow-y-auto">
                            {threads.length === 0 && (
                                <p className="py-8 text-center text-sm text-muted-foreground">
                                    Nothing waiting. Patient replies will appear here.
                                </p>
                            )}
                            {threads.map((thread) => (
                                <button
                                    key={thread.id}
                                    type="button"
                                    onClick={() => void openThread(thread.id)}
                                    className={`w-full rounded-md border px-3 py-2 text-left transition-colors ${
                                        thread.id === selectedId
                                            ? "border-primary bg-muted"
                                            : "border-border hover:bg-muted/50"
                                    }`}
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="flex min-w-0 items-center gap-1.5">
                                            <ChannelIcon channel={thread.channel} />
                                            <span className="truncate text-sm font-medium">
                                                {thread.contact_name ??
                                                    thread.contact_masked_email ??
                                                    "Unknown patient"}
                                            </span>
                                        </span>
                                        {thread.unresolved_handoffs > 0 && (
                                            <Badge variant="secondary">Needs reply</Badge>
                                        )}
                                    </div>
                                    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                                        {thread.latest_intent && <span>{thread.latest_intent}</span>}
                                        {thread.last_message_at && (
                                            <span>
                                                {new Date(thread.last_message_at).toLocaleString()}
                                            </span>
                                        )}
                                    </div>
                                    {thread.sender_mismatch && (
                                        <p className="mt-1 flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
                                            <AlertTriangle className="h-3 w-3" />
                                            Replied from a different address
                                        </p>
                                    )}
                                </button>
                            ))}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-base">
                                {selected
                                    ? selected.contact_name ??
                                      selected.contact_masked_email ??
                                      "Conversation"
                                    : "Select a conversation"}
                            </CardTitle>
                            {selected?.sender_mismatch && (
                                <p className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                                    <AlertTriangle className="h-3.5 w-3.5" />
                                    The latest reply came from a different address than the
                                    patient on file. Confirm who you are speaking to before
                                    sharing anything personal.
                                </p>
                            )}
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {detailLoading && (
                                <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
                            )}

                            {!detailLoading && !detail && (
                                <p className="py-10 text-center text-sm text-muted-foreground">
                                    Choose a conversation to read it.
                                </p>
                            )}

                            {!detailLoading && detail && (
                                <>
                                    <div className="max-h-[24rem] space-y-3 overflow-y-auto">
                                        {detail.messages.length === 0 && (
                                            <p className="text-sm text-muted-foreground">
                                                No messages recorded on this conversation yet.
                                            </p>
                                        )}
                                        {detail.messages.map((message) => (
                                            <div
                                                key={message.id}
                                                className="rounded-md border border-border px-3 py-2"
                                            >
                                                <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                                                    <span className="flex items-center gap-1.5">
                                                        <ChannelIcon channel={message.channel} />
                                                        {message.from_masked ?? "Patient"}
                                                    </span>
                                                    {message.created_at && (
                                                        <span>
                                                            {new Date(message.created_at).toLocaleString()}
                                                        </span>
                                                    )}
                                                </div>
                                                {message.subject && (
                                                    <p className="mt-1 text-sm font-medium">
                                                        {message.subject}
                                                    </p>
                                                )}
                                                <p className="mt-1 whitespace-pre-wrap text-sm">
                                                    {message.body ?? "(no content)"}
                                                </p>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="flex flex-wrap gap-2">
                                        {canAssign && (
                                            <Button
                                                variant="outline"
                                                onClick={() => void claim()}
                                                disabled={busy}
                                            >
                                                <UserCheck className="mr-2 h-4 w-4" />
                                                Assign to me
                                            </Button>
                                        )}
                                        <Button onClick={() => void resolve()} disabled={busy}>
                                            <CheckCircle2 className="mr-2 h-4 w-4" />
                                            Mark resolved
                                        </Button>
                                    </div>

                                    <p className="text-xs text-muted-foreground">
                                        Replying to the patient is done from your own email —
                                        a copy of every reply was forwarded to the clinic
                                        inbox. In-app replying is not enabled yet.
                                    </p>
                                </>
                            )}
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    )
}
