/**
 * Compliance settings — quiet-hours exceptions (Item 20).
 *
 * Quiet hours come from a location's weekly opening hours. This is where the
 * three things those hours cannot express are set: a specific date, a specific
 * patient, and a specific kind of message.
 *
 * The backend refuses an exception that would leave the clinic with no
 * permitted window at all — every message would then be held for a window that
 * never opens, and the campaign would go quiet with no error anywhere. Its
 * refusal explains what happened and what to change, so that message is shown
 * verbatim rather than replaced with something generic.
 */
import { useEffect, useMemo, useState } from "react"
import { CalendarOff, Clock, Plus, Trash2, User } from "lucide-react"
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
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
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
    createQuietHoursException,
    deleteQuietHoursException,
    listQuietHoursExceptions,
    type QuietHoursContentClass,
    type QuietHoursException,
} from "@/lib/quiet-hours-exceptions-api"
import { toast } from "sonner"

const ANY = "__any__"

const CONTENT_CLASSES: { value: QuietHoursContentClass; label: string }[] = [
    { value: "transactional_care", label: "Transactional care" },
    { value: "recall", label: "Recall" },
    { value: "sales", label: "Sales" },
    { value: "marketing", label: "Marketing" },
]

interface FormState {
    exception_date: string
    contact_id: string
    content_class: string
    is_blocked: boolean
    open_time: string
    close_time: string
    reason: string
}

const EMPTY_FORM: FormState = {
    exception_date: "",
    contact_id: "",
    content_class: ANY,
    is_blocked: true,
    open_time: "",
    close_time: "",
    reason: "",
}

/** The backend's 422 detail is written for the operator; prefer it to our own. */
function errorDetail(error: unknown, fallback: string): string {
    const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail
    return detail ?? (error instanceof Error ? error.message : fallback)
}

function formatTime(value: string | null): string {
    if (!value) return ""
    return value.slice(0, 5)
}

function describeWindow(row: QuietHoursException): string {
    if (row.is_blocked) return "No contact"
    const open = formatTime(row.open_time) || "00:00"
    const close = formatTime(row.close_time) || "23:59"
    return `${open} – ${close}`
}

