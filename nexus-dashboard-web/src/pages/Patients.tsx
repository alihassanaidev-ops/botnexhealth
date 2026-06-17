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
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
import {
    listContacts,
    getContact,
    revealContactPhone,
    mergeContact,
    unmergeContact,
    type ContactListItem,
    type ContactsListResponse,
    type ContactDetail,
} from "@/lib/contacts-api"

const PAGE_SIZE = 25

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

// ── Merge picker ───────────────────────────────────────────────────────────────

interface MergePickerProps {
    /** The primary patient that will absorb the chosen record. */
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
                        <Link2 className="h-5 w-5" /> Merge a duplicate into {primary.full_name ?? "this patient"}
                    </DialogTitle>
                    <DialogDescription>
                        The selected record becomes part of this patient. Its calls are kept, and you can unmerge later.
                    </DialogDescription>
                </DialogHeader>
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                    <Input
                        autoFocus
                        placeholder="Search patients by name…"
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
                        <p className="p-4 text-sm text-muted-foreground text-center">No other patients found.</p>
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

// ── Patient detail drawer ───────────────────────────────────────────────────────

interface PatientDetailProps {
    contactId: string | null
    onClose: () => void
    onChanged: () => void
}

function PatientDetail({ contactId, onClose, onChanged }: PatientDetailProps) {
    const { user } = useAuth()
    const isAdmin = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"
    const [detail, setDetail] = useState<ContactDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [showMerge, setShowMerge] = useState(false)
    const [unmerging, setUnmerging] = useState<string | null>(null)

    const load = useCallback(async () => {
        if (!contactId) return
        setLoading(true)
        try {
            setDetail(await getContact(contactId))
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't load patient")
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
                                    {detail.full_name ?? "Unknown patient"}
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
                </TableRow>
            ))}
        </>
    )
}

// ── Main page ───────────────────────────────────────────────────────────────────

export default function Patients() {
    const [data, setData] = useState<ContactsListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [search, setSearch] = useState("")
    const [page, setPage] = useState(0)
    const [selected, setSelected] = useState<string | null>(null)

    const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
    const [debouncedSearch, setDebouncedSearch] = useState("")
    useEffect(() => {
        if (searchTimer.current) clearTimeout(searchTimer.current)
        searchTimer.current = setTimeout(() => setDebouncedSearch(search), 400)
        return () => { if (searchTimer.current) clearTimeout(searchTimer.current) }
    }, [search])

    useEffect(() => { setPage(0) }, [debouncedSearch])

    const fetchContacts = useCallback(async () => {
        setLoading(true)
        try {
            setData(await listContacts({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                search: debouncedSearch || undefined,
            }))
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to load patients")
        } finally {
            setLoading(false)
        }
    }, [page, debouncedSearch])

    useEffect(() => { fetchContacts() }, [fetchContacts])

    const total = data?.total ?? 0
    const pageCount = Math.ceil(total / PAGE_SIZE)
    const from = total === 0 ? 0 : page * PAGE_SIZE + 1
    const to = Math.min((page + 1) * PAGE_SIZE, total)

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Users className="h-7 w-7" />
                        Patients
                    </h2>
                    <p className="text-muted-foreground mt-1">
                        Everyone who has called, with their call history and callback number.
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    {!loading && data && (
                        <div className="text-right">
                            <p className="text-2xl font-bold tabular-nums">{total.toLocaleString()}</p>
                            <p className="text-xs text-muted-foreground">patients</p>
                        </div>
                    )}
                    <Button variant="outline" size="sm" onClick={fetchContacts} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            <div className="flex items-center gap-2">
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                    <Input
                        placeholder="Search patient..."
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
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Patient</TableHead>
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
                                        <TableCell colSpan={4} className="px-4 py-16 text-center">
                                            <div className="flex flex-col items-center gap-3 text-muted-foreground">
                                                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center">
                                                    <Users className="h-6 w-6 opacity-40" />
                                                </div>
                                                <div>
                                                    <p className="font-medium text-sm text-foreground/70">No patients yet</p>
                                                    <p className="text-xs mt-0.5">
                                                        {search ? "Try a different search." : "Patients appear here as calls come in."}
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
                                <span className="font-medium text-foreground">{total.toLocaleString()}</span> patients
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

            <PatientDetail
                contactId={selected}
                onClose={() => setSelected(null)}
                onChanged={fetchContacts}
            />
        </div>
    )
}
