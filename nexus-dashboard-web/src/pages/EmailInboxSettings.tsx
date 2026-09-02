import { useCallback, useEffect, useMemo, useState } from "react"
import { AlertTriangle, Check, Copy, Inbox, Loader2, Save } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { CardsSkeleton } from "@/components/ui/skeletons"
import { useAuth } from "@/context/AuthContext"
import { useLocationContext } from "@/context/LocationContext"
import { useInstitutionScope } from "@/hooks/useInstitutionScope"
import { listAdminInstitutionLocations } from "@/lib/admin-api"
import {
    getEmailInboxSettings,
    updateEmailInboxSettings,
    type EmailInboxSettings,
} from "@/lib/email-inbox-settings-api"
import { listEmailSendingIdentities, type EmailSendingIdentity } from "@/lib/email-sending-identities-api"
import type { Location, LocationInfo } from "@/types"

const DEFAULT = "__default__"
const PLATFORM_DOMAIN = "__platform__"

function errorMessage(err: unknown, fallback: string) {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    return typeof detail === "string" ? detail : fallback
}

export default function EmailInboxSettingsPage() {
    const { user } = useAuth()
    const scope = useInstitutionScope()
    const locationContext = useLocationContext()
    const isLocationAdmin = user?.role === "LOCATION_ADMIN"
    const [adminLocations, setAdminLocations] = useState<Location[]>([])
    const [selectedScope, setSelectedScope] = useState(DEFAULT)
    const [value, setValue] = useState<EmailInboxSettings | null>(null)
    const [loading, setLoading] = useState(false)
    const [saving, setSaving] = useState(false)
    const [copied, setCopied] = useState(false)
    const [domains, setDomains] = useState<EmailSendingIdentity[]>([])

    useEffect(() => {
        if (!scope.isPlatformAdmin || !scope.selectedInstitution) {
            setAdminLocations([])
            return
        }
        listAdminInstitutionLocations(scope.selectedInstitution.slug)
            .then((rows) => setAdminLocations(rows.filter((row) => row.is_active)))
            .catch(() => toast.error("Failed to load practice locations"))
    }, [scope.isPlatformAdmin, scope.selectedInstitution])

    const locations = useMemo(
        () => (scope.isPlatformAdmin ? adminLocations : locationContext.locations) as Array<Location | LocationInfo>,
        [scope.isPlatformAdmin, adminLocations, locationContext.locations],
    )
    const locationId = isLocationAdmin
        ? locationContext.selectedLocationId ?? undefined
        : selectedScope === DEFAULT ? undefined : selectedScope

    useEffect(() => {
        setSelectedScope(DEFAULT)
    }, [scope.institutionId])

    const load = useCallback(async () => {
        if (!scope.ready || (isLocationAdmin && !locationId)) {
            setValue(null)
            return
        }
        setLoading(true)
        try {
            setValue(await getEmailInboxSettings(scope.institutionId, locationId))
        } catch (err) {
            toast.error(errorMessage(err, "Failed to load inbox settings"))
        } finally {
            setLoading(false)
        }
    }, [scope.ready, scope.institutionId, isLocationAdmin, locationId])

    useEffect(() => { void load() }, [load])
    useEffect(() => {
        if (!scope.ready) return
        listEmailSendingIdentities(scope.institutionId)
            .then(setDomains)
            .catch(() => setDomains([]))
    }, [scope.ready, scope.institutionId])

    const receivingReady = useMemo(() => {
        if (!value) return false
        if (!value.email_identity_id) return value.platform_fallback_ready
        return Boolean(
            value.receiving_pipeline_ready
            && domains.find((domain) => domain.id === value.email_identity_id)?.inbound_enabled,
        )
    }, [domains, value])

    const save = async () => {
        if (!value) return
        setSaving(true)
        try {
            const updated = await updateEmailInboxSettings(
                {
                    is_enabled: value.is_enabled,
                    allow_new_contacts: value.allow_new_contacts,
                    stop_automation_on_reply: value.stop_automation_on_reply,
                    forward_to: value.forward_to || null,
                    email_identity_id: value.email_identity_id,
                },
                scope.institutionId,
                locationId,
            )
            setValue(updated)
            toast.success("Inbound email settings saved")
        } catch (err) {
            toast.error(errorMessage(err, "Could not save inbox settings"))
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className="space-y-6">
            <PageHeader
                icon={Inbox}
                title="Inbound Email"
                description="Receive patient email in the shared inbox and reply without leaving ScaleNexus."
                actions={scope.picker}
            />
            {scope.ready && locations.length > 0 && !isLocationAdmin && (
                <div className="flex items-center gap-2">
                    <Label>Applies to</Label>
                    <Select value={selectedScope} onValueChange={setSelectedScope}>
                        <SelectTrigger className="w-72"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value={DEFAULT}>All locations (default)</SelectItem>
                            {locations.map((location) => (
                                <SelectItem key={location.id} value={location.id}>{location.name}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            )}
            {loading ? <CardsSkeleton /> : value && (
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between gap-3">
                            <CardTitle className="text-base">Receiving and routing</CardTitle>
                            {receivingReady ? (
                                <Badge className="bg-emerald-100 text-emerald-800"><Check className="mr-1 h-3 w-3" />Receiving ready</Badge>
                            ) : <Badge variant="destructive"><AlertTriangle className="mr-1 h-3 w-3" />Receiving setup pending</Badge>}
                        </div>
                        {value.inherited && <p className="text-xs text-muted-foreground">This location currently inherits the practice default. Saving creates a location override.</p>}
                    </CardHeader>
                    <CardContent className="space-y-6">
                        <div className="flex items-start justify-between gap-4">
                            <div><Label>Enable inbound email</Label><p className="text-xs text-muted-foreground">Accept direct mail and enable in-app replies for this scope.</p></div>
                            <Switch checked={value.is_enabled} onCheckedChange={(checked) => setValue({ ...value, is_enabled: checked })} />
                        </div>
                        <div className="space-y-2">
                            <Label>Receiving domain</Label>
                            <Select
                                value={value.email_identity_id ?? PLATFORM_DOMAIN}
                                onValueChange={(selected) => {
                                    const emailIdentityId = selected === PLATFORM_DOMAIN ? null : selected
                                    const selectedReady = emailIdentityId === null
                                        ? value.platform_fallback_ready
                                        : Boolean(
                                            value.receiving_pipeline_ready
                                            && domains.find((domain) => domain.id === emailIdentityId)?.inbound_enabled,
                                        )
                                    setValue({
                                        ...value,
                                        email_identity_id: emailIdentityId,
                                        is_enabled: selectedReady ? value.is_enabled : false,
                                        inbox_address: null,
                                    })
                                }}
                            >
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={PLATFORM_DOMAIN}>ScaleNexus fallback domain</SelectItem>
                                    {domains.filter((domain) => domain.inbound_domain).map((domain) => (
                                        <SelectItem key={domain.id} value={domain.id} disabled={!domain.inbound_enabled}>
                                            {domain.inbound_domain}{domain.inbound_enabled ? "" : " — awaiting activation"}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">A clinic-owned receiving subdomain keeps patient-facing reply addresses on the clinic’s brand.</p>
                        </div>
                        <div className="space-y-2">
                            <Label>Inbox address</Label>
                            <div className="flex gap-2">
                                <Input readOnly value={value.inbox_address ?? (value.platform_ready ? "Select a location to get its address" : "Available after AWS receiving is deployed")} />
                                <Button variant="outline" disabled={!value.inbox_address} onClick={async () => {
                                    if (!value.inbox_address) return
                                    await navigator.clipboard.writeText(value.inbox_address)
                                    setCopied(true); window.setTimeout(() => setCopied(false), 1500)
                                }}><Copy className="mr-2 h-4 w-4" />{copied ? "Copied" : "Copy"}</Button>
                            </div>
                            <p className="text-xs text-muted-foreground">Publish this address or forward an existing clinic mailbox to it. Its signature prevents mail being filed into another practice.</p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="forward-to">Forward staff copy to (optional)</Label>
                            <Input id="forward-to" type="email" placeholder="frontdesk@clinic.com" value={value.forward_to ?? ""} onChange={(e) => setValue({ ...value, forward_to: e.target.value })} />
                        </div>
                        <div className="flex items-start justify-between gap-4">
                            <div><Label>Create contacts from new senders</Label><p className="text-xs text-muted-foreground">Unknown senders become leads in Contacts. Leave off if only existing patients should enter the inbox.</p></div>
                            <Switch checked={value.allow_new_contacts} onCheckedChange={(checked) => setValue({ ...value, allow_new_contacts: checked })} />
                        </div>
                        <div className="flex items-start justify-between gap-4">
                            <div><Label>Stop other automation after a reply</Label><p className="text-xs text-muted-foreground">Prevents follow-up workflows continuing after a patient has responded.</p></div>
                            <Switch checked={value.stop_automation_on_reply} onCheckedChange={(checked) => setValue({ ...value, stop_automation_on_reply: checked })} />
                        </div>
                        <Button
                            onClick={() => void save()}
                            disabled={saving || (value.is_enabled && !receivingReady)}
                        >
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Save settings
                        </Button>
                    </CardContent>
                </Card>
            )}
        </div>
    )
}
