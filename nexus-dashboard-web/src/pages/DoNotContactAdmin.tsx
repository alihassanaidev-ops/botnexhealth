import { useEffect, useMemo, useState } from "react"
import {
    Loader2,
    Mail,
    MessageSquare,
    Phone,
    Plus,
    RefreshCcw,
    Search,
    ShieldOff,
} from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import {
    listInstitutionPortalLocations,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"
import {
    createDoNotContact,
    listDoNotContact,
    releaseDoNotContact,
    type DncScope,
} from "@/lib/do-not-contact-api"
import type { DncChannel, DncChannelRecord, DncPatientRecord } from "@/types"
import { toast } from "sonner"

type ChannelFilter = "all" | Exclude<DncChannel, "all">

interface CreateFormState {
    phone: string
    scope: DncScope
    locationId: string
    reason: string
}

const EMPTY_FORM: CreateFormState = {
    phone: "",
    scope: "institution",
    locationId: "",
    reason: "",
}

const CHANNEL_LABELS: Record<DncChannel, string> = {
    sms: "SMS",
    voice: "Voice",
    email: "Email",
    all: "All channels",
}

const CHANNEL_ICONS: Record<DncChannel, typeof MessageSquare> = {
    sms: MessageSquare,
    voice: Phone,
    email: Mail,
    all: ShieldOff,
}

const CHANNEL_STYLES: Record<DncChannel, string> = {
    sms: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300",
    voice: "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300",
    email: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    all: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
}

function errorDetail(error: unknown, fallback: string): string {
    const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    return detail ?? (error instanceof Error ? error.message : fallback)
}

function formatDate(value: string): string {
    return new Date(value).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
    })
}

function displayIdentity(patient: DncPatientRecord): string {
    return patient.phone_masked ?? patient.email_masked ?? "No contact identifier"
}

function ChannelTag({
    entry,
    locationName,
    onRemove,
}: {
    entry: DncChannelRecord
    locationName: string | null
    onRemove: () => void
}) {
    const Icon = CHANNEL_ICONS[entry.channel]
    const scopeLabel = entry.scope === "location" && locationName ? ` · ${locationName}` : ""
    return (
        <div className="inline-flex items-center gap-1.5">
            <Badge
                variant="outline"
                className={`gap-1 py-1 font-medium ${CHANNEL_STYLES[entry.channel]}`}
                title={`${entry.reason ?? "Patient opted out"}${scopeLabel}`}
            >
                <Icon className="h-3 w-3" />
                {CHANNEL_LABELS[entry.channel]}
                {entry.scope === "location" && locationName && (
                    <span className="max-w-28 truncate opacity-70">· {locationName}</span>
                )}
            </Badge>
            <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={onRemove}
                aria-label={`Remove ${CHANNEL_LABELS[entry.channel]} DNC tag`}
            >
                Remove
            </Button>
        </div>
    )
}

