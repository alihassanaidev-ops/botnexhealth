import { useEffect, useState } from "react"

import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { useLocationContext } from "@/context/LocationContext"
import {
    createEnquirySource,
    listEnquirySources,
    rotateEnquirySource,
    updateEnquirySource,
    type EnquirySource,
    type EnquirySourceCreated,
} from "@/lib/enquiry-sources-api"

/**
 * Where a clinic issues the credential an external form posts leads to.
 *
 * One per form rather than one per clinic, so a practice can run a website
 * form, a Typeform landing page and a paid-ads form at once and retire one
 * without touching the others.
 *
 * The URL is shown once, when it is created or rotated. The server keeps only a
 * hash, so nothing can hand it back afterwards — which the page has to say
 * plainly at the moment it matters, or someone will close it and assume they can
 * look it up later.
 */
function formatWhen(value: string | null): string {
    if (!value) return "never"
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? "never" : date.toLocaleString()
}

export default function EnquirySources() {
    const { locations } = useLocationContext()
    const [sources, setSources] = useState<EnquirySource[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")

    const [label, setLabel] = useState("")
    const [locationId, setLocationId] = useState<string>("__none__")
    const [secret, setSecret] = useState("")
    const [creating, setCreating] = useState(false)
    // The one-time reveal. Held in state only; never re-fetched.
    const [revealed, setRevealed] = useState<EnquirySourceCreated | null>(null)
    const [copied, setCopied] = useState(false)

    async function refresh() {
        try {
            setSources(await listEnquirySources())
            setError("")
        } catch {
            setError("Couldn't load your contact forms.")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        void refresh()
    }, [])

    async function create() {
        if (!label.trim()) return
        setCreating(true)
        try {
            const created = await createEnquirySource({
                label: label.trim(),
                location_id: locationId === "__none__" ? null : locationId,
                signing_secret: secret.trim() || null,
            })
            setRevealed(created)
            setLabel("")
            setSecret("")
            setCopied(false)
            await refresh()
        } catch {
            setError("Couldn't create that form.")
        } finally {
            setCreating(false)
        }
    }

    async function toggle(source: EnquirySource) {
        await updateEnquirySource(source.id, { is_active: !source.is_active })
        await refresh()
    }

    async function rotate(source: EnquirySource) {
        const created = await rotateEnquirySource(source.id)
        setRevealed(created)
        setCopied(false)
        await refresh()
    }

    return (
        <div className="space-y-6">
            <PageHeader
                title="Contact forms"
                description="Give each website or marketing form its own secure address for adding contacts."
            />

            {revealed && (
                <Card className="border-primary">
                    <CardHeader>
                        <CardTitle className="text-base">
                            Paste this into {revealed.label}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <code className="block break-all rounded-md bg-muted p-3 text-xs">
                            {revealed.intake_url}
                        </code>
                        <div className="flex gap-2">
                            <Button
                                size="sm"
                                onClick={() => {
                                    void navigator.clipboard?.writeText(revealed.intake_url)
                                    setCopied(true)
                                }}
                            >
                                {copied ? "Copied" : "Copy"}
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setRevealed(null)}>
                                Done
                            </Button>
                        </div>
                        <p className="text-sm text-destructive">
                            This is the only time you'll see it. We store it hashed, so we
                            can't show it again — if it's lost, rotate the form for a new one.
                        </p>
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Add a form</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-1.5">
                        <Label htmlFor="label">Name it</Label>
                        <Input
                            id="label"
                            value={label}
                            placeholder="Typeform — new patient interest"
                            onChange={(e) => setLabel(e.target.value)}
                        />
                        <p className="text-xs text-muted-foreground">
                            So you know which one to switch off later.
                        </p>
                    </div>

                    <div className="space-y-1.5">
                        <Label>Location these contacts belong to</Label>
                        <Select value={locationId} onValueChange={setLocationId}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__none__">Decide later</SelectItem>
                                {locations.map((loc) => (
                                    <SelectItem key={loc.id} value={loc.id}>
                                        {loc.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="secret">Signing secret (optional)</Label>
                        <Input
                            id="secret"
                            value={secret}
                            placeholder="Leave empty unless your form can sign requests"
                            onChange={(e) => setSecret(e.target.value)}
                        />
                        <p className="text-xs text-muted-foreground">
                            If your form provider can sign its requests, set the same secret
                            here and we'll check every one. The address alone proves who is
                            calling; a signature also proves the message wasn't altered.
                        </p>
                    </div>

                    <Button onClick={create} disabled={creating || !label.trim()}>
                        {creating ? "Creating…" : "Create"}
                    </Button>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Your forms</CardTitle>
                </CardHeader>
                <CardContent>
                    {loading && <div className="h-10 rounded-md bg-muted animate-pulse" />}
                    {error && <p className="text-sm text-destructive">{error}</p>}
                    {!loading && !error && sources.length === 0 && (
                        <p className="text-sm text-muted-foreground">
                            No forms yet. Add one above and paste its address into your form
                            provider.
                        </p>
                    )}
                    <div className="space-y-2">
                        {sources.map((source) => (
                            <div
                                key={source.id}
                                className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3"
                            >
                                <div className="min-w-0">
                                    <p className="font-medium">
                                        {source.label}{" "}
                                        {!source.is_active && (
                                            <span className="text-xs text-muted-foreground">
                                                (switched off)
                                            </span>
                                        )}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        Last contact received: {formatWhen(source.last_used_at)}
                                        {source.has_signing_secret && " · signed"}
                                    </p>
                                </div>
                                <div className="flex gap-2">
                                    <Button size="sm" variant="outline" onClick={() => rotate(source)}>
                                        New address
                                    </Button>
                                    <Button size="sm" variant="ghost" onClick={() => toggle(source)}>
                                        {source.is_active ? "Switch off" : "Switch on"}
                                    </Button>
                                </div>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
