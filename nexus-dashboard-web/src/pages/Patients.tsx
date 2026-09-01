import { useCallback, useEffect, useRef, useState } from "react"
import {
    Users,
    Search,
    ChevronLeft,
    ChevronRight,
    RefreshCcw,
    X,
    Phone,
    Link2,
    Link2Off,
    Sparkles,
    UserPlus,
} from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
} from "@/components/ui/dialog"
import { RevealablePhone } from "@/components/RevealablePhone"
import { toast } from "sonner"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"
import { useSelectedLocationId } from "@/context/LocationContext"
import {
    createContact,
    listContacts,
    listLivePatients,
    getContact,
    revealContactPhone,
    mergeContact,
    unmergeContact,
    updateContact,
    type ContactListItem,
    type ContactsListResponse,
    type ContactDetail,
    type LivePatientPage,
} from "@/lib/contacts-api"

const PAGE_SIZE = 25
type DirectoryMode = "contacts" | "patients"

function lifecycleLabel(value: ContactListItem["lifecycle"]): string {
    if (value === "patient") return "Patient"
    if (value === "lead") return "Lead"
    return "Contact"
}

function formatDate(value: string | null): string {
    if (!value) return "—"
    return new Date(value).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
}

function formatDateTime(value: string | null): string {
    if (!value) return "—"
    const d = new Date(value)
    return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })
}

function initials(name: string | null): string {
    if (!name) return "?"
    const parts = name.trim().split(/\s+/)
    return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?"
}

interface ContactCreateDialogProps {
    open: boolean
    onClose: () => void
    onCreated: (contactId: string, matchedPatient: boolean) => void
}

