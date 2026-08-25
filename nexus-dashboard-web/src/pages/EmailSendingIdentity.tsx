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
    RefreshCw,
    Save,
} from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { CardsSkeleton } from "@/components/ui/skeletons"
import { useInstitutionScope } from "@/hooks/useInstitutionScope"
import {
    listEmailSendingIdentities,
    updateEmailSendingIdentity,
    verifyEmailSendingIdentity,
    type EmailIdentityStatus,
    type EmailSendingIdentity as Identity,
} from "@/lib/email-sending-identities-api"

const STATUS_META: Record<
    EmailIdentityStatus,
    { label: string; tone: "ok" | "warn" | "bad"; explain: string }
> = {
    verified: {
        label: "Verified",
        tone: "ok",
        explain: "Patients receive email from this address.",
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

    // A platform admin administers any practice and picks which; a clinic
    // admin has no choice to make and never sees the picker.
    const { institutionId, ready, picker } = useInstitutionScope()

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

    const copy = async (value: string, key: string) => {
        try {
            await navigator.clipboard.writeText(value)
            setCopied(key)
            setTimeout(() => setCopied(null), 1500)
        } catch {
            toast.error("Could not copy to clipboard")
        }
    }

    if (loading) return <CardsSkeleton />

    return (
        <div className="space-y-6">
            <PageHeader
                icon={MailCheck}
                title="Email Sending Address"
                description="The address patients see when your clinic emails them."
            />

            {picker}

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
                        <p className="text-sm text-muted-foreground">
                            Contact support to have your clinic’s own address set up —
                            there is nothing for you to configure.
                        </p>
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
                                <StatusBadge status={identity.status} />
                            </div>
                            <p className="text-sm text-muted-foreground">{meta.explain}</p>
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
        </div>
    )
}
