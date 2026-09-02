import { useCallback, useEffect, useMemo, useState } from "react"

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
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useLocationContext } from "@/context/LocationContext"
import { listFieldDefinitions } from "@/lib/custom-fields-api"
import {
    CONTACT_FIELD_OPTIONS,
    disconnect,
    getForm,
    listConnections,
    listForms,
    listProviders,
    saveMappings,
    startOAuth,
    syncConnection,
    updateForm,
    type FieldMapping,
    type FormConnection,
    type FormDetail,
    type FormSummary,
    type MappingUpsert,
    type ProviderStatus,
} from "@/lib/form-integrations-api"
import type { CustomFieldDefinition } from "@/types"

/**
 * Where a practice connects Meta and Typeform, and says what their forms mean.
 *
 * The order on the page is the order the work happens in, because doing it out
 * of order does not work: connect an account, sync its forms, map each question
 * onto a contact field or a custom field, and only then switch the form on.
 *
 * Enabling is the step with teeth. It registers delivery with the provider and
 * refuses a form whose questions include no email and no phone — a form that
 * accepts submissions nobody can act on looks live and produces nothing.
 */

const IGNORE = "__ignore__"
const NO_LOCATION = "__none__"

function formatWhen(value: string | null): string {
    if (!value) return "never"
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? "never" : date.toLocaleString()
}

/** The select value encodes both the kind and the target in one string. */
function mappingValue(mapping: FieldMapping): string {
    if (mapping.target_kind === "contact_field" && mapping.target_contact_field) {
        return `contact:${mapping.target_contact_field}`
    }
    if (mapping.target_kind === "custom_field" && mapping.target_custom_field_id) {
        return `custom:${mapping.target_custom_field_id}`
    }
    return IGNORE
}

function toUpsert(sourceKey: string, value: string): MappingUpsert {
    if (value.startsWith("contact:")) {
        return {
            source_key: sourceKey,
            target_kind: "contact_field",
            target_contact_field: value.slice("contact:".length),
        }
    }
    if (value.startsWith("custom:")) {
        return {
            source_key: sourceKey,
            target_kind: "custom_field",
            target_custom_field_id: value.slice("custom:".length),
        }
    }
    return { source_key: sourceKey, target_kind: "ignore" }
}