export default function DoNotContactAdmin() {
    const [records, setRecords] = useState<DncPatientRecord[]>([])
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [loading, setLoading] = useState(true)
    const [search, setSearch] = useState("")
    const [channel, setChannel] = useState<ChannelFilter>("all")
    const [formOpen, setFormOpen] = useState(false)
    const [form, setForm] = useState<CreateFormState>(EMPTY_FORM)
    const [saving, setSaving] = useState(false)
    const [releaseTarget, setReleaseTarget] = useState<{
        patient: DncPatientRecord
        entry: DncChannelRecord
    } | null>(null)
    const [releasing, setReleasing] = useState(false)

    async function refresh() {
        setLoading(true)
        try {
            setRecords(await listDoNotContact())
        } catch (error) {
            toast.error(errorDetail(error, "Failed to load DNC patients"))
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        void refresh()
        listInstitutionPortalLocations()
            .then(setLocations)
            .catch(() => setLocations([]))
    }, [])

    const locationNames = useMemo(
        () => new Map(locations.map((location) => [location.id, location.name])),
        [locations],
    )

    const filteredRecords = useMemo(() => {
        const query = search.trim().toLowerCase()
        return records.filter((patient) => {
            const matchesChannel =
                channel === "all"
                || patient.channels.some(
                    (entry) => entry.channel === channel || entry.channel === "all",
                )
            const matchesSearch =
                !query
                || patient.patient_name?.toLowerCase().includes(query)
                || patient.phone_masked?.toLowerCase().includes(query)
                || patient.email_masked?.toLowerCase().includes(query)
            return matchesChannel && Boolean(matchesSearch)
        })
    }, [channel, records, search])

    const tagCount = records.reduce((count, patient) => count + patient.channels.length, 0)

    async function handleCreate() {
        const phone = form.phone.trim()
        if (!phone) {
            toast.error("Phone is required")
            return
        }
        if (form.scope === "location" && !form.locationId) {
            toast.error("Location scope requires a location")
            return
        }
        setSaving(true)
        try {
            await createDoNotContact({
                phone,
                scope: form.scope,
                location_id: form.scope === "location" ? form.locationId : null,
                reason: form.reason.trim() || null,
            })
            toast.success("All-channel DNC recorded")
            setFormOpen(false)
            setForm(EMPTY_FORM)
            await refresh()
        } catch (error) {
            toast.error(errorDetail(error, "Failed to record DNC"))
        } finally {
            setSaving(false)
        }
    }

    async function handleRelease() {
        if (!releaseTarget) return
        setReleasing(true)
        try {
            const released = await releaseDoNotContact(
                releaseTarget.entry.record_type,
                releaseTarget.entry.id,
            )
            if (released) {
                toast.success(`${CHANNEL_LABELS[releaseTarget.entry.channel]} DNC tag removed`)
            } else {
                toast.info("This DNC tag was already removed")
            }
            setReleaseTarget(null)
            await refresh()
        } catch (error) {
            toast.error(errorDetail(error, "Failed to remove DNC tag"))
        } finally {
            setReleasing(false)
        }
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="pointer-events-none fixed inset-0 overflow-hidden">
                <div className="absolute -right-32 -top-32 h-[420px] w-[420px] rounded-full bg-transparent blur-[100px] dark:bg-violet-700/20" />
            </div>

            <PageHeader
                icon={ShieldOff}
                title="DNC Patients"
                description="Review patients who opted out and remove an individual SMS, voice, or email restriction when they ask to opt back in."
                actions={(
                    <>
                        <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="gap-1.5">
                            <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                        <Button
                            size="sm"
                            className="gap-1.5"
                            onClick={() => {
                                setForm(EMPTY_FORM)
                                setFormOpen(true)
                            }}
                        >
                            <Plus className="h-4 w-4" />
                            Add DNC
                        </Button>
                    </>
                )}
            />

            <Card>
                <CardContent className="p-0">
                    <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between">
                        <div className="relative w-full sm:max-w-sm">
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                                placeholder="Search patient or masked contact"
                                className="pl-9"
                                aria-label="Search DNC patients"
                            />
                        </div>
                        <div className="flex items-center gap-3">
                            <Select value={channel} onValueChange={(value) => setChannel(value as ChannelFilter)}>
                                <SelectTrigger className="w-40" aria-label="Filter by channel">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All channels</SelectItem>
                                    <SelectItem value="sms">SMS</SelectItem>
                                    <SelectItem value="voice">Voice</SelectItem>
                                    <SelectItem value="email">Email</SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="whitespace-nowrap text-xs text-muted-foreground">
                                <span className="font-medium text-foreground">{records.length}</span> patients · {tagCount} tags
                            </p>
                        </div>
                    </div>

                    {loading ? (
                        <div className="space-y-2 p-4">
                            {Array.from({ length: 5 }).map((_, index) => (
                                <Skeleton key={index} className="h-12 w-full" />
                            ))}
                        </div>
                    ) : filteredRecords.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 px-4 py-16 text-center text-muted-foreground">
                            <div className="grid size-12 place-items-center rounded-full bg-muted">
                                <ShieldOff className="h-6 w-6 opacity-40" />
                            </div>
                            <p className="text-sm font-medium text-foreground/70">
                                {records.length === 0 ? "No DNC patients" : "No matching DNC patients"}
                            </p>
                            <p className="text-xs">
                                {records.length === 0
                                    ? "Channel opt-outs will appear here automatically."
                                    : "Try another search or channel filter."}
                            </p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="pl-4">Patient</TableHead>
                                    <TableHead>Contact</TableHead>
                                    <TableHead>DNC channels</TableHead>
                                    <TableHead className="pr-4 text-right">Latest opt-out</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filteredRecords.map((patient) => (
                                    <TableRow key={patient.id}>
                                        <TableCell className="pl-4">
                                            <p className="font-medium">{patient.patient_name ?? "Unknown patient"}</p>
                                            {!patient.contact_id && (
                                                <p className="text-xs text-muted-foreground">Unmatched contact</p>
                                            )}
                                        </TableCell>
                                        <TableCell className="font-mono text-xs text-muted-foreground">
                                            <div>{displayIdentity(patient)}</div>
                                            {patient.phone_masked && patient.email_masked && (
                                                <div>{patient.email_masked}</div>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-wrap gap-1.5">
                                                {patient.channels.map((entry) => (
                                                    <ChannelTag
                                                        key={`${entry.record_type}:${entry.id}`}
                                                        entry={entry}
                                                        locationName={entry.location_id ? locationNames.get(entry.location_id) ?? null : null}
                                                        onRemove={() => setReleaseTarget({ patient, entry })}
                                                    />
                                                ))}
                                            </div>
                                        </TableCell>
                                        <TableCell className="pr-4 text-right text-xs text-muted-foreground">
                                            {formatDate(patient.latest_opt_out_at)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            <Dialog open={formOpen} onOpenChange={(open) => !open && setFormOpen(false)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Add all-channel DNC</DialogTitle>
                        <DialogDescription>
                            Use this when a patient asks staff not to contact them through any channel. Channel-specific reply opt-outs appear automatically.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Phone</label>
                            <Input
                                autoFocus
                                placeholder="+15551234567"
                                value={form.phone}
                                onChange={(event) => setForm((state) => ({ ...state, phone: event.target.value }))}
                            />
                        </div>
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Scope</label>
                            <Select
                                value={form.scope}
                                onValueChange={(value) => setForm((state) => ({ ...state, scope: value as DncScope }))}
                            >
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="institution">Institution (all locations)</SelectItem>
                                    <SelectItem value="location">Single location</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {form.scope === "location" && (
                            <div>
                                <label className="mb-1 block text-xs font-medium text-muted-foreground">Location</label>
                                <Select
                                    value={form.locationId}
                                    onValueChange={(value) => setForm((state) => ({ ...state, locationId: value }))}
                                >
                                    <SelectTrigger><SelectValue placeholder="Select location" /></SelectTrigger>
                                    <SelectContent>
                                        {locations.map((location) => (
                                            <SelectItem key={location.id} value={location.id}>{location.name}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Reason (optional)</label>
                            <Textarea
                                value={form.reason}
                                maxLength={500}
                                placeholder="Patient asked staff not to contact them"
                                onChange={(event) => setForm((state) => ({ ...state, reason: event.target.value }))}
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setFormOpen(false)} disabled={saving}>Cancel</Button>
                        <Button onClick={handleCreate} disabled={saving}>
                            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Record DNC
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={releaseTarget !== null} onOpenChange={(open) => !open && setReleaseTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Remove this DNC tag?</DialogTitle>
                        <DialogDescription>
                            {releaseTarget
                                ? releaseTarget.patient.patient_name ?? displayIdentity(releaseTarget.patient)
                                : "This patient"}{" "}
                            will be contactable through {releaseTarget
                                ? CHANNEL_LABELS[releaseTarget.entry.channel].toLowerCase()
                                : "this channel"} again.
                            {releaseTarget?.entry.channel !== "all" && " Other channel restrictions will stay active."}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReleaseTarget(null)} disabled={releasing}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleRelease} disabled={releasing}>
                            {releasing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Remove tag
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
