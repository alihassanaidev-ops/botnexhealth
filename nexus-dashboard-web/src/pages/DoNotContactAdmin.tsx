/**
 * Do Not Contact — staff-initiated opt-outs (Plan 08 U-2b). INSTITUTION_ADMIN.
 *
 * The compliance gate already blocks every channel for an active DoNotContact.
 * This is the privileged entry point to *record* an opt-out received off-channel
 * (in person, by phone to a human, or by email) and to *release* one. Phone
 * values are masked server-side; scope is location- or institution-tier.
 */

import { useEffect, useState } from "react"
import { ShieldOff, Plus, Loader2, RefreshCcw, Trash2 } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import {
    createDoNotContact,
    listDoNotContact,
    releaseDoNotContact,
    type DncScope,
} from "@/lib/do-not-contact-api"
import {
    listInstitutionPortalLocations,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"
import type { DncRecord } from "@/types"

interface FormState {
    phone: string
    scope: DncScope
    locationId: string
    reason: string
}

const EMPTY_FORM: FormState = { phone: "", scope: "institution", locationId: "", reason: "" }

function errorDetail(e: unknown, fallback: string): string {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    return detail ?? (e instanceof Error ? e.message : fallback)
}

export default function DoNotContactAdmin() {
    const [records, setRecords] = useState<DncRecord[]>([])
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [loading, setLoading] = useState(true)
    const [formOpen, setFormOpen] = useState(false)
    const [form, setForm] = useState<FormState>(EMPTY_FORM)
    const [saving, setSaving] = useState(false)
    const [releaseTarget, setReleaseTarget] = useState<DncRecord | null>(null)
    const [releasePhone, setReleasePhone] = useState("")
    const [releasing, setReleasing] = useState(false)

    async function refresh() {
        setLoading(true)
        try {
            setRecords(await listDoNotContact())
        } catch (e) {
            toast.error(errorDetail(e, "Failed to load do-not-contact list"))
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

    function openCreate() {
        setForm(EMPTY_FORM)
        setFormOpen(true)
    }

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
            toast.success("Do-not-contact recorded")
            setFormOpen(false)
            await refresh()
        } catch (e) {
            toast.error(errorDetail(e, "Failed to record do-not-contact"))
        } finally {
            setSaving(false)
        }
    }

    function openRelease(r: DncRecord) {
        setReleasePhone("")
        setReleaseTarget(r)
    }

    async function handleRelease() {
        if (!releaseTarget) return
        const phone = releasePhone.trim()
        if (!phone) {
            toast.error("Enter the full phone number to release")
            return
        }
        setReleasing(true)
        try {
            const released = await releaseDoNotContact(phone)
            if (released) {
                toast.success("Do-not-contact released")
            } else {
                toast.error("No active record matched that phone number")
            }
            setReleaseTarget(null)
            await refresh()
        } catch (e) {
            toast.error(errorDetail(e, "Failed to release do-not-contact"))
        } finally {
            setReleasing(false)
        }
    }

    function locationName(id: string | null): string | null {
        if (!id) return null
        return locations.find((l) => l.id === id)?.name ?? id
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>

            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <ShieldOff className="h-7 w-7" />
                        Do Not Contact
                    </h2>
                    <p className="text-muted-foreground mt-1">
                        Record opt-outs received off-channel (in person, by phone, or by email). An
                        active record blocks every channel for its scope.
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                    <Button size="sm" className="gap-1.5" onClick={openCreate}>
                        <Plus className="h-4 w-4" />
                        Add opt-out
                    </Button>
                </div>
            </div>

            <Card>
                <CardContent className="p-0">
                    <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                        <p className="text-xs text-muted-foreground">
                            <span className="font-medium text-foreground">{records.length}</span> active
                        </p>
                    </div>

                    {loading ? (
                        <div className="space-y-2 p-4">
                            {Array.from({ length: 5 }).map((_, i) => (
                                <Skeleton key={i} className="h-10 w-full" />
                            ))}
                        </div>
                    ) : records.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 px-4 py-16 text-center text-muted-foreground">
                            <div className="grid size-12 place-items-center rounded-full bg-muted">
                                <ShieldOff className="h-6 w-6 opacity-40" />
                            </div>
                            <p className="text-sm font-medium text-foreground/70">No do-not-contact records</p>
                            <p className="text-xs">Add one when a patient opts out off-channel.</p>
                        </div>
                    ) : (
                        <ul className="divide-y divide-border">
                            {records.map((r, i) => (
                                <li key={`${r.phone_masked}-${i}`} className="flex items-center justify-between gap-3 px-4 py-2.5">
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <span className="font-mono text-sm">{r.phone_masked}</span>
                                            <span className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                                                {r.scope}
                                            </span>
                                            {r.scope === "location" && locationName(r.location_id) && (
                                                <span className="text-xs text-muted-foreground truncate">
                                                    {locationName(r.location_id)}
                                                </span>
                                            )}
                                        </div>
                                        <p className="mt-0.5 truncate text-xs text-muted-foreground">
                                            {r.reason || <span className="italic opacity-60">No reason</span>}
                                            <span className="opacity-60"> · {r.source} · {new Date(r.created_at).toLocaleDateString()}</span>
                                        </p>
                                    </div>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 shrink-0"
                                        onClick={() => openRelease(r)}
                                        aria-label="Release"
                                    >
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </Button>
                                </li>
                            ))}
                        </ul>
                    )}
                </CardContent>
            </Card>

            {/* Add opt-out dialog */}
            <Dialog open={formOpen} onOpenChange={(o) => !o && setFormOpen(false)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Add do-not-contact</DialogTitle>
                        <DialogDescription>
                            Blocks all channels for the chosen scope. This is honored immediately.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Phone</label>
                            <Input
                                autoFocus
                                placeholder="+15551234567"
                                value={form.phone}
                                onChange={(e) => setForm((s) => ({ ...s, phone: e.target.value }))}
                            />
                        </div>
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Scope</label>
                            <Select
                                value={form.scope}
                                onValueChange={(v) => setForm((s) => ({ ...s, scope: v as DncScope }))}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
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
                                    onValueChange={(v) => setForm((s) => ({ ...s, locationId: v }))}
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder="Select location" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {locations.map((loc) => (
                                            <SelectItem key={loc.id} value={loc.id}>
                                                {loc.name}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Reason (optional)</label>
                            <Textarea
                                placeholder="e.g. Patient asked in person to stop all messages"
                                value={form.reason}
                                maxLength={500}
                                onChange={(e) => setForm((s) => ({ ...s, reason: e.target.value }))}
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" size="sm" onClick={() => setFormOpen(false)} disabled={saving}>
                            Cancel
                        </Button>
                        <Button size="sm" className="gap-1.5" onClick={handleCreate} disabled={saving}>
                            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                            Record
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Release confirm dialog */}
            <Dialog open={releaseTarget !== null} onOpenChange={(o) => !o && setReleaseTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Release this do-not-contact?</DialogTitle>
                        <DialogDescription>
                            {releaseTarget?.phone_masked} will be contactable again for its scope. The
                            stored number is masked, so re-enter the full phone to confirm the release.
                        </DialogDescription>
                    </DialogHeader>
                    <div>
                        <label className="mb-1 block text-xs font-medium text-muted-foreground">Full phone number</label>
                        <Input
                            autoFocus
                            placeholder="+15551234567"
                            value={releasePhone}
                            onChange={(e) => setReleasePhone(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") handleRelease() }}
                        />
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReleaseTarget(null)} disabled={releasing}>
                            Keep block
                        </Button>
                        <Button variant="destructive" onClick={handleRelease} disabled={releasing}>
                            {releasing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Release
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