export default function FormIntegrations() {
    const { locations } = useLocationContext()
    const [providers, setProviders] = useState<ProviderStatus[]>([])
    const [connections, setConnections] = useState<FormConnection[]>([])
    const [forms, setForms] = useState<FormSummary[]>([])
    const [customFields, setCustomFields] = useState<CustomFieldDefinition[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")
    const [notice, setNotice] = useState("")
    const [busyId, setBusyId] = useState<string | null>(null)
    const [mappingForm, setMappingForm] = useState<FormDetail | null>(null)

    const refresh = useCallback(async () => {
        try {
            const [providerRows, connectionRows, formRows] = await Promise.all([
                listProviders(),
                listConnections(),
                listForms(),
            ])
            setProviders(providerRows)
            setConnections(connectionRows)
            setForms(formRows)
            setError("")
        } catch {
            setError("Couldn't load your form integrations.")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        void refresh()
        // Contact custom fields are the mapping targets for every question that
        // is not a name, an email or a phone number.
        listFieldDefinitions("contact")
            .then(setCustomFields)
            .catch(() => setCustomFields([]))
    }, [refresh])

    async function connect(provider: ProviderStatus) {
        setBusyId(provider.provider)
        setError("")
        try {
            const { authorization_url } = await startOAuth(provider.provider)
            // Full navigation, not a popup: the provider's consent screen
            // refuses to render in a frame, and a popup blocked by the browser
            // is indistinguishable from a broken button.
            window.location.assign(authorization_url)
        } catch {
            setError(`Couldn't start the ${provider.label} connection.`)
            setBusyId(null)
        }
    }

    async function sync(connection: FormConnection) {
        setBusyId(connection.id)
        setError("")
        setNotice("")
        try {
            const result = await syncConnection(connection.id)
            setNotice(
                `${connection.account_name || connection.account_ref}: ` +
                    `${result.discovered} form(s), ${result.created} new, ` +
                    `${result.new_fields} new question(s)` +
                    (result.archived ? `, ${result.archived} no longer listed` : ""),
            )
            await refresh()
        } catch (err) {
            setError(messageFrom(err, "Couldn't sync that account."))
        } finally {
            setBusyId(null)
        }
    }

    async function remove(connection: FormConnection) {
        setBusyId(connection.id)
        try {
            await disconnect(connection.id)
            await refresh()
        } catch (err) {
            setError(messageFrom(err, "Couldn't disconnect that account."))
        } finally {
            setBusyId(null)
        }
    }

    async function patchForm(
        form: FormSummary,
        body: Parameters<typeof updateForm>[1],
    ) {
        setBusyId(form.id)
        setError("")
        try {
            await updateForm(form.id, body)
            await refresh()
        } catch (err) {
            setError(messageFrom(err, "Couldn't update that form."))
        } finally {
            setBusyId(null)
        }
    }

    async function openMapping(form: FormSummary) {
        setBusyId(form.id)
        try {
            setMappingForm(await getForm(form.id))
        } catch {
            setError("Couldn't load that form's questions.")
        } finally {
            setBusyId(null)
        }
    }

    const formsByConnection = useMemo(() => {
        const grouped = new Map<string, FormSummary[]>()
        for (const form of forms) {
            const bucket = grouped.get(form.connection_id) ?? []
            bucket.push(form)
            grouped.set(form.connection_id, bucket)
        }
        return grouped
    }, [forms])

    return (
        <div className="space-y-6">
            <PageHeader
                title="Lead forms"
                description="Connect Meta and Typeform, choose which forms bring people in, and map their questions onto your contact fields."
            />

            {error && (
                <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                    {error}
                </p>
            )}
            {notice && (
                <p className="rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
                    {notice}
                </p>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Connect an account</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    {providers.map((provider) => (
                        <div
                            key={provider.provider}
                            className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3"
                        >
                            <div className="min-w-0">
                                <p className="font-medium">{provider.label}</p>
                                <p className="text-xs text-muted-foreground">
                                    {provider.configured
                                        ? `${provider.connection_count} account(s) connected`
                                        : "Not available on this deployment yet."}
                                </p>
                            </div>
                            <Button
                                size="sm"
                                disabled={!provider.configured || busyId === provider.provider}
                                onClick={() => void connect(provider)}
                            >
                                {provider.connection_count > 0 ? "Connect another" : "Connect"}
                            </Button>
                        </div>
                    ))}
                    {providers.length === 0 && !loading && (
                        <p className="text-sm text-muted-foreground">
                            No form providers are configured on this deployment.
                        </p>
                    )}
                </CardContent>
            </Card>

            {loading && <div className="h-24 animate-pulse rounded-md bg-muted" />}

            {connections.map((connection) => (
                <Card key={connection.id}>
                    <CardHeader>
                        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                            {connection.account_name || connection.account_ref}
                            <Badge variant="outline">{connection.provider}</Badge>
                            {connection.status === "needs_reauth" && (
                                <Badge variant="destructive">Reconnect needed</Badge>
                            )}
                            {connection.disconnected_at && (
                                <Badge variant="outline">Disconnected</Badge>
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <p className="text-xs text-muted-foreground">
                                Last synced: {formatWhen(connection.last_synced_at)}
                                {connection.last_error && ` · ${connection.last_error}`}
                            </p>
                            <div className="flex gap-2">
                                {/* Both need a live token, so a disconnected
                                    account offers neither — its forms and the
                                    leads they brought in stay visible. */}
                                {!connection.disconnected_at && (
                                    <>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={busyId === connection.id}
                                            onClick={() => void sync(connection)}
                                        >
                                            Sync forms
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={busyId === connection.id}
                                            onClick={() => void remove(connection)}
                                        >
                                            Disconnect
                                        </Button>
                                    </>
                                )}
                            </div>
                        </div>

                        <div className="space-y-2">
                            {(formsByConnection.get(connection.id) ?? []).map((form) => (
                                <div key={form.id} className="rounded-md border p-3">
                                    <div className="flex flex-wrap items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <p className="font-medium">
                                                {form.name}{" "}
                                                {form.archived_at && (
                                                    <span className="text-xs text-muted-foreground">
                                                        (no longer at the provider)
                                                    </span>
                                                )}
                                            </p>
                                            <p className="text-xs text-muted-foreground">
                                                Last submission: {formatWhen(form.last_submission_at)}
                                                {form.context_keys.length > 0 &&
                                                    ` · branches on: ${form.context_keys.join(", ")}`}
                                            </p>
                                            {form.webhook_last_error && (
                                                <p className="text-xs text-destructive">
                                                    {form.webhook_last_error}
                                                </p>
                                            )}
                                            {form.unprocessed_count > 0 && (
                                                <p className="text-xs text-destructive">
                                                    {form.unprocessed_count} submission
                                                    {form.unprocessed_count === 1 ? "" : "s"}{" "}
                                                    didn't become a contact
                                                    {form.last_issue ? ` — ${form.last_issue}` : "."}
                                                </p>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={busyId === form.id}
                                                onClick={() => void openMapping(form)}
                                            >
                                                Map fields
                                            </Button>
                                            <div className="flex items-center gap-2">
                                                <Switch
                                                    id={`enable-${form.id}`}
                                                    checked={form.is_enabled}
                                                    disabled={busyId === form.id || !!form.archived_at}
                                                    onCheckedChange={(checked) =>
                                                        void patchForm(form, { is_enabled: checked })
                                                    }
                                                />
                                                <Label htmlFor={`enable-${form.id}`} className="text-xs">
                                                    {form.is_enabled ? "On" : "Off"}
                                                </Label>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                                        <div className="space-y-1.5">
                                            <Label className="text-xs">Location these leads belong to</Label>
                                            <Select
                                                value={form.location_id ?? NO_LOCATION}
                                                onValueChange={(value) =>
                                                    void patchForm(form, {
                                                        location_id: value === NO_LOCATION ? null : value,
                                                    })
                                                }
                                            >
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value={NO_LOCATION}>Decide later</SelectItem>
                                                    {locations.map((loc) => (
                                                        <SelectItem key={loc.id} value={loc.id}>
                                                            {loc.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-1.5">
                                            <Label className="text-xs" htmlFor={`source-${form.id}`}>
                                                Lead source label
                                            </Label>
                                            <Input
                                                id={`source-${form.id}`}
                                                defaultValue={form.source_name}
                                                onBlur={(e) => {
                                                    const next = e.target.value.trim()
                                                    if (next && next !== form.source_name) {
                                                        void patchForm(form, { source_name: next })
                                                    }
                                                }}
                                            />
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {(formsByConnection.get(connection.id) ?? []).length === 0 && (
                                <p className="text-sm text-muted-foreground">
                                    No forms synced from this account yet. Use “Sync forms”.
                                </p>
                            )}
                        </div>
                    </CardContent>
                </Card>
            ))}

            {mappingForm && (
                <MappingDialog
                    form={mappingForm}
                    customFields={customFields}
                    onClose={() => setMappingForm(null)}
                    onSaved={async () => {
                        setMappingForm(null)
                        await refresh()
                    }}
                    onError={setError}
                />
            )}
        </div>
    )
}

/**
 * What each question on one form means here.
 *
 * The consent block sits in the same dialog on purpose. Submitting a form is
 * not consent to be texted, so the practice declares what their own wording
 * obtained — and the wording itself is stored, because that is the evidence.
 */
function MappingDialog({
    form,
    customFields,
    onClose,
    onSaved,
    onError,
}: {
    form: FormDetail
    customFields: CustomFieldDefinition[]
    onClose: () => void
    onSaved: () => Promise<void> | void
    onError: (message: string) => void
}) {
    const [values, setValues] = useState<Record<string, string>>(() =>
        Object.fromEntries(
            form.mappings.map((mapping) => [mapping.source_key, mappingValue(mapping)]),
        ),
    )
    const [consentSms, setConsentSms] = useState(form.consent_sms)
    const [consentEmail, setConsentEmail] = useState(form.consent_email)
    const [wording, setWording] = useState("")
    const [saving, setSaving] = useState(false)

    // Every question the last sync found, plus any mapping row for a question
    // that has since disappeared — hiding those would silently drop a decision.
    const rows = useMemo(() => {
        const seen = new Set(form.fields.map((field) => field.key))
        const extra = form.mappings
            .filter((mapping) => !seen.has(mapping.source_key))
            .map((mapping) => ({
                key: mapping.source_key,
                label: mapping.source_label || mapping.source_key,
                type: mapping.source_type || "unknown",
                options: [] as string[],
            }))
        return [...form.fields, ...extra]
    }, [form])

    const reachable = useMemo(
        () =>
            Object.values(values).some(
                (value) => value === "contact:email" || value === "contact:phone",
            ),
        [values],
    )

    async function save() {
        setSaving(true)
        try {
            await saveMappings(
                form.id,
                rows.map((row) => toUpsert(row.key, values[row.key] ?? IGNORE)),
            )
            if (
                consentSms !== form.consent_sms ||
                consentEmail !== form.consent_email ||
                wording.trim()
            ) {
                await updateForm(form.id, {
                    consent_sms: consentSms,
                    consent_email: consentEmail,
                    ...(wording.trim() ? { consent_wording: wording.trim() } : {}),
                })
            }
            await onSaved()
        } catch (err) {
            onError(messageFrom(err, "Couldn't save that field map."))
        } finally {
            setSaving(false)
        }
    }

    return (
        <Dialog open onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>{form.name}</DialogTitle>
                    <DialogDescription>
                        Say what each question means. Anything left unmapped is ignored —
                        it is never guessed at.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-3">
                    {rows.map((row) => (
                        <div key={row.key} className="grid gap-2 sm:grid-cols-2 sm:items-center">
                            <div className="min-w-0">
                                <p className="truncate text-sm font-medium">{row.label}</p>
                                <p className="text-xs text-muted-foreground">
                                    {row.type}
                                    {row.options && row.options.length > 0 &&
                                        ` · ${row.options.slice(0, 4).join(" / ")}`}
                                </p>
                            </div>
                            <Select
                                value={values[row.key] ?? IGNORE}
                                onValueChange={(value) =>
                                    setValues((prev) => ({ ...prev, [row.key]: value }))
                                }
                            >
                                <SelectTrigger aria-label={`Map ${row.label}`}>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={IGNORE}>Ignore</SelectItem>
                                    {CONTACT_FIELD_OPTIONS.map((option) => (
                                        <SelectItem
                                            key={option.value}
                                            value={`contact:${option.value}`}
                                        >
                                            Contact · {option.label}
                                        </SelectItem>
                                    ))}
                                    {customFields.map((definition) => (
                                        <SelectItem
                                            key={definition.id}
                                            value={`custom:${definition.id}`}
                                        >
                                            Custom · {definition.field_name}
                                            {definition.is_phi ? " (private)" : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    ))}
                    {rows.length === 0 && (
                        <p className="text-sm text-muted-foreground">
                            This form has no questions yet. Sync the account again.
                        </p>
                    )}
                </div>

                <p className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">
                    Answers mapped to a custom field that is not marked private are the ones
                    a workflow can branch on. Names, emails and phone numbers go on the
                    contact record instead, and are read back through merge fields.
                </p>

                <div className="space-y-3 rounded-md border p-3">
                    <p className="text-sm font-medium">What this form's wording obtains</p>
                    <div className="flex flex-wrap gap-4">
                        <label className="flex items-center gap-2 text-sm">
                            <Switch checked={consentSms} onCheckedChange={setConsentSms} />
                            Consent to text
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                            <Switch checked={consentEmail} onCheckedChange={setConsentEmail} />
                            Consent to email
                        </label>
                    </div>
                    {(consentSms || consentEmail) && (
                        <div className="space-y-1.5">
                            <Label htmlFor="consent-wording" className="text-xs">
                                The exact wording shown on the form
                            </Label>
                            <Textarea
                                id="consent-wording"
                                value={wording}
                                placeholder="I agree to be contacted by the practice about my enquiry."
                                onChange={(e) => setWording(e.target.value)}
                            />
                            <p className="text-xs text-muted-foreground">
                                Stored as the evidence of what the person agreed to.
                            </p>
                        </div>
                    )}
                </div>

                {!reachable && (
                    <p className="text-sm text-destructive">
                        Map one question to an email or a phone number. Without one, a
                        submission arrives with no way to reach the person.
                    </p>
                )}

                <DialogFooter>
                    <Button variant="ghost" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button onClick={() => void save()} disabled={saving}>
                        {saving ? "Saving…" : "Save mapping"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}

/** The server's own wording when it has one — it names the setting to fix. */
function messageFrom(error: unknown, fallback: string): string {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response
        ?.data?.detail
    return typeof detail === "string" && detail ? detail : fallback
}
