import { useEffect, useState } from "react"

import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    createEnquiry,
    getEnquiry,
    listEnquiries,
    updateEnquiry,
    type Enquiry,
    type EnquiryDetail,
    type EnquiryStage,
} from "@/lib/enquiries-api"

/**
 * People who have enquired but are not patients yet.
 *
 * Kept as its own screen rather than folded into Patients, because the two are
 * genuinely different things to a clinic: a patient exists in the practice
 * software and a lead does not, and that is the whole distinction the stage
 * column exists to show. Once a lead registers, the row links across to their
 * patient record and this list stops being where you look for them.
 *
 * Contact details are masked here for the same reason they are on the patients
 * list — these belong to someone who is not a patient and has consented to very
 * little.
 */
const STAGES: { value: EnquiryStage | "all"; label: string }[] = [
    { value: "all", label: "All" },
    { value: "lead", label: "New" },
    { value: "contacted", label: "In progress" },
    { value: "registered", label: "Registered" },
    { value: "booked", label: "Booked" },
]

function stageLabel(stage: EnquiryStage): string {
    return STAGES.find((s) => s.value === stage)?.label ?? stage
}

function displayName(row: Enquiry): string {
    const name = [row.first_name, row.last_name].filter(Boolean).join(" ").trim()
    // A lead can legitimately arrive with only a phone number.
    return name || row.phone_masked || row.email_masked || "Unknown"
}

