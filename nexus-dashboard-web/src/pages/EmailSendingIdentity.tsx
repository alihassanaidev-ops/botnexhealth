import { useCallback, useEffect, useMemo, useState } from "react"
import { Check, Copy, Loader2, MailCheck, Plus, Power, PowerOff, RefreshCw, Save, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { CardsSkeleton } from "@/components/ui/skeletons"
import { useInstitutionScope } from "@/hooks/useInstitutionScope"
import { listAdminInstitutionLocations } from "@/lib/admin-api"
import {
    activateEmailSendingIdentity,
    activateInboundDomain,
    createEmailSenderAddress,
    deactivateEmailSendingIdentity,
    deactivateInboundDomain,
    deleteEmailSenderAddress,
    deleteEmailSendingIdentity,
    listEmailSendingIdentities,
    makeEmailSenderAddressDefault,
    provisionEmailSendingIdentity,
    updateEmailSenderAddress,
    verifyEmailSendingIdentity,
    type EmailSenderAddress,
    type EmailSendingIdentity,
} from "@/lib/email-sending-identities-api"
import { listInstitutionPortalLocations } from "@/lib/institution-portal-api"

const PRACTICE = "__practice__"

function detail(err: unknown, fallback: string) {
    const value = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    return typeof value === "string" ? value : fallback
}

export default function EmailSendingIdentityPage() {
    const { institutionId, ready, picker, selectedInstitution, isPlatformAdmin } = useInstitutionScope()
    const [domains, setDomains] = useState<EmailSendingIdentity[]>([])
    const [locations, setLocations] = useState<Array<{ id: string; name: string }>>([])
    const [loading, setLoading] = useState(true)
    const [busy, setBusy] = useState<string | null>(null)
    const [copied, setCopied] = useState<string | null>(null)
    const [domainOpen, setDomainOpen] = useState(false)
    const [addressDomain, setAddressDomain] = useState<EmailSendingIdentity | null>(null)
    const [deleteDomain, setDeleteDomain] = useState<EmailSendingIdentity | null>(null)
    const [deleteAddress, setDeleteAddress] = useState<EmailSenderAddress | null>(null)
    const [domainName, setDomainName] = useState("")
    const [inboundDomain, setInboundDomain] = useState("")
    const [scope, setScope] = useState(PRACTICE)
    const [localPart, setLocalPart] = useState("appointments")
    const [fromName, setFromName] = useState("")
    const [externalReplyTo, setExternalReplyTo] = useState("")
    const [drafts, setDrafts] = useState<Record<string, { from_name: string; external_reply_to: string }>>({})

    const load = useCallback(async () => {
        if (!ready) return setLoading(false)
        setLoading(true)
        try {
            const rows = await listEmailSendingIdentities(institutionId)
            setDomains(rows)
            setDrafts(Object.fromEntries(rows.flatMap((domain) => domain.addresses.map((address) => [address.id, {
                from_name: address.from_name ?? "",
                external_reply_to: address.external_reply_to ?? "",
            }]))))
        } catch (err) {
            toast.error(detail(err, "Could not load email domains"))
        } finally {
            setLoading(false)
        }
    }, [institutionId, ready])

    useEffect(() => { void load() }, [load])
    useEffect(() => {
        if (!ready) return
        const request = isPlatformAdmin && selectedInstitution
            ? listAdminInstitutionLocations(selectedInstitution.slug)
            : listInstitutionPortalLocations()
        request.then((rows) => setLocations(rows.map((row) => ({ id: row.id, name: row.name })))).catch(() => setLocations([]))
    }, [isPlatformAdmin, ready, selectedInstitution])

    const resetAddress = (domain?: EmailSendingIdentity) => {
        setAddressDomain(domain ?? null)
        setScope(PRACTICE)
        setLocalPart("appointments")
        setFromName(selectedInstitution?.name ?? "")
        setExternalReplyTo("")
    }

    const openDomain = () => {
        setDomainName("")
        setInboundDomain("")
        resetAddress()
        setDomainOpen(true)
    }

    const provision = async () => {
        if (!institutionId) return
        setBusy("provision")
        try {
            await provisionEmailSendingIdentity({
                institution_id: institutionId,
                location_id: scope === PRACTICE ? null : scope,
                domain: domainName.trim() || null,
                inbound_domain: inboundDomain.trim() || null,
                local_part: localPart.trim(),
                from_name: fromName.trim() || null,
                reply_to_address: externalReplyTo.trim() || null,
            })
            setDomainOpen(false)
            toast.success("Domain registered", { description: "Publish the displayed DNS records, then verify and activate it." })
            await load()
        } catch (err) { toast.error(detail(err, "Could not register domain")) }
        finally { setBusy(null) }
    }

    const addAddress = async () => {
        if (!addressDomain) return
        setBusy("add-address")
        try {
            await createEmailSenderAddress(addressDomain.id, {
                location_id: scope === PRACTICE ? null : scope,
                local_part: localPart.trim(),
                from_name: fromName.trim() || null,
                external_reply_to: externalReplyTo.trim() || null,
            })
            setAddressDomain(null)
            toast.success("Sender address added")
            await load()
        } catch (err) { toast.error(detail(err, "Could not add sender address")) }
        finally { setBusy(null) }
    }

    const act = async (key: string, action: () => Promise<unknown>, success: string) => {
        setBusy(key)
        try { await action(); toast.success(success); await load() }
        catch (err) { toast.error(detail(err, "Could not update email setup")) }
        finally { setBusy(null) }
    }

    const copy = async (value: string, key: string) => {
        await navigator.clipboard.writeText(value)
        setCopied(key)
        window.setTimeout(() => setCopied(null), 1200)
    }

    const scopeName = (address: EmailSenderAddress) => address.location_id
        ? locations.find((location) => location.id === address.location_id)?.name ?? "Location"
        : "Practice default"

    const activeAddresses = useMemo(() => domains.flatMap((domain) => domain.addresses).filter((a) => a.is_active), [domains])
    if (loading) return <CardsSkeleton />

    return <div className="space-y-6">
        <PageHeader icon={MailCheck} title="Email domains & addresses" description="Clinic-owned domains, receiving subdomains, and the addresses workflows can use." />
        {picker}
        {ready && <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/20 px-4 py-3">
            <div><p className="text-sm font-medium">{domains.length} domain{domains.length === 1 ? "" : "s"}, {activeAddresses.length} active sender address{activeAddresses.length === 1 ? "" : "es"}</p><p className="text-xs text-muted-foreground">Locations inherit the practice default unless a location default is selected.</p></div>
            {isPlatformAdmin && <Button onClick={openDomain}><Plus className="mr-2 h-4 w-4" />Register domain</Button>}
        </div>}

        {ready && domains.length === 0 && <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">No clinic domain is registered. Workflow email uses the ScaleNexus fallback until a platform administrator registers one.</CardContent></Card>}

        {domains.map((domain) => {
            const records = [...domain.dns_records, ...domain.inbound_dns_records]
            return <Card key={domain.id}>
                <CardHeader className="pb-3"><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle className="text-base font-mono">{domain.domain}</CardTitle><p className="mt-1 text-xs text-muted-foreground">{domain.inbound_domain ? `Replies: ${domain.inbound_domain}` : "Uses the ScaleNexus reply domain unless an external Reply-To is set."}</p></div><div className="flex gap-2"><Badge variant={domain.status === "verified" ? "default" : "secondary"}>{domain.status.replace(/_/g, " ")}</Badge><Badge variant={domain.is_active ? "default" : "outline"}>{domain.is_active ? "Sending active" : "Standby"}</Badge>{domain.inbound_domain && <Badge variant={domain.inbound_enabled ? "default" : "outline"}>{domain.inbound_enabled ? "Receiving active" : "Receiving standby"}</Badge>}</div></div></CardHeader>
                <CardContent className="space-y-5">
                    {records.length > 0 && <div className="space-y-2"><Label>DNS records</Label><div className="overflow-x-auto rounded-md border"><table className="w-full text-xs"><thead className="bg-muted/50"><tr><th className="px-2 py-1.5 text-left">Purpose</th><th className="px-2 py-1.5 text-left">Type</th><th className="px-2 py-1.5 text-left">Name</th><th className="px-2 py-1.5 text-left">Value</th><th /></tr></thead><tbody>{records.map((record, index) => <tr className="border-t" key={`${record.name}-${record.type}-${index}`}><td className="px-2 py-1.5 capitalize">{record.purpose.replace(/_/g, " ")}</td><td className="px-2 py-1.5 font-mono">{record.type}</td><td className="px-2 py-1.5 font-mono break-all">{record.name}</td><td className="px-2 py-1.5 font-mono break-all">{record.value}</td><td className="px-2"><button onClick={() => void copy(record.value, `${domain.id}-${index}`)}>{copied === `${domain.id}-${index}` ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}</button></td></tr>)}</tbody></table></div></div>}

                    <div className="space-y-3"><div className="flex items-center justify-between"><Label>Sender addresses</Label><Button variant="outline" size="sm" onClick={() => resetAddress(domain)}><Plus className="mr-1 h-3.5 w-3.5" />Add address</Button></div>{domain.addresses.length === 0 && <p className="text-sm text-muted-foreground">No addresses use this domain yet.</p>}{domain.addresses.map((address) => {
                        const draft = drafts[address.id] ?? { from_name: "", external_reply_to: "" }
                        return <div key={address.id} className="space-y-3 rounded-md border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="font-mono text-sm">{address.from_address}</p><p className="text-xs text-muted-foreground">{scopeName(address)}</p></div><div className="flex gap-2">{address.is_default && <Badge>Default</Badge>}<Badge variant={address.is_active ? "outline" : "secondary"}>{address.is_active ? "Enabled" : "Disabled"}</Badge></div></div><div className="grid gap-3 md:grid-cols-2"><div><Label>Display name</Label><Input value={draft.from_name} onChange={(e) => setDrafts((all) => ({ ...all, [address.id]: { ...draft, from_name: e.target.value } }))} /></div><div><Label>External Reply-To (optional)</Label><Input type="email" placeholder="Leave blank for managed inbox" value={draft.external_reply_to} onChange={(e) => setDrafts((all) => ({ ...all, [address.id]: { ...draft, external_reply_to: e.target.value } }))} /></div></div><div className="flex flex-wrap gap-2"><Button size="sm" onClick={() => void act(`save-${address.id}`, () => updateEmailSenderAddress(address.id, { from_name: draft.from_name || null, external_reply_to: draft.external_reply_to || null }), "Address saved")} disabled={busy === `save-${address.id}`}><Save className="mr-1 h-3.5 w-3.5" />Save</Button>{!address.is_default && <Button size="sm" variant="outline" onClick={() => void act(`default-${address.id}`, () => makeEmailSenderAddressDefault(address.id), "Default sender changed")}>Make default</Button>}<Button size="sm" variant="outline" disabled={address.is_default} onClick={() => void act(`toggle-${address.id}`, () => updateEmailSenderAddress(address.id, { is_active: !address.is_active }), address.is_active ? "Address disabled" : "Address enabled")}>{address.is_active ? <PowerOff className="mr-1 h-3.5 w-3.5" /> : <Power className="mr-1 h-3.5 w-3.5" />}{address.is_active ? "Disable" : "Enable"}</Button><Button size="sm" variant="ghost" className="text-destructive" disabled={address.is_default} onClick={() => setDeleteAddress(address)}><Trash2 className="mr-1 h-3.5 w-3.5" />Remove</Button></div></div>
                    })}</div>

                    <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void act(`verify-${domain.id}`, () => verifyEmailSendingIdentity(domain.id, institutionId), "Verification refreshed")}><RefreshCw className="mr-2 h-4 w-4" />Check DNS</Button>{isPlatformAdmin && <>{domain.is_active ? <Button variant="outline" onClick={() => void act(`off-${domain.id}`, () => deactivateEmailSendingIdentity(domain.id), "Sending domain deactivated")}><PowerOff className="mr-2 h-4 w-4" />Deactivate sending</Button> : <Button variant="outline" disabled={!domain.can_activate} onClick={() => void act(`on-${domain.id}`, () => activateEmailSendingIdentity(domain.id), "Sending domain activated")}><Power className="mr-2 h-4 w-4" />Activate sending</Button>}{domain.inbound_domain && (domain.inbound_enabled ? <Button variant="outline" onClick={() => void act(`inoff-${domain.id}`, () => deactivateInboundDomain(domain.id), "Receiving domain deactivated")}>Deactivate receiving</Button> : <Button variant="outline" onClick={() => void act(`inon-${domain.id}`, () => activateInboundDomain(domain.id), "Receiving domain activated")}>Activate receiving</Button>)}<Button variant="ghost" className="text-destructive" onClick={() => setDeleteDomain(domain)}><Trash2 className="mr-2 h-4 w-4" />Remove domain</Button></>}</div>
                </CardContent>
            </Card>
        })}

        <DomainDialog open={domainOpen} onOpenChange={setDomainOpen} locations={locations} values={{ domainName, inboundDomain, scope, localPart, fromName, externalReplyTo }} setters={{ setDomainName, setInboundDomain, setScope, setLocalPart, setFromName, setExternalReplyTo }} busy={busy === "provision"} onSave={() => void provision()} />
        <AddressDialog domain={addressDomain} onClose={() => setAddressDomain(null)} locations={locations} scope={scope} setScope={setScope} localPart={localPart} setLocalPart={setLocalPart} fromName={fromName} setFromName={setFromName} externalReplyTo={externalReplyTo} setExternalReplyTo={setExternalReplyTo} busy={busy === "add-address"} onSave={() => void addAddress()} />
        <ConfirmDelete open={Boolean(deleteDomain)} title="Remove this domain?" description="Its sender addresses and provider resources will be removed. Existing reply tokens should be allowed to age out before doing this." onClose={() => setDeleteDomain(null)} onConfirm={() => deleteDomain && void act(`delete-${deleteDomain.id}`, () => deleteEmailSendingIdentity(deleteDomain.id), "Domain removed").then(() => setDeleteDomain(null))} />
        <ConfirmDelete open={Boolean(deleteAddress)} title="Remove this sender address?" description="Published workflows pinned to it will stop instead of silently changing brands." onClose={() => setDeleteAddress(null)} onConfirm={() => deleteAddress && void act(`delete-address-${deleteAddress.id}`, () => deleteEmailSenderAddress(deleteAddress.id), "Address removed").then(() => setDeleteAddress(null))} />
    </div>
}

type DomainValues = { domainName: string; inboundDomain: string; scope: string; localPart: string; fromName: string; externalReplyTo: string }
type DomainSetters = { setDomainName: (v: string) => void; setInboundDomain: (v: string) => void; setScope: (v: string) => void; setLocalPart: (v: string) => void; setFromName: (v: string) => void; setExternalReplyTo: (v: string) => void }

function ScopeFields({ locations, scope, setScope, localPart, setLocalPart, fromName, setFromName, externalReplyTo, setExternalReplyTo }: { locations: Array<{ id: string; name: string }>; scope: string; setScope: (v: string) => void; localPart: string; setLocalPart: (v: string) => void; fromName: string; setFromName: (v: string) => void; externalReplyTo: string; setExternalReplyTo: (v: string) => void }) {
    return <><div><Label>Address applies to</Label><Select value={scope} onValueChange={setScope}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value={PRACTICE}>Entire practice</SelectItem>{locations.map((location) => <SelectItem value={location.id} key={location.id}>{location.name}</SelectItem>)}</SelectContent></Select></div><div className="grid gap-3 sm:grid-cols-2"><div><Label>Address prefix</Label><Input value={localPart} onChange={(e) => setLocalPart(e.target.value.toLowerCase())} placeholder="appointments" /></div><div><Label>Display name</Label><Input value={fromName} onChange={(e) => setFromName(e.target.value)} /></div></div><div><Label>External Reply-To (optional)</Label><Input type="email" value={externalReplyTo} onChange={(e) => setExternalReplyTo(e.target.value)} placeholder="Leave blank for the managed inbox" /></div></>
}

function DomainDialog({ open, onOpenChange, locations, values, setters, busy, onSave }: { open: boolean; onOpenChange: (v: boolean) => void; locations: Array<{ id: string; name: string }>; values: DomainValues; setters: DomainSetters; busy: boolean; onSave: () => void }) {
    return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>Register clinic email domain</DialogTitle><DialogDescription>The clinic publishes the generated DKIM, SPF and MX records. Leave Domain blank only when deliberately creating a ScaleNexus fallback subdomain.</DialogDescription></DialogHeader><div className="space-y-4"><div><Label>Clinic sending domain</Label><Input value={values.domainName} onChange={(e) => setters.setDomainName(e.target.value.toLowerCase())} placeholder="clinic.com" /></div><div><Label>Managed receiving subdomain</Label><Input value={values.inboundDomain} onChange={(e) => setters.setInboundDomain(e.target.value.toLowerCase())} placeholder="reply.clinic.com" /><p className="text-xs text-muted-foreground">Use a dedicated subdomain. Never replace the clinic’s main mailbox MX.</p></div><ScopeFields locations={locations} scope={values.scope} setScope={setters.setScope} localPart={values.localPart} setLocalPart={setters.setLocalPart} fromName={values.fromName} setFromName={setters.setFromName} externalReplyTo={values.externalReplyTo} setExternalReplyTo={setters.setExternalReplyTo} /></div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button disabled={busy || !values.localPart.trim()} onClick={onSave}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Register</Button></DialogFooter></DialogContent></Dialog>
}

function AddressDialog({ domain, onClose, locations, scope, setScope, localPart, setLocalPart, fromName, setFromName, externalReplyTo, setExternalReplyTo, busy, onSave }: { domain: EmailSendingIdentity | null; onClose: () => void; locations: Array<{ id: string; name: string }>; scope: string; setScope: (v: string) => void; localPart: string; setLocalPart: (v: string) => void; fromName: string; setFromName: (v: string) => void; externalReplyTo: string; setExternalReplyTo: (v: string) => void; busy: boolean; onSave: () => void }) {
    return <Dialog open={Boolean(domain)} onOpenChange={(open) => !open && onClose()}><DialogContent><DialogHeader><DialogTitle>Add sender address</DialogTitle><DialogDescription>Create another address on {domain?.domain}. It can be assigned to the practice or one location.</DialogDescription></DialogHeader><div className="space-y-4"><ScopeFields locations={locations} scope={scope} setScope={setScope} localPart={localPart} setLocalPart={setLocalPart} fromName={fromName} setFromName={setFromName} externalReplyTo={externalReplyTo} setExternalReplyTo={setExternalReplyTo} /></div><DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={busy || !localPart.trim()} onClick={onSave}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Add address</Button></DialogFooter></DialogContent></Dialog>
}

function ConfirmDelete({ open, title, description, onClose, onConfirm }: { open: boolean; title: string; description: string; onClose: () => void; onConfirm: () => void }) {
    return <Dialog open={open} onOpenChange={(value) => !value && onClose()}><DialogContent><DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription>{description}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button><Button variant="destructive" onClick={onConfirm}>Remove</Button></DialogFooter></DialogContent></Dialog>
}