function ContactCreateDialog({ open, onClose, onCreated }: ContactCreateDialogProps) {
    const locationId = useSelectedLocationId()
    const { hasPms } = useInstitution()
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState("")
    const [form, setForm] = useState({
        firstName: "",
        lastName: "",
        phone: "",
        email: "",
        notes: "",
        consentSms: false,
        consentEmail: false,
    })

    function reset() {
        setForm({
            firstName: "",
            lastName: "",
            phone: "",
            email: "",
            notes: "",
            consentSms: false,
            consentEmail: false,
        })
        setError("")
    }

    async function save() {
        if (!form.phone.trim() && !form.email.trim()) {
            setError("Add a phone number or email address so the clinic can reach them.")
            return
        }
        setSaving(true)
        setError("")
        try {
            const consented = [
                form.consentSms ? "SMS" : null,
                form.consentEmail ? "email" : null,
            ].filter(Boolean)
            const result = await createContact({
                first_name: form.firstName.trim() || undefined,
                last_name: form.lastName.trim() || undefined,
                phone: form.phone.trim() || undefined,
                email: form.email.trim() || undefined,
                notes: form.notes.trim() || undefined,
                location_id: locationId ?? null,
                consent_sms: form.consentSms,
                consent_email: form.consentEmail,
                consent_wording: consented.length
                    ? `Agreed to ${consented.join(" and ")} contact when speaking to staff`
                    : undefined,
            })
            toast.success(
                result.created
                    ? "Contact added"
                    : result.matched_existing_patient
                        ? "This person already exists as a patient"
                        : "Existing contact opened",
            )
            reset()
            onClose()
            onCreated(result.contact.id, result.matched_existing_patient)
        } catch (e) {
            setError(e instanceof Error ? e.message : "Couldn't add that contact.")
        } finally {
            setSaving(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={(next) => {
            if (!next) {
                reset()
                onClose()
            }
        }}>
            <DialogContent className="max-w-xl">
                <DialogHeader>
                    <DialogTitle>Add contact</DialogTitle>
                    <DialogDescription>
                        {hasPms
                            ? "Add someone who has contacted the practice but is not yet linked to a patient record."
                            : "Add someone the practice may need to call, message, or follow up with."}
                    </DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                        <Label htmlFor="contact-first-name">First name</Label>
                        <Input id="contact-first-name" value={form.firstName} onChange={(e) => setForm({ ...form, firstName: e.target.value })} />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="contact-last-name">Last name</Label>
                        <Input id="contact-last-name" value={form.lastName} onChange={(e) => setForm({ ...form, lastName: e.target.value })} />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="contact-phone">Phone</Label>
                        <Input id="contact-phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="contact-email">Email</Label>
                        <Input id="contact-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
                    </div>
                </div>
                <div className="space-y-1.5">
                    <Label htmlFor="contact-notes">Notes</Label>
                    <Textarea id="contact-notes" value={form.notes} placeholder="What did they ask about?" onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </div>
                <div className="space-y-2 rounded-md border p-3">
                    <p className="text-sm font-medium">Permission to contact</p>
                    <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={form.consentSms} onChange={(e) => setForm({ ...form, consentSms: e.target.checked })} />
                        They agreed to receive text messages
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={form.consentEmail} onChange={(e) => setForm({ ...form, consentEmail: e.target.checked })} />
                        They agreed to receive email
                    </label>
                    <p className="text-xs text-muted-foreground">Leave both clear unless the person explicitly agreed.</p>
                </div>
                {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
                <div className="flex justify-end gap-2">
                    <Button
                        variant="outline"
                        onClick={() => {
                            reset()
                            onClose()
                        }}
                        disabled={saving}
                    >
                        Cancel
                    </Button>
                    <Button onClick={() => void save()} disabled={saving}>{saving ? "Adding…" : "Add contact"}</Button>
                </div>
            </DialogContent>
        </Dialog>
    )
}

// ── Merge picker ───────────────────────────────────────────────────────────────

interface MergePickerProps {
    /** The primary person record that will absorb the chosen duplicate. */
    primary: ContactDetail
    onClose: () => void
    onMerged: () => void
}

function MergePicker({ primary, onClose, onMerged }: MergePickerProps) {
    const [search, setSearch] = useState("")
    const [results, setResults] = useState<ContactListItem[]>([])
    const [loading, setLoading] = useState(false)
    const [merging, setMerging] = useState<string | null>(null)

    useEffect(() => {
        let cancelled = false
        const t = setTimeout(async () => {
            setLoading(true)
            try {
                const res = await listContacts({ limit: 10, search: search || undefined })
                if (!cancelled) setResults(res.items.filter((c) => c.id !== primary.id))
            } catch {
                if (!cancelled) setResults([])
            } finally {
                if (!cancelled) setLoading(false)
            }
        }, 300)
        return () => { cancelled = true; clearTimeout(t) }
    }, [search, primary.id])

    async function handleMerge(aliasId: string) {
        setMerging(aliasId)
        try {
            await mergeContact(primary.id, aliasId)
            toast.success("Records merged")
            onMerged()
            onClose()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't merge records")
        } finally {
            setMerging(null)
        }
    }

    return (
        <Dialog open onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Link2 className="h-5 w-5" /> Merge a duplicate into {primary.full_name ?? "this contact"}
                    </DialogTitle>
                    <DialogDescription>
                        The selected record becomes an alias of this person. Its calls are kept, and you can unmerge later.
                    </DialogDescription>
                </DialogHeader>
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                    <Input
                        autoFocus
                        placeholder="Search contacts by name…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="h-9 pl-8"
                    />
                </div>
                <div className="max-h-72 overflow-y-auto rounded-md border divide-y">
                    {loading ? (
                        <div className="p-4 space-y-2">
                            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
                        </div>
                    ) : results.length === 0 ? (
                        <p className="p-4 text-sm text-muted-foreground text-center">No other contacts found.</p>
                    ) : (
                        results.map((c) => (
                            <div key={c.id} className="flex items-center justify-between gap-3 px-3 py-2">
                                <div className="min-w-0">
                                    <p className="truncate text-sm font-medium">{c.full_name ?? "Unknown"}</p>
                                    <p className="text-xs text-muted-foreground">
                                        {c.phone_masked ?? "no phone"} · {c.call_count} call{c.call_count === 1 ? "" : "s"}
                                    </p>
                                </div>
                                <Button
                                    size="sm"
                                    variant="outline"
                                    className="h-7 text-xs gap-1 shrink-0"
                                    disabled={merging !== null}
                                    onClick={() => handleMerge(c.id)}
                                >
                                    <Link2 className="h-3 w-3" />
                                    {merging === c.id ? "Merging…" : "Merge"}
                                </Button>
                            </div>
                        ))
                    )}
                </div>
            </DialogContent>
        </Dialog>
    )
}

// ── Shared person detail drawer ───────────────────────────────────────────────

interface PersonDetailProps {
    contactId: string | null
    mode: DirectoryMode
    onClose: () => void
    onChanged: () => void
}

function PersonDetail({ contactId, mode, onClose, onChanged }: PersonDetailProps) {
    const { user } = useAuth()
    const isAdmin = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [detail, setDetail] = useState<ContactDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [showMerge, setShowMerge] = useState(false)
    const [unmerging, setUnmerging] = useState<string | null>(null)
    const [notes, setNotes] = useState("")
    const [savingNotes, setSavingNotes] = useState(false)

    const load = useCallback(async () => {
        if (!contactId) return
        setLoading(true)
        try {
            const next = await getContact(contactId)
            setDetail(next)
            setNotes(next.notes ?? "")
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't load contact")
        } finally {
            setLoading(false)
        }
    }, [contactId])

    useEffect(() => { if (contactId) load() }, [contactId, load])

    async function handleUnmerge(aliasId: string) {
        if (!detail) return
        setUnmerging(aliasId)
        try {
            await unmergeContact(detail.id, aliasId)
            toast.success("Record unmerged")
            await load()
            onChanged()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't unmerge")
        } finally {
            setUnmerging(null)
        }
    }

    async function saveNotes() {
        if (!detail) return
        setSavingNotes(true)
        try {
            const updated = await updateContact(detail.id, { notes })
            setDetail(updated)
            setNotes(updated.notes ?? "")
            toast.success("Notes saved")
            onChanged()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't save notes")
        } finally {
            setSavingNotes(false)
        }
    }

    return (
        <Dialog open={!!contactId} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                {loading || !detail ? (
                    <div className="space-y-4 py-4">
                        <Skeleton className="h-10 w-48" />
                        <Skeleton className="h-24 w-full" />
                        <Skeleton className="h-40 w-full" />
                    </div>
                ) : (
                    <>
                        <DialogHeader>
                            <DialogTitle className="flex items-center gap-3">
                                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-foreground text-background text-sm font-semibold">
                                    {initials(detail.full_name)}
                                </span>
                                <span>
                                    {detail.full_name ?? (mode === "patients" ? "Unknown patient" : "Unknown contact")}
                                    {detail.is_new_patient && (
                                        <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-600">
                                            <Sparkles className="h-3 w-3" /> New
                                        </span>
                                    )}
                                </span>
                            </DialogTitle>
                            <DialogDescription className="flex items-center gap-2 pt-1">
                                <Phone className="h-3.5 w-3.5" />
                                <RevealablePhone
                                    callId={detail.id}
                                    masked={detail.phone_masked}
                                    available={detail.phone_reveal_available}
                                    revealFn={revealContactPhone}
                                />
                                <span className="text-muted-foreground">·</span>
                                <span className="text-muted-foreground">
                                    {detail.call_count} call{detail.call_count === 1 ? "" : "s"} · since {formatDate(detail.created_at)}
                                </span>
                            </DialogDescription>
                        </DialogHeader>

                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={detail.lifecycle === "patient" ? "default" : "secondary"}>
                                {lifecycleLabel(detail.lifecycle)}
                            </Badge>
                            {detail.source && <span className="text-xs text-muted-foreground">Added via {detail.source.replace(/_/g, " ")}</span>}
                            {detail.email_masked && <span className="text-xs text-muted-foreground">· {detail.email_masked}</span>}
                            {detail.lifecycle === "patient" && detail.pms_last_synced_at && (
                                <span className="text-xs text-muted-foreground">· PMS synced {formatDateTime(detail.pms_last_synced_at)}</span>
                            )}
                        </div>

                        {(mode === "contacts" || detail.notes) && (
                            <div className="space-y-2 rounded-lg border p-3">
                                <Label htmlFor="relationship-notes">Relationship notes</Label>
                                <Textarea
                                    id="relationship-notes"
                                    value={notes}
                                    readOnly={!isAdmin}
                                    placeholder="Record follow-up context without duplicating clinical chart notes."
                                    onChange={(e) => setNotes(e.target.value)}
                                />
                                {isAdmin && (
                                    <div className="flex justify-end">
                                        <Button size="sm" onClick={() => void saveNotes()} disabled={savingNotes}>
                                            {savingNotes ? "Saving…" : "Save notes"}
                                        </Button>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Linked records */}
                        <div className="rounded-lg border p-3">
                            <div className="mb-2 flex items-center justify-between">
                                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                    Linked records {detail.aliases.length > 0 && `(${detail.aliases.length})`}
                                </p>
                                {isAdmin && (
                                    <Button variant="outline" size="sm" className="h-7 gap-1 text-xs" onClick={() => setShowMerge(true)}>
                                        <Link2 className="h-3 w-3" /> Merge duplicate
                                    </Button>
                                )}
                            </div>
                            {detail.aliases.length === 0 ? (
                                <p className="text-xs text-muted-foreground">
                                    No linked records. Merge a duplicate if the same person appears under another entry
                                    (e.g. a different phone or a name typo).
                                </p>
                            ) : (
                                <div className="divide-y">
                                    {detail.aliases.map((a) => (
                                        <div key={a.id} className="flex items-center justify-between gap-2 py-2">
                                            <div className="min-w-0">
                                                <p className="truncate text-sm">{a.full_name ?? "Unknown"}</p>
                                                <p className="text-xs text-muted-foreground">{a.phone_masked ?? "no phone"}</p>
                                            </div>
                                            {isAdmin && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="h-7 gap-1 text-xs text-muted-foreground"
                                                    disabled={unmerging !== null}
                                                    onClick={() => handleUnmerge(a.id)}
                                                >
                                                    <Link2Off className="h-3 w-3" />
                                                    {unmerging === a.id ? "…" : "Unmerge"}
                                                </Button>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Call history */}
                        <div>
                            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                Call history
                            </p>
                            <div className="space-y-2">
                                {detail.calls.length === 0 ? (
                                    <p className="text-xs text-muted-foreground">No calls recorded.</p>
                                ) : (
                                    detail.calls.map((c) => (
                                        <div key={c.id} className="rounded-md border p-3">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-xs font-medium text-muted-foreground">
                                                    {formatDateTime(c.created_at)}
                                                </span>
                                                <div className="flex flex-wrap gap-1">
                                                    {c.call_tags.map((t) => (
                                                        <span key={t} className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                                                            {t.replace(/_/g, " ")}
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                            {c.summary && (
                                                <p className="mt-1.5 text-sm text-muted-foreground line-clamp-3">{c.summary}</p>
                                            )}
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </>
                )}
            </DialogContent>
            {showMerge && detail && (
                <MergePicker
                    primary={detail}
                    onClose={() => setShowMerge(false)}
                    onMerged={async () => { await load(); onChanged() }}
                />
            )}
        </Dialog>
    )
}

// ── Live PMS patient directory ────────────────────────────────────────────────

function LivePatientsDirectory() {
    const locationId = useSelectedLocationId()
    const { pmsType } = useInstitution()
    const [data, setData] = useState<LivePatientPage | null>(null)
    const [loading, setLoading] = useState(false)
    const [search, setSearch] = useState("")
    const [debouncedSearch, setDebouncedSearch] = useState("")
    const [patientStatus, setPatientStatus] = useState<"active" | "inactive" | "all">("active")
    const [cursor, setCursor] = useState<string | null>(null)
    const [pageNumber, setPageNumber] = useState(1)
    const [selected, setSelected] = useState<string | null>(null)
    const requestVersion = useRef(0)

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search.trim()), 400)
        return () => clearTimeout(timer)
    }, [search])

    useEffect(() => {
        setCursor(null)
        setPageNumber(1)
    }, [debouncedSearch, patientStatus, locationId])

    const fetchPatients = useCallback(async () => {
        const version = ++requestVersion.current
        if (!locationId) {
            setData(null)
            return
        }
        setLoading(true)
        try {
            const next = await listLivePatients({
                locationId,
                cursor,
                pageSize: PAGE_SIZE,
                search: debouncedSearch.length >= 2 ? debouncedSearch : undefined,
                patientStatus,
            })
            if (requestVersion.current === version) setData(next)
        } catch (error) {
            if (requestVersion.current === version) {
                setData(null)
                toast.error(error instanceof Error ? error.message : "Failed to load patients")
            }
        } finally {
            if (requestVersion.current === version) setLoading(false)
        }
    }, [locationId, cursor, debouncedSearch, patientStatus])

    useEffect(() => { void fetchPatients() }, [fetchPatients])

    const providerName = pmsType === "gotracker" ? "GoTracker" : "NexHealth"
    const totalLabel = data?.total === null || data?.total === undefined
        ? `${data?.returned ?? 0} on this page`
        : data.total.toLocaleString()

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <PageHeader
                icon={Users}
                title="Patients"
                description={`Current patient records read securely from ${providerName}. Active patients are shown by default.`}
                actions={
                    <>
                        {!loading && data && (
                            <div className="text-right">
                                <p className="text-2xl font-bold tabular-nums">{totalLabel}</p>
                                <p className="text-xs text-muted-foreground">
                                    Live · {formatDateTime(data.fetched_at)}
                                </p>
                            </div>
                        )}
                        <Button variant="outline" size="sm" onClick={() => void fetchPatients()} disabled={loading || !locationId} className="gap-1.5">
                            <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </>
                }
            />

            <div className="flex flex-wrap items-center gap-2">
                {([
                    ["active", "Active"],
                    ["inactive", "Inactive"],
                    ["all", "All patients"],
                ] as const).map(([value, label]) => (
                    <Button
                        key={value}
                        size="sm"
                        variant={patientStatus === value ? "default" : "outline"}
                        onClick={() => setPatientStatus(value)}
                    >
                        {label}
                    </Button>
                ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        placeholder="Search patients by name…"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        className="h-8 w-[220px] pl-8 lg:w-[320px]"
                    />
                </div>
                {search && (
                    <Button variant="ghost" onClick={() => setSearch("")} className="h-8 px-2 text-muted-foreground">
                        Reset <X className="ml-2 h-4 w-4" />
                    </Button>
                )}
                {search.trim().length === 1 && (
                    <span className="text-xs text-muted-foreground">Enter at least 2 characters.</span>
                )}
            </div>

            {!locationId ? (
                <Card>
                    <CardContent className="p-10 text-center text-sm text-muted-foreground">
                        Select a location to read its patient directory.
                    </CardContent>
                </Card>
            ) : (
                <Card>
                    <CardContent className="p-0">
                        <div className="overflow-x-auto">
                            <Table className="w-full text-sm">
                                <TableHeader className="border-b border-border bg-muted">
                                    <TableRow>
                                        <TableHead className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Patient</TableHead>
                                        <TableHead className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Status</TableHead>
                                        <TableHead className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Phone</TableHead>
                                        <TableHead className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Email</TableHead>
                                        <TableHead className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">PMS updated</TableHead>
                                        <TableHead className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">History</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {loading ? (
                                        Array.from({ length: 8 }).map((_, index) => (
                                            <TableRow key={index}>
                                                {Array.from({ length: 6 }).map((__, cell) => (
                                                    <TableCell key={cell} className="px-4 py-3"><Skeleton className="h-5 w-24" /></TableCell>
                                                ))}
                                            </TableRow>
                                        ))
                                    ) : !data || data.items.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={6} className="px-4 py-16 text-center text-sm text-muted-foreground">
                                                No {patientStatus === "all" ? "" : `${patientStatus} `}patients found in {providerName}.
                                            </TableCell>
                                        </TableRow>
                                    ) : data.items.map((patient) => (
                                        <TableRow
                                            key={patient.pms_patient_id}
                                            className={patient.contact_id ? "cursor-pointer hover:bg-muted" : undefined}
                                            onClick={() => patient.contact_id && setSelected(patient.contact_id)}
                                        >
                                            <TableCell className="px-4">
                                                <div className="flex items-center gap-3">
                                                    <span className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-xs font-semibold text-background">
                                                        {initials(patient.full_name)}
                                                    </span>
                                                    <div>
                                                        <p className="font-medium">{patient.full_name}</p>
                                                        <p className="text-xs text-muted-foreground">{providerName} ID {patient.pms_patient_id.replace(/^\w+-/, "")}</p>
                                                    </div>
                                                </div>
                                            </TableCell>
                                            <TableCell className="px-4">
                                                <Badge variant={patient.inactive ? "secondary" : "default"}>
                                                    {patient.inactive ? "Inactive" : "Active"}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="whitespace-nowrap px-4">{patient.phone_masked ?? "—"}</TableCell>
                                            <TableCell className="whitespace-nowrap px-4">{patient.email_masked ?? "—"}</TableCell>
                                            <TableCell className="whitespace-nowrap px-4 text-muted-foreground">
                                                {formatDateTime(patient.pms_last_sync_time ?? patient.pms_updated_at)}
                                            </TableCell>
                                            <TableCell className="px-4 text-muted-foreground">
                                                {patient.contact_id ? "Available" : "Not yet linked locally"}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>

                        {!loading && data && data.items.length > 0 && (
                            <div className="flex flex-col gap-3 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                                <p className="text-sm text-muted-foreground">
                                    Page {pageNumber} · {data.returned} record{data.returned === 1 ? "" : "s"} received from {providerName}
                                </p>
                                <div className="flex items-center gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        disabled={!data.has_previous_page || !data.previous_cursor}
                                        onClick={() => {
                                            setCursor(data.previous_cursor)
                                            setPageNumber((value) => Math.max(1, value - 1))
                                        }}
                                        className="gap-1"
                                    >
                                        <ChevronLeft className="h-4 w-4" /> Previous
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        disabled={!data.has_next_page || !data.next_cursor}
                                        onClick={() => {
                                            setCursor(data.next_cursor)
                                            setPageNumber((value) => value + 1)
                                        }}
                                        className="gap-1"
                                    >
                                        Next <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            <PersonDetail
                contactId={selected}
                mode="patients"
                onClose={() => setSelected(null)}
                onChanged={() => void fetchPatients()}
            />
        </div>
    )
}

// ── Skeleton rows ───────────────────────────────────────────────────────────────

function SkeletonRows() {
    return (
        <>
            {Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                    <TableCell className="px-4 py-3"><Skeleton className="h-8 w-40" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-28" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-12" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-24" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-24" /></TableCell>
                </TableRow>
            ))}
        </>
    )
}

// ── Main page ───────────────────────────────────────────────────────────────────

function LocalPeopleDirectory({ mode }: { mode: DirectoryMode }) {
    const { user } = useAuth()
    const { hasPms, pmsType } = useInstitution()
    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [data, setData] = useState<ContactsListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [search, setSearch] = useState("")
    const [page, setPage] = useState(0)
    const [selected, setSelected] = useState<string | null>(null)
    const [showCreate, setShowCreate] = useState(false)
    const [contactFilter, setContactFilter] = useState<"all" | "lead" | "contact">("all")

    const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
    const [debouncedSearch, setDebouncedSearch] = useState("")
    useEffect(() => {
        if (searchTimer.current) clearTimeout(searchTimer.current)
        searchTimer.current = setTimeout(() => setDebouncedSearch(search), 400)
        return () => { if (searchTimer.current) clearTimeout(searchTimer.current) }
    }, [search])

    useEffect(() => { setPage(0) }, [debouncedSearch, contactFilter, mode])

    const fetchContacts = useCallback(async () => {
        setLoading(true)
        try {
            setData(await listContacts({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                search: debouncedSearch || undefined,
                directory: mode,
                lifecycle: mode === "contacts" && contactFilter !== "all" ? contactFilter : undefined,
            }))
        } catch (e) {
            toast.error(e instanceof Error ? e.message : `Failed to load ${mode}`)
        } finally {
            setLoading(false)
        }
    }, [page, debouncedSearch, mode, contactFilter])

    useEffect(() => { fetchContacts() }, [fetchContacts])

    const total = data?.total ?? 0
    const pageCount = Math.ceil(total / PAGE_SIZE)
    const from = total === 0 ? 0 : page * PAGE_SIZE + 1
    const to = Math.min((page + 1) * PAGE_SIZE, total)

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <PageHeader
                icon={Users}
                title={mode === "patients" ? "Patients" : "Contacts"}
                description={mode === "patients"
                    ? `People linked to ${pmsType === "gotracker" ? "GoTracker" : pmsType === "nexhealth" ? "NexHealth" : "the practice system"} patient records. This view uses the latest synchronized data.`
                    : hasPms
                        ? "People the practice knows who are not yet linked to a patient record, including leads and callers."
                        : "People the practice knows through forms, calls, and conversations, with their follow-up history."}
                actions={
                    <>
                        {!loading && data && (
                            <div className="text-right">
                                <p className="text-2xl font-bold tabular-nums">{total.toLocaleString()}</p>
                                <p className="text-xs text-muted-foreground">{mode}</p>
                            </div>
                        )}
                        {mode === "contacts" && canManage && (
                            <Button size="sm" onClick={() => setShowCreate(true)} className="gap-1.5">
                                <UserPlus className="h-4 w-4" /> Add contact
                            </Button>
                        )}
                        <Button variant="outline" size="sm" onClick={fetchContacts} disabled={loading} className="gap-1.5">
                            <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </>
                }
            />

            {mode === "contacts" && (
                <div className="flex flex-wrap items-center gap-2">
                    {([
                        ["all", "All contacts"],
                        ["lead", "Leads"],
                        ["contact", "Callers & other contacts"],
                    ] as const).map(([value, label]) => (
                        <Button
                            key={value}
                            size="sm"
                            variant={contactFilter === value ? "default" : "outline"}
                            onClick={() => setContactFilter(value)}
                        >
                            {label}
                        </Button>
                    ))}
                </div>
            )}

            <div className="flex items-center gap-2">
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                    <Input
                        placeholder={mode === "patients" ? "Search patients…" : "Search contacts…"}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="h-8 pl-8 w-[200px] lg:w-[300px]"
                    />
                </div>
                {search && (
                    <Button variant="ghost" onClick={() => setSearch("")} className="h-8 px-2 text-muted-foreground">
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
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Person</TableHead>
                                    {mode === "contacts" && <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Relationship</TableHead>}
                                    {mode === "patients" && <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">PMS sync</TableHead>}
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Phone</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Calls</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Last call</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading ? (
                                    <SkeletonRows />
                                ) : !data || data.items.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={5} className="px-4 py-16 text-center">
                                            <div className="flex flex-col items-center gap-3 text-muted-foreground">
                                                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center">
                                                    <Users className="h-6 w-6 opacity-40" />
                                                </div>
                                                <div>
                                                    <p className="font-medium text-sm text-foreground/70">No {mode} yet</p>
                                                    <p className="text-xs mt-0.5">
                                                        {search
                                                            ? "Try a different search."
                                                            : mode === "patients"
                                                                ? "Patients appear after NexHealth or GoTracker synchronizes them."
                                                                : "Contacts appear after a form submission, manual entry, or call."}
                                                    </p>
                                                </div>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    data.items.map((c) => (
                                        <TableRow
                                            key={c.id}
                                            className="cursor-pointer hover:bg-muted transition-colors"
                                            onClick={() => setSelected(c.id)}
                                        >
                                            <TableCell className="px-4">
                                                <div className="flex items-center gap-3">
                                                    <span className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-background text-xs font-semibold">
                                                        {initials(c.full_name)}
                                                    </span>
                                                    <div className="min-w-0">
                                                        <span className={c.full_name ? "font-medium" : "text-muted-foreground"}>
                                                            {c.full_name ?? "Unknown"}
                                                        </span>
                                                        {c.alias_count > 0 && (
                                                            <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                                                <Link2 className="h-2.5 w-2.5" /> {c.alias_count} linked
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>
                                            </TableCell>
                                            {mode === "contacts" && (
                                                <TableCell className="px-4">
                                                    <div className="flex flex-col items-start gap-1">
                                                        <Badge variant="secondary">{lifecycleLabel(c.lifecycle)}</Badge>
                                                        <span className="text-xs text-muted-foreground">
                                                            {c.source ? `via ${c.source.replace(/_/g, " ")}` : "from a conversation"}
                                                            {c.has_notes && " · has notes"}
                                                        </span>
                                                    </div>
                                                </TableCell>
                                            )}
                                            {mode === "patients" && (
                                                <TableCell className="whitespace-nowrap px-4 text-muted-foreground">
                                                    {formatDateTime(c.pms_last_synced_at)}
                                                </TableCell>
                                            )}
                                            <TableCell className="whitespace-nowrap px-4 text-sm" onClick={(e) => e.stopPropagation()}>
                                                <RevealablePhone
                                                    callId={c.id}
                                                    masked={c.phone_masked}
                                                    available={c.phone_reveal_available}
                                                    revealFn={revealContactPhone}
                                                />
                                            </TableCell>
                                            <TableCell className="px-4 tabular-nums text-muted-foreground">{c.call_count}</TableCell>
                                            <TableCell className="whitespace-nowrap px-4 text-muted-foreground">{formatDate(c.last_call_at)}</TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>

                    {!loading && total > 0 && (
                        <div className="flex flex-col gap-3 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                            <p className="text-sm text-muted-foreground">
                                Showing <span className="font-medium text-foreground">{from}–{to}</span> of{" "}
                                <span className="font-medium text-foreground">{total.toLocaleString()}</span> {mode}
                            </p>
                            {pageCount > 1 && (
                                <div className="flex items-center gap-2">
                                    <span className="mr-1 text-sm tabular-nums text-muted-foreground">
                                        Page {page + 1} of {pageCount}
                                    </span>
                                    <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="gap-1">
                                        <ChevronLeft className="h-4 w-4" /> Previous
                                    </Button>
                                    <Button variant="outline" size="sm" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)} className="gap-1">
                                        Next <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>

            <PersonDetail
                contactId={selected}
                mode={mode}
                onClose={() => setSelected(null)}
                onChanged={fetchContacts}
            />
            {mode === "contacts" && (
                <ContactCreateDialog
                    open={showCreate}
                    onClose={() => setShowCreate(false)}
                    onCreated={(contactId, matchedPatient) => {
                        void fetchContacts()
                        if (matchedPatient) {
                            toast.info("Open Patients to view the synchronized patient record.")
                        } else {
                            setSelected(contactId)
                        }
                    }}
                />
            )}
        </div>
    )
}

export function PeopleDirectory({ mode }: { mode: DirectoryMode }) {
    return mode === "patients"
        ? <LivePatientsDirectory />
        : <LocalPeopleDirectory mode={mode} />
}

export default function Patients() {
    return <PeopleDirectory mode="patients" />
}