export default function Leads() {
    const [rows, setRows] = useState<Enquiry[]>([])
    const [total, setTotal] = useState(0)
    const [stage, setStage] = useState<EnquiryStage | "all">("all")
    const [search, setSearch] = useState("")
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")

    const [adding, setAdding] = useState(false)
    const [form, setForm] = useState({
        first_name: "", last_name: "", phone: "", email: "", notes: "",
        consent_sms: false,
    })
    const [addError, setAddError] = useState("")
    const [addNote, setAddNote] = useState("")

    const [open, setOpen] = useState<EnquiryDetail | null>(null)
    const [notes, setNotes] = useState("")
    const [saving, setSaving] = useState(false)

    async function refresh() {
        setLoading(true)
        try {
            const data = await listEnquiries({
                stage: stage === "all" ? undefined : stage,
                search: search.trim() || undefined,
            })
            setRows(data.items)
            setTotal(data.total)
            setError("")
        } catch {
            setError("Couldn't load enquiries.")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        void refresh()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [stage])

    async function submitNew() {
        if (!form.phone.trim() && !form.email.trim()) {
            setAddError("Add a phone number or an email address so they can be reached.")
            return
        }
        setAddError("")
        setAddNote("")
        try {
            const result = await createEnquiry({
                first_name: form.first_name.trim() || undefined,
                last_name: form.last_name.trim() || undefined,
                phone: form.phone.trim() || undefined,
                email: form.email.trim() || undefined,
                notes: form.notes.trim() || undefined,
                consent_sms: form.consent_sms,
                consent_wording: form.consent_sms ? "Agreed when speaking to staff" : undefined,
            })
            // Told rather than silently deduplicated: otherwise whoever typed
            // it sees nothing happen and types it again.
            setAddNote(
                result.created
                    ? "Added."
                    : "That person is already on your list — opened instead of added again.",
            )
            setForm({ first_name: "", last_name: "", phone: "", email: "", notes: "", consent_sms: false })
            setAdding(false)
            await refresh()
            setOpen(result.enquiry)
            setNotes(result.enquiry.notes ?? "")
        } catch {
            setAddError("Couldn't save that enquiry.")
        }
    }

    async function openLead(id: string) {
        const detail = await getEnquiry(id)
        setOpen(detail)
        setNotes(detail.notes ?? "")
    }

    async function saveNotes() {
        if (!open) return
        setSaving(true)
        try {
            const updated = await updateEnquiry(open.id, { notes })
            setOpen(updated)
            await refresh()
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className="space-y-6">
            <PageHeader
                title="Enquiries"
                description="People who have got in touch but aren't patients yet."
            />

            <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={() => setAdding((v) => !v)}>
                    {adding ? "Cancel" : "Add enquiry"}
                </Button>
                {STAGES.map((s) => (
                    <Button
                        key={s.value}
                        size="sm"
                        variant={stage === s.value ? "default" : "outline"}
                        onClick={() => setStage(s.value)}
                    >
                        {s.label}
                    </Button>
                ))}
                <div className="ml-auto flex gap-2">
                    <Input
                        value={search}
                        placeholder="Name, phone or email"
                        className="w-56"
                        onChange={(e) => setSearch(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && void refresh()}
                    />
                    <Button size="sm" variant="outline" onClick={() => void refresh()}>
                        Search
                    </Button>
                </div>
            </div>

            {addNote && <p className="text-sm text-muted-foreground">{addNote}</p>}

            {adding && (
                <Card>
                    <CardContent className="space-y-3 pt-6">
                        <div className="grid gap-3 sm:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="fn">First name</Label>
                                <Input id="fn" value={form.first_name}
                                    onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="ln">Last name</Label>
                                <Input id="ln" value={form.last_name}
                                    onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="ph">Phone</Label>
                                <Input id="ph" value={form.phone}
                                    onChange={(e) => setForm({ ...form, phone: e.target.value })} />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="em">Email</Label>
                                <Input id="em" value={form.email}
                                    onChange={(e) => setForm({ ...form, email: e.target.value })} />
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="new-notes">Notes</Label>
                            <textarea id="new-notes" rows={2} value={form.notes}
                                className="w-full rounded-md border bg-background p-2 text-sm"
                                placeholder="What they asked about"
                                onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                        </div>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="checkbox" checked={form.consent_sms}
                                onChange={(e) => setForm({ ...form, consent_sms: e.target.checked })} />
                            They agreed we can text them
                        </label>
                        <p className="text-xs text-muted-foreground">
                            Only tick this if they actually said so — it is what allows a
                            campaign to text them later.
                        </p>
                        {addError && <p className="text-sm text-destructive" role="alert">{addError}</p>}
                        <Button size="sm" onClick={submitNew}>Save enquiry</Button>
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardContent className="pt-6">
                    {loading && <div className="h-10 rounded-md bg-muted animate-pulse" />}
                    {error && <p className="text-sm text-destructive">{error}</p>}
                    {!loading && !error && rows.length === 0 && (
                        <p className="text-sm text-muted-foreground">
                            No enquiries yet. They'll appear here as your forms send them in.
                        </p>
                    )}

                    <div className="space-y-2">
                        {rows.map((row) => (
                            <button
                                key={row.id}
                                onClick={() => void openLead(row.id)}
                                className="flex w-full flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-left hover:bg-muted/50"
                            >
                                <div className="min-w-0">
                                    <p className="font-medium">{displayName(row)}</p>
                                    <p className="text-xs text-muted-foreground">
                                        {row.phone_masked ?? row.email_masked ?? "no contact details"}
                                        {" · "}via {row.source}
                                        {row.has_notes && " · has notes"}
                                    </p>
                                </div>
                                <span className="rounded-full border px-2 py-0.5 text-xs">
                                    {stageLabel(row.stage)}
                                </span>
                            </button>
                        ))}
                    </div>

                    {total > rows.length && (
                        <p className="pt-3 text-xs text-muted-foreground">
                            Showing {rows.length} of {total}.
                        </p>
                    )}
                </CardContent>
            </Card>

            {open && (
                <Card>
                    <CardContent className="space-y-4 pt-6">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="font-medium">{displayName(open)}</p>
                                <p className="text-xs text-muted-foreground">
                                    {stageLabel(open.stage)} · via {open.source}
                                    {open.external_ref && ` · ref ${open.external_ref}`}
                                </p>
                            </div>
                            <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
                                Close
                            </Button>
                        </div>

                        {open.contact_id && (
                            <p className="text-sm text-muted-foreground">
                                Registered in the practice software — their patient record is
                                the place to look now.
                            </p>
                        )}

                        <div className="space-y-1.5">
                            <Label htmlFor="notes">Notes</Label>
                            <textarea
                                id="notes"
                                value={notes}
                                rows={4}
                                className="w-full rounded-md border bg-background p-2 text-sm"
                                placeholder="What happened when you contacted them"
                                onChange={(e) => setNotes(e.target.value)}
                            />
                            <Button size="sm" onClick={saveNotes} disabled={saving}>
                                {saving ? "Saving…" : "Save notes"}
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    )
}