export default function QuietHoursExceptions() {
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [locationId, setLocationId] = useState<string>("")
    const [rows, setRows] = useState<QuietHoursException[]>([])
    const [loading, setLoading] = useState(true)
    const [formOpen, setFormOpen] = useState(false)
    const [form, setForm] = useState<FormState>(EMPTY_FORM)
    const [saving, setSaving] = useState(false)
    const [deleting, setDeleting] = useState<string | null>(null)

    useEffect(() => {
        listInstitutionPortalLocations()
            .then((found) => {
                setLocations(found)
                if (found.length > 0) setLocationId(String(found[0].id))
                else setLoading(false)
            })
            .catch(() => {
                setLocations([])
                setLoading(false)
            })
    }, [])

    useEffect(() => {
        if (!locationId) return
        let cancelled = false
        setLoading(true)
        listQuietHoursExceptions(locationId)
            .then((found) => {
                if (!cancelled) setRows(found)
            })
            .catch((error) => {
                if (!cancelled) toast.error(errorDetail(error, "Failed to load exceptions"))
            })
            .finally(() => {
                if (!cancelled) setLoading(false)
            })
        return () => {
            cancelled = true
        }
    }, [locationId])

    async function refresh() {
        if (!locationId) return
        try {
            setRows(await listQuietHoursExceptions(locationId))
        } catch (error) {
            toast.error(errorDetail(error, "Failed to load exceptions"))
        }
    }

    async function handleSave() {
        if (!locationId) return
        if (!form.is_blocked && form.open_time && form.close_time) {
            if (form.open_time >= form.close_time) {
                toast.error("The closing time must be later than the opening time.")
                return
            }
        }
        setSaving(true)
        try {
            await createQuietHoursException({
                location_id: locationId,
                exception_date: form.exception_date || null,
                contact_id: form.contact_id.trim() || null,
                content_class:
                    form.content_class === ANY
                        ? null
                        : (form.content_class as QuietHoursContentClass),
                is_blocked: form.is_blocked,
                open_time: form.is_blocked ? null : form.open_time || null,
                close_time: form.is_blocked ? null : form.close_time || null,
                reason: form.reason.trim() || null,
            })
            toast.success("Exception saved")
            setFormOpen(false)
            setForm(EMPTY_FORM)
            await refresh()
        } catch (error) {
            // A rejection here is the useful case: the backend has worked out
            // that this rule would silence the clinic. Show it in full and keep
            // the dialog open so it can be corrected in place.
            toast.error(errorDetail(error, "This exception could not be saved"), {
                duration: 10_000,
            })
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(row: QuietHoursException) {
        setDeleting(row.id)
        try {
            await deleteQuietHoursException(row.id)
            toast.success("Exception removed")
            await refresh()
        } catch (error) {
            toast.error(errorDetail(error, "Failed to remove the exception"))
        } finally {
            setDeleting(null)
        }
    }

    const locationName = useMemo(
        () => locations.find((l) => String(l.id) === locationId)?.name ?? "",
        [locations, locationId],
    )

    return (
        <div className="space-y-6">
            <PageHeader
                title="Quiet-hours exceptions"
                description="Override a location's usual contact window for a date, a patient, or a kind of message."
            />

            <Card>
                <CardContent className="space-y-4 pt-6">
                    <div className="flex flex-wrap items-end justify-between gap-3">
                        <div className="space-y-1">
                            <Label htmlFor="qh-location">Location</Label>
                            <Select value={locationId} onValueChange={setLocationId}>
                                <SelectTrigger id="qh-location" className="w-[260px]">
                                    <SelectValue placeholder="Select a location" />
                                </SelectTrigger>
                                <SelectContent>
                                    {locations.map((location) => (
                                        <SelectItem key={location.id} value={String(location.id)}>
                                            {location.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <Button
                            onClick={() => {
                                setForm(EMPTY_FORM)
                                setFormOpen(true)
                            }}
                            disabled={!locationId}
                            className="gap-1.5"
                        >
                            <Plus className="h-4 w-4" /> Add exception
                        </Button>
                    </div>

                    {loading ? (
                        <div className="space-y-2">
                            <Skeleton className="h-10 w-full" />
                            <Skeleton className="h-10 w-full" />
                        </div>
                    ) : rows.length === 0 ? (
                        <p className="py-8 text-center text-sm text-muted-foreground">
                            No exceptions for {locationName || "this location"}. Its weekly
                            opening hours decide when patients may be contacted.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Applies to</TableHead>
                                    <TableHead>Date</TableHead>
                                    <TableHead>Messages</TableHead>
                                    <TableHead>Window</TableHead>
                                    <TableHead>Reason</TableHead>
                                    <TableHead className="w-[60px]" />
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {rows.map((row) => (
                                    <TableRow key={row.id}>
                                        <TableCell>
                                            {row.contact_id ? (
                                                <span className="flex items-center gap-1.5 text-sm">
                                                    <User className="h-3.5 w-3.5" /> One patient
                                                </span>
                                            ) : (
                                                <span className="text-sm text-muted-foreground">
                                                    Every patient
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            {row.exception_date ? (
                                                <span className="flex items-center gap-1.5 text-sm">
                                                    <CalendarOff className="h-3.5 w-3.5" />
                                                    {row.exception_date}
                                                </span>
                                            ) : (
                                                <span className="text-sm text-muted-foreground">
                                                    Every day
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            {row.content_class ? (
                                                <Badge variant="outline">
                                                    {CONTENT_CLASSES.find(
                                                        (c) => c.value === row.content_class,
                                                    )?.label ?? row.content_class}
                                                </Badge>
                                            ) : (
                                                <span className="text-sm text-muted-foreground">
                                                    All kinds
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <span className="flex items-center gap-1.5 text-sm">
                                                <Clock className="h-3.5 w-3.5" />
                                                {describeWindow(row)}
                                            </span>
                                        </TableCell>
                                        <TableCell className="max-w-[220px] truncate text-sm text-muted-foreground">
                                            {row.reason ?? "—"}
                                        </TableCell>
                                        <TableCell>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                aria-label="Remove exception"
                                                disabled={deleting === row.id}
                                                onClick={() => void handleDelete(row)}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}

                    <p className="text-xs text-muted-foreground">
                        More specific rules win: one written for a patient beats one for the
                        whole location, and a dated rule beats an undated one. A rule replaces
                        that day's window rather than narrowing it, so it can also permit a
                        send the opening hours would refuse — an early reminder, for instance.
                    </p>
                </CardContent>
            </Card>

            <Dialog open={formOpen} onOpenChange={setFormOpen}>
                <DialogContent className="sm:max-w-[520px]">
                    <DialogHeader>
                        <DialogTitle>Add a quiet-hours exception</DialogTitle>
                        <DialogDescription>
                            Leave a field blank to have the rule apply regardless of it.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div className="space-y-1.5">
                            <Label htmlFor="qh-date">Date</Label>
                            <Input
                                id="qh-date"
                                type="date"
                                value={form.exception_date}
                                onChange={(event) =>
                                    setForm({ ...form, exception_date: event.target.value })
                                }
                            />
                            <p className="text-xs text-muted-foreground">
                                Blank applies on every date. Set it for a public holiday.
                            </p>
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="qh-contact">Patient ID</Label>
                            <Input
                                id="qh-contact"
                                placeholder="Blank applies to every patient"
                                value={form.contact_id}
                                onChange={(event) =>
                                    setForm({ ...form, contact_id: event.target.value })
                                }
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="qh-class">Kind of message</Label>
                            <Select
                                value={form.content_class}
                                onValueChange={(value) => setForm({ ...form, content_class: value })}
                            >
                                <SelectTrigger id="qh-class">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ANY}>All kinds</SelectItem>
                                    {CONTENT_CLASSES.map((option) => (
                                        <SelectItem key={option.value} value={option.value}>
                                            {option.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                            <div className="space-y-0.5">
                                <Label htmlFor="qh-blocked" className="text-sm font-normal">
                                    Prevent contact entirely
                                </Label>
                                <p className="text-xs text-muted-foreground">
                                    Turn off to set a window instead.
                                </p>
                            </div>
                            <Switch
                                id="qh-blocked"
                                checked={form.is_blocked}
                                onCheckedChange={(checked) =>
                                    setForm({ ...form, is_blocked: checked })
                                }
                            />
                        </div>

                        {!form.is_blocked && (
                            <div className="grid grid-cols-2 gap-3">
                                <div className="space-y-1.5">
                                    <Label htmlFor="qh-open">Contact from</Label>
                                    <Input
                                        id="qh-open"
                                        type="time"
                                        value={form.open_time}
                                        onChange={(event) =>
                                            setForm({ ...form, open_time: event.target.value })
                                        }
                                    />
                                </div>
                                <div className="space-y-1.5">
                                    <Label htmlFor="qh-close">Contact until</Label>
                                    <Input
                                        id="qh-close"
                                        type="time"
                                        value={form.close_time}
                                        onChange={(event) =>
                                            setForm({ ...form, close_time: event.target.value })
                                        }
                                    />
                                </div>
                            </div>
                        )}

                        <div className="space-y-1.5">
                            <Label htmlFor="qh-reason">Reason</Label>
                            <Textarea
                                id="qh-reason"
                                rows={2}
                                placeholder="Christmas Day — practice closed"
                                value={form.reason}
                                onChange={(event) =>
                                    setForm({ ...form, reason: event.target.value })
                                }
                            />
                            <p className="text-xs text-muted-foreground">
                                Worth filling in. A rule nobody can explain later is a rule
                                nobody dares delete.
                            </p>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setFormOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? "Saving…" : "Save exception"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
