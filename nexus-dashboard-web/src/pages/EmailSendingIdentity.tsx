/**
 * Email sending identity — the address patients see mail from.
 *
 * The page is deliberately blunt about verification state. An unverified
 * domain does not fail loudly: it sends, lands in spam, and reports nothing.
 * So an unverified identity is shown as a problem to act on, not as a neutral
 * "pending" chip that reads like progress.
 */
import { useCallback, useEffect, useState } from "react"
import {
    AlertTriangle,
    Check,
    Copy,
    Loader2,
    MailCheck,
    Plus,
    Power,
    PowerOff,
    RefreshCw,
    Save,
    Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
import { CardsSkeleton } from "@/components/ui/skeletons"
import { useInstitutionScope } from "@/hooks/useInstitutionScope"
import { listAdminInstitutionLocations } from "@/lib/admin-api"
import {
    activateEmailSendingIdentity,
    deactivateEmailSendingIdentity,
    deleteEmailSendingIdentity,
    listEmailSendingIdentities,
    provisionEmailSendingIdentity,
    updateEmailSendingIdentity,
    verifyEmailSendingIdentity,
    type EmailIdentityStatus,
    type EmailSendingIdentity as Identity,
} from "@/lib/email-sending-identities-api"
import type { Location } from "@/types"

const INSTITUTION_SCOPE = "__institution__"

const STATUS_META: Record<
    EmailIdentityStatus,
    { label: string; tone: "ok" | "warn" | "bad"; explain: string }
> = {
    verified: {
        label: "Verified",
        tone: "ok",
        explain: "The sending domain is authenticated.",
    },
    pending_dns: {
        label: "Waiting on DNS",
        tone: "warn",
        explain:
            "The records below have not been published yet. Until they are, email cannot be sent from this address.",
    },
    verifying: {
        label: "Verifying",
        tone: "warn",
        explain:
            "Records published — waiting for them to propagate. This usually takes a few minutes.",
    },
    failed: {
        label: "Not verified",
        tone: "bad",
        explain:
            "Verification did not complete. Email is being sent from the platform address instead.",
    },
    revoked: {
        label: "Stopped verifying",
        tone: "bad",
        explain:
            "This domain was verified and no longer is — the DNS records may have been removed. Email is falling back to the platform address.",
    },
}

function errorMessage(err: unknown, fallback: string): string {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
        ?.detail
    return typeof detail === "string" ? detail : fallback
}

function StatusBadge({ status }: { status: EmailIdentityStatus }) {
    const meta = STATUS_META[status]
    if (meta.tone === "ok") {
        return (
            <Badge className="bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300">
                {meta.label}
            </Badge>
        )
    }
    return (
        <Badge variant={meta.tone === "bad" ? "destructive" : "secondary"}>
            {meta.label}
        </Badge>
    )
}

export default function EmailSendingIdentityPage() {
    const [identities, setIdentities] = useState<Identity[]>([])
    const [loading, setLoading] = useState(true)
    const [busyId, setBusyId] = useState<string | null>(null)
    const [drafts, setDrafts] = useState<
        Record<string, { from_name: string; reply_to_address: string }>
    >({})
    const [copied, setCopied] = useState<string | null>(null)
    const [locations, setLocations] = useState<Location[]>([])
    const [provisionOpen, setProvisionOpen] = useState(false)
    const [provisioning, setProvisioning] = useState(false)
    const [provisionScope, setProvisionScope] = useState(INSTITUTION_SCOPE)
    const [newFromName, setNewFromName] = useState("")
    const [newReplyTo, setNewReplyTo] = useState("")
    const [newLocalPart, setNewLocalPart] = useState("hello")
    const [deleteTarget, setDeleteTarget] = useState<Identity | null>(null)

    // A platform admin administers any practice and picks which; a clinic
    // admin has no choice to make and never sees the picker.
    const {
        institutionId,
        ready,
        picker,
        selectedInstitution,
        isPlatformAdmin,
    } = useInstitutionScope()

    useEffect(() => {
        if (!isPlatformAdmin || !selectedInstitution) {
            setLocations([])
            return
        }
        let cancelled = false
        listAdminInstitutionLocations(selectedInstitution.slug)
            .then((rows) => {
                if (!cancelled) setLocations(rows.filter((row) => row.is_active))
            })
            .catch(() => {
                if (!cancelled) toast.error("Failed to load practice locations")
            })
        return () => {
            cancelled = true
        }
    }, [isPlatformAdmin, selectedInstitution])

    const load = useCallback(async () => {
        if (!ready) {
            setIdentities([])
            setLoading(false)
            return
        }
        setLoading(true)
        try {
            const list = await listEmailSendingIdentities(institutionId)
            setIdentities(list)
            setDrafts(
                Object.fromEntries(
                    list.map((i) => [
                        i.id,
                        {
                            from_name: i.from_name ?? "",
                            reply_to_address: i.reply_to_address ?? "",
                        },
                    ]),
                ),
            )
        } catch {
            toast.error("Failed to load sending identities")
        } finally {
            setLoading(false)
        }
    }, [ready, institutionId])

    useEffect(() => {
        void load()
    }, [load])

    const replaceIdentity = (updated: Identity) =>
        setIdentities((prev) => prev.map((i) => (i.id === updated.id ? updated : i)))

    const recheck = async (identity: Identity) => {
        setBusyId(identity.id)
        try {
            const updated = await verifyEmailSendingIdentity(identity.id, institutionId)
            replaceIdentity(updated)
            toast[updated.is_sendable ? "success" : "message"](
                STATUS_META[updated.status].label,
                { description: STATUS_META[updated.status].explain },
            )
        } catch (err) {
            toast.error(errorMessage(err, "Could not check verification"))
        } finally {
            setBusyId(null)
        }
    }

    const save = async (identity: Identity) => {
        const draft = drafts[identity.id]
        setBusyId(identity.id)
        try {
            const updated = await updateEmailSendingIdentity(
                identity.id,
                {
                    from_name: draft.from_name.trim() || null,
                    reply_to_address: draft.reply_to_address.trim() || null,
                },
                institutionId,
            )
            replaceIdentity(updated)
            toast.success("Saved")
        } catch (err) {
            toast.error(errorMessage(err, "Could not save"))
        } finally {
            setBusyId(null)
        }
    }

    const openProvision = () => {
        const institutionExists = identities.some((identity) => !identity.location_id)
        const firstAvailableLocation = locations.find(
            (location) =>
                !identities.some((identity) => identity.location_id === location.id),
        )
        const scope = institutionExists
            ? (firstAvailableLocation?.id ?? INSTITUTION_SCOPE)
            : INSTITUTION_SCOPE
        setProvisionScope(scope)
        setNewFromName(
            scope === INSTITUTION_SCOPE
                ? (selectedInstitution?.name ?? "")
                : (firstAvailableLocation?.name ?? ""),
        )
        setNewReplyTo("")
        setNewLocalPart("hello")
        setProvisionOpen(true)
    }

    const changeProvisionScope = (scope: string) => {
        setProvisionScope(scope)
        setNewFromName(
            scope === INSTITUTION_SCOPE
                ? (selectedInstitution?.name ?? "")
                : (locations.find((location) => location.id === scope)?.name ?? ""),
        )
    }

    const provision = async () => {
        if (!institutionId) return
        setProvisioning(true)
        try {
            await provisionEmailSendingIdentity({
                institution_id: institutionId,
                location_id:
                    provisionScope === INSTITUTION_SCOPE ? null : provisionScope,
                from_name: newFromName.trim() || null,
                reply_to_address: newReplyTo.trim() || null,
                local_part: newLocalPart.trim(),
            })
            setProvisionOpen(false)
            toast.success("Sending address provisioned", {
                description:
                    "DNS verification runs automatically. Live sending remains off until explicitly activated.",
            })
            await load()
        } catch (err) {
            toast.error(errorMessage(err, "Could not provision sending address"))
        } finally {
            setProvisioning(false)
        }
    }

    const setActive = async (identity: Identity, active: boolean) => {
        setBusyId(identity.id)
        try {
            const updated = active
                ? await activateEmailSendingIdentity(identity.id)
                : await deactivateEmailSendingIdentity(identity.id)
            replaceIdentity(updated)
            toast.success(active ? "SES sending activated" : "SES sending deactivated", {
                description: active
                    ? "New workflow email for this scope now uses this address."
                    : "Workflow email now falls back to the platform address.",
            })
        } catch (err) {
            toast.error(errorMessage(err, `Could not ${active ? "activate" : "deactivate"}`))
        } finally {
            setBusyId(null)
        }
    }

    const remove = async () => {
        if (!deleteTarget) return
        setBusyId(deleteTarget.id)
        try {
            await deleteEmailSendingIdentity(deleteTarget.id)
            setDeleteTarget(null)
            toast.success("Sending address removed")
            await load()
        } catch (err) {
            toast.error(errorMessage(err, "Could not remove sending address"))
        } finally {
            setBusyId(null)
        }
    }

    const copy = async (value: string, key: string) => {
        try {
            await navigator.clipboard.writeText(value)
            setCopied(key)
            setTimeout(() => setCopied(null), 1500)
        } catch {
            toast.error("Could not copy to clipboard")
        }
    }

    const hasAvailableProvisionScope =
        !identities.some((identity) => !identity.location_id) ||
        locations.some(
            (location) =>
                !identities.some((identity) => identity.location_id === location.id),
        )

    if (loading) return <CardsSkeleton />

    return (
        <div className="space-y-6">
            <PageHeader
                icon={MailCheck}
                title="Email Sending Address"
                description="The address patients see when your clinic emails them."
            />

            {picker}

            {ready && isPlatformAdmin && (
                <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/20 px-4 py-3">
                    <div>
                        <p className="text-sm font-medium">Platform-managed email identity</p>
                        <p className="text-xs text-muted-foreground">
                            Provisioning and verification do not change live sending until you activate it.
                        </p>
                    </div>
                    <Button onClick={openProvision} disabled={!hasAvailableProvisionScope}>
                        <Plus className="mr-2 h-4 w-4" />
                        Add sending address
                    </Button>
                </div>
            )}

            {!ready && (
                <Card>
                    <CardContent className="py-10 text-center text-sm text-muted-foreground">
                        Choose a practice to see its sending address.
                    </CardContent>
                </Card>
            )}

            {ready && identities.length === 0 && (
                <Card>
                    <CardContent className="space-y-2 py-10 text-center">
                        <p className="text-sm text-muted-foreground">
                            No sending address is set up yet, so patient email goes out
                            from the ScaleNexus platform address.
                        </p>
                        {!isPlatformAdmin && (
                            <p className="text-sm text-muted-foreground">
                                Contact support to have your clinic’s own address set up —
                                there is nothing for you to configure.
                            </p>
                        )}
                    </CardContent>
                </Card>
            )}

            {identities.map((identity) => {
                const meta = STATUS_META[identity.status]
                const draft = drafts[identity.id] ?? {
                    from_name: "",
                    reply_to_address: "",
                }
                const needsDns = !identity.dns_self_published && identity.dns_records.length > 0

                return (
                    <Card key={identity.id}>
                        <CardHeader className="pb-3">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <CardTitle className="text-base">
                                    {identity.location_id
                                        ? "Location address"
                                        : "Clinic-wide address"}
                                </CardTitle>
                                <div className="flex items-center gap-2">
                                    <Badge variant={identity.is_active ? "default" : "outline"}>
                                        {identity.is_active ? "Active" : "Standby"}
                                    </Badge>
                                    <StatusBadge status={identity.status} />
                                </div>
                            </div>
                            <p className="text-sm text-muted-foreground">
                                {identity.status === "verified"
                                    ? identity.is_active
                                        ? "Patients receive workflow email from this address."
                                        : "Verified and ready, but live email still uses the platform address."
                                    : meta.explain}
                            </p>
                        </CardHeader>

                        <CardContent className="space-y-4">
                            <div className="rounded-md border border-border px-3 py-2">
                                <p className="text-xs text-muted-foreground">Sends from</p>
                                <p className="font-mono text-sm">
                                    {identity.from_name
                                        ? `${identity.from_name} <${identity.from_address}>`
                                        : identity.from_address}
                                </p>
                            </div>

                            {identity.failure_reason && (
                                <div className="flex gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2">
                                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                                    <p className="text-sm">{identity.failure_reason}</p>
                                </div>
                            )}

                            {!identity.is_active && identity.activation_blocker && (
                                <div className="flex gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                                    <PowerOff className="mt-0.5 h-4 w-4 shrink-0" />
                                    <p className="text-sm">{identity.activation_blocker}</p>
                                </div>
                            )}

                            {needsDns && (
                                <div className="space-y-2">
                                    <div>
                                        <Label className="text-sm">DNS records to publish</Label>
                                        <p className="text-xs text-muted-foreground">
                                            Add these to your domain’s DNS, then check again.
                                            Email cannot be sent from this address until they
                                            are live.
                                        </p>
                                    </div>
                                    <div className="overflow-x-auto rounded-md border border-border">
                                        <table className="w-full text-xs">
                                            <thead className="bg-muted/50">
                                                <tr>
                                                    <th className="px-2 py-1.5 text-left font-medium">Type</th>
                                                    <th className="px-2 py-1.5 text-left font-medium">Name</th>
                                                    <th className="px-2 py-1.5 text-left font-medium">Value</th>
                                                    <th className="w-8" />
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {identity.dns_records.map((record) => (
                                                    <tr key={record.name} className="border-t border-border">
                                                        <td className="px-2 py-1.5 font-mono">{record.type}</td>
                                                        <td className="px-2 py-1.5 font-mono break-all">{record.name}</td>
                                                        <td className="px-2 py-1.5 font-mono break-all">{record.value}</td>
                                                        <td className="px-2 py-1.5">
                                                            <button
                                                                type="button"
                                                                aria-label={`Copy ${record.name}`}
                                                                onClick={() =>
                                                                    void copy(record.value, record.name)
                                                                }
                                                            >
                                                                {copied === record.name ? (
                                                                    <Check className="h-3.5 w-3.5 text-emerald-600" />
                                                                ) : (
                                                                    <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                                                                )}
                                                            </button>
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            )}

                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label htmlFor={`name-${identity.id}`}>Display name</Label>
                                    <Input
                                        id={`name-${identity.id}`}
                                        value={draft.from_name}
                                        placeholder="Bright Smile Dental"
                                        onChange={(e) =>
                                            setDrafts((d) => ({
                                                ...d,
                                                [identity.id]: {
                                                    ...draft,
                                                    from_name: e.target.value,
                                                },
                                            }))
                                        }
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        What patients see as the sender.
                                    </p>
                                </div>
                                <div className="space-y-1.5">
                                    <Label htmlFor={`reply-${identity.id}`}>Reply-to address</Label>
                                    <Input
                                        id={`reply-${identity.id}`}
                                        value={draft.reply_to_address}
                                        placeholder="frontdesk@yourclinic.com"
                                        onChange={(e) =>
                                            setDrafts((d) => ({
                                                ...d,
                                                [identity.id]: {
                                                    ...draft,
                                                    reply_to_address: e.target.value,
                                                },
                                            }))
                                        }
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Where patient replies go.
                                    </p>
                                </div>
                            </div>

                            <div className="flex flex-wrap gap-2">
                                <Button
                                    onClick={() => void save(identity)}
                                    disabled={busyId === identity.id}
                                >
                                    {busyId === identity.id ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <Save className="mr-2 h-4 w-4" />
                                    )}
                                    Save
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => void recheck(identity)}
                                    disabled={busyId === identity.id}
                                >
                                    <RefreshCw className="mr-2 h-4 w-4" />
                                    Check verification
                                </Button>
                                {isPlatformAdmin && identity.is_active && (
                                    <Button
                                        variant="outline"
                                        onClick={() => void setActive(identity, false)}
                                        disabled={busyId === identity.id}
                                    >
                                        <PowerOff className="mr-2 h-4 w-4" />
                                        Deactivate
                                    </Button>
                                )}
                                {isPlatformAdmin && !identity.is_active && (
                                    <Button
                                        variant="outline"
                                        onClick={() => void setActive(identity, true)}
                                        disabled={
                                            busyId === identity.id || !identity.can_activate
                                        }
                                        title={identity.activation_blocker ?? undefined}
                                    >
                                        <Power className="mr-2 h-4 w-4" />
                                        Activate
                                    </Button>
                                )}
                                {isPlatformAdmin && (
                                    <Button
                                        variant="ghost"
                                        className="text-destructive hover:text-destructive"
                                        onClick={() => setDeleteTarget(identity)}
                                        disabled={busyId === identity.id}
                                    >
                                        <Trash2 className="mr-2 h-4 w-4" />
                                        Remove
                                    </Button>
                                )}
                                {identity.last_checked_at && (
                                    <span className="self-center text-xs text-muted-foreground">
                                        Last checked{" "}
                                        {new Date(identity.last_checked_at).toLocaleString()}
                                    </span>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                )
            })}

            <Dialog open={provisionOpen} onOpenChange={setProvisionOpen}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Add sending address</DialogTitle>
                        <DialogDescription>
                            Create an authenticated SES identity for this practice. It stays
                            on standby after verification until a platform administrator activates it.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="space-y-1.5">
                            <Label htmlFor="identity-scope">Applies to</Label>
                            <Select value={provisionScope} onValueChange={changeProvisionScope}>
                                <SelectTrigger id="identity-scope">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem
                                        value={INSTITUTION_SCOPE}
                                        disabled={identities.some((identity) => !identity.location_id)}
                                    >
                                        Entire practice
                                    </SelectItem>
                                    {locations.map((location) => (
                                        <SelectItem
                                            key={location.id}
                                            value={location.id}
                                            disabled={identities.some(
                                                (identity) => identity.location_id === location.id,
                                            )}
                                        >
                                            {location.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                A location address overrides the practice-wide address for that location.
                            </p>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="new-from-name">Display name</Label>
                                <Input
                                    id="new-from-name"
                                    value={newFromName}
                                    onChange={(event) => setNewFromName(event.target.value)}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="new-local-part">Address prefix</Label>
                                <Input
                                    id="new-local-part"
                                    value={newLocalPart}
                                    pattern="[a-z0-9._-]+"
                                    placeholder="hello"
                                    onChange={(event) =>
                                        setNewLocalPart(event.target.value.toLowerCase())
                                    }
                                />
                                <p className="text-xs text-muted-foreground">
                                    Usually “hello”; the domain is managed automatically.
                                </p>
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="new-reply-to">Reply-to address</Label>
                            <Input
                                id="new-reply-to"
                                type="email"
                                value={newReplyTo}
                                placeholder="frontdesk@yourclinic.com"
                                onChange={(event) => setNewReplyTo(event.target.value)}
                            />
                            <p className="text-xs text-muted-foreground">
                                Until the shared inbox is enabled, patient replies go here.
                            </p>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setProvisionOpen(false)}
                            disabled={provisioning}
                        >
                            Cancel
                        </Button>
                        <Button
                            onClick={() => void provision()}
                            disabled={provisioning || !newLocalPart.trim()}
                        >
                            {provisioning && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Provision
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Remove sending address?</DialogTitle>
                        <DialogDescription>
                            This removes the SES identity and managed DNS records. Workflow email
                            for this scope will fall back to the platform address.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={() => void remove()}>
                            {deleteTarget && busyId === deleteTarget.id && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Remove
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
