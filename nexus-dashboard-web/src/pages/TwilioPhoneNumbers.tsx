import { useEffect, useState, useCallback } from "react"
import {
    Phone,
    RefreshCw,
    Send,
    MessageSquare,
    Mic,
    Image,
    CheckCircle2,
    Ban,
    Unlock,
} from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/foundation/compat/card"
import { Button } from "@/components/foundation/compat/button"
import { Badge } from "@/components/foundation/compat/badge"
import { Skeleton } from "@/components/foundation/compat/skeleton"
import { Input } from "@/components/foundation/compat/input"
import { Label } from "@/components/foundation/compat/label"
import { Textarea } from "@/components/foundation/compat/textarea"
import { UiSelect } from "@/components/foundation/Primitives"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/foundation/compat/dialog"
import { toast } from "sonner"
import {
    createSmsSuppression,
    listSmsLocations,
    listSmsSuppressions,
    listTwilioPhoneNumbers,
    releaseSmsSuppression,
    sendSms,
} from "@/lib/admin-api"
import type { SmsLocation, SmsSuppression, TwilioPhoneNumber } from "@/types"

export default function TwilioPhoneNumbers() {
    const [numbers, setNumbers] = useState<TwilioPhoneNumber[]>([])
    const [smsLocations, setSmsLocations] = useState<SmsLocation[]>([])
    const [suppressions, setSuppressions] = useState<SmsSuppression[]>([])
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)

    // SMS Dialog
    const [smsOpen, setSmsOpen] = useState(false)
    const [selectedFrom, setSelectedFrom] = useState("")
    const [selectedLocationId, setSelectedLocationId] = useState("")
    const [toNumber, setToNumber] = useState("")
    const [smsBody, setSmsBody] = useState("")
    const [sending, setSending] = useState(false)
    const [suppressionLocationId, setSuppressionLocationId] = useState("")
    const [suppressionPhone, setSuppressionPhone] = useState("")
    const [suppressionReason, setSuppressionReason] = useState("")
    const [savingSuppression, setSavingSuppression] = useState(false)

    const fetchNumbers = useCallback(async (isRefresh = false) => {
        if (isRefresh) setRefreshing(true)
        else setLoading(true)
        try {
            const [numberData, locationData, suppressionData] = await Promise.all([
                listTwilioPhoneNumbers(),
                listSmsLocations(),
                listSmsSuppressions(),
            ])
            setNumbers(numberData)
            setSmsLocations(locationData)
            setSuppressions(suppressionData)
            setSuppressionLocationId((current) => current || locationData[0]?.id || "")
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load phone numbers"
            toast.error(message)
        } finally {
            setLoading(false)
            setRefreshing(false)
        }
    }, [])

    useEffect(() => {
        fetchNumbers()
    }, [fetchNumbers])

    function openSmsDialog(phoneNumber: string) {
        setSelectedFrom(phoneNumber)
        const matchingLocation = smsLocations.find((loc) => loc.twilio_from_number === phoneNumber)
        setSelectedLocationId(matchingLocation?.id || "")
        setToNumber("")
        setSmsBody("")
        setSmsOpen(true)
    }

    async function handleSendSms() {
        if (!toNumber.trim() || !smsBody.trim() || !selectedLocationId) {
            toast.error("Recipient, location, and message body are required")
            return
        }
        setSending(true)
        try {
            const result = await sendSms({
                from_number: selectedFrom,
                to_number: toNumber.trim(),
                body: smsBody.trim(),
                institution_location_id: selectedLocationId,
            })
            toast.success(
                result.status === "suppressed"
                    ? `SMS suppressed for ${result.to_number_masked || "recipient"}`
                    : `SMS ${result.status} — SID: ${result.message_sid}`
            )
            setSmsOpen(false)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to send SMS"
            toast.error(message)
        } finally {
            setSending(false)
        }
    }

    async function handleCreateSuppression() {
        if (!suppressionLocationId || !suppressionPhone.trim()) {
            toast.error("Location and phone number are required")
            return
        }
        setSavingSuppression(true)
        try {
            await createSmsSuppression({
                location_id: suppressionLocationId,
                phone: suppressionPhone.trim(),
                reason: suppressionReason.trim() || undefined,
            })
            toast.success("SMS suppression added")
            setSuppressionPhone("")
            setSuppressionReason("")
            setSuppressions(await listSmsSuppressions())
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to add suppression"
            toast.error(message)
        } finally {
            setSavingSuppression(false)
        }
    }

    async function handleReleaseSuppression(id: string) {
        try {
            await releaseSmsSuppression(id)
            toast.success("SMS suppression released")
            setSuppressions(await listSmsSuppressions())
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to release suppression"
            toast.error(message)
        }
    }

    const smsCapable = numbers.filter((n) => n.capabilities.sms)
    const voiceCapable = numbers.filter((n) => n.capabilities.voice)

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={MessageSquare}
                title="Twilio Phone Numbers"
                description="Manage platform phone numbers and send SMS messages."
                actions={
                    <Button
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        onClick={() => fetchNumbers(true)}
                        disabled={refreshing}
                    >
                        <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                }
            />

            {/* Stats row */}
            {!loading && (
                <div className="grid gap-4 md:grid-cols-3">
                    {[
                        { label: "Total Numbers", value: numbers.length, icon: Phone },
                        { label: "SMS Capable", value: smsCapable.length, icon: MessageSquare },
                        { label: "Voice Capable", value: voiceCapable.length, icon: Mic },
                    ].map((card) => (
                        <div key={card.label} className="rounded-xl border border-border/80 bg-card p-5 shadow-sm">
                                <div className="mb-3 flex items-center justify-between">
                                    <span className="text-xs font-medium text-muted-foreground">{card.label}</span>
                                    <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted">
                                        <card.icon className="h-4 w-4 text-foreground" />
                                    </div>
                                </div>
                                <div className="text-2xl font-semibold tabular-nums tracking-tight text-foreground">
                                    {card.value}
                                </div>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    numbers
                                </p>
                        </div>
                    ))}
                </div>
            )}

            {/* Phone Numbers Table */}
            <Card>
                <CardHeader>
                    <CardTitle>Phone Numbers</CardTitle>
                    <CardDescription>
                        All incoming phone numbers registered on the Twilio account.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="space-y-3">
                            {Array.from({ length: 4 }).map((_, i) => (
                                <Skeleton key={i} className="h-14 w-full" />
                            ))}
                        </div>
                    ) : numbers.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                            <Phone className="h-10 w-10 mb-3 opacity-40" />
                            <p className="font-medium">No phone numbers found</p>
                            <p className="text-sm">
                                Make sure TWILLIO_SID and TWILLIO_API_SECRET are configured correctly.
                            </p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b text-left text-muted-foreground">
                                        <th className="pb-3 font-medium">Phone Number</th>
                                        <th className="pb-3 font-medium">Friendly Name</th>
                                        <th className="pb-3 font-medium">Capabilities</th>
                                        <th className="pb-3 font-medium">SID</th>
                                        <th className="pb-3 font-medium sr-only">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {numbers.map((number) => (
                                        <tr key={number.sid} className="border-b last:border-0">
                                            <td className="py-3 font-mono font-medium">
                                                {number.phone_number}
                                            </td>
                                            <td className="py-3 text-muted-foreground">
                                                {number.friendly_name || "—"}
                                            </td>
                                            <td className="py-3">
                                                <div className="flex gap-1.5 flex-wrap">
                                                    {number.capabilities.voice && (
                                                        <Badge variant="secondary" className="gap-1 text-xs">
                                                            <Mic className="h-3 w-3" />
                                                            Voice
                                                        </Badge>
                                                    )}
                                                    {number.capabilities.sms && (
                                                        <Badge variant="secondary" className="gap-1 text-xs">
                                                            <MessageSquare className="h-3 w-3" />
                                                            SMS
                                                        </Badge>
                                                    )}
                                                    {number.capabilities.mms && (
                                                        <Badge variant="secondary" className="gap-1 text-xs">
                                                            <Image className="h-3 w-3" />
                                                            MMS
                                                        </Badge>
                                                    )}
                                                </div>
                                            </td>
                                            <td className="py-3 font-mono text-xs text-muted-foreground">
                                                {number.sid}
                                            </td>
                                            <td className="py-3 text-right">
                                                {number.capabilities.sms && (
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="gap-1.5"
                                                        onClick={() => openSmsDialog(number.phone_number)}
                                                    >
                                                        <Send className="h-3.5 w-3.5" />
                                                        Send SMS
                                                    </Button>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Ban className="h-4 w-4" />
                        SMS Suppressions
                    </CardTitle>
                    <CardDescription>
                        Manually opt a patient phone number out of outbound SMS.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    {/* Add-suppression form */}
                    <div className="rounded-xl border bg-muted/30 p-4">
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                            <div className="space-y-1.5">
                                <Label htmlFor="suppression-location">Location</Label>
                                <UiSelect
                                    id="suppression-location"
                                    value={suppressionLocationId}
                                    onChange={(e) => setSuppressionLocationId(e.target.value)}
                                >
                                    <option value="">Select a location</option>
                                    {smsLocations.map((loc) => (
                                        <option key={loc.id} value={loc.id}>
                                            {loc.institution_name} — {loc.location_name}
                                        </option>
                                    ))}
                                </UiSelect>
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="suppression-phone">Phone</Label>
                                <Input
                                    id="suppression-phone"
                                    placeholder="+12125551234"
                                    value={suppressionPhone}
                                    onChange={(e) => setSuppressionPhone(e.target.value)}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="suppression-reason">
                                    Reason <span className="font-normal text-muted-foreground">(optional)</span>
                                </Label>
                                <Input
                                    id="suppression-reason"
                                    placeholder="Manual opt-out"
                                    value={suppressionReason}
                                    onChange={(e) => setSuppressionReason(e.target.value)}
                                />
                            </div>
                        </div>
                        <div className="mt-4 flex justify-end">
                            <Button
                                className="gap-2"
                                onClick={handleCreateSuppression}
                                disabled={savingSuppression || !suppressionLocationId || !suppressionPhone.trim()}
                            >
                                {savingSuppression ? (
                                    <RefreshCw className="h-4 w-4 animate-spin" />
                                ) : (
                                    <Ban className="h-4 w-4" />
                                )}
                                Suppress number
                            </Button>
                        </div>
                    </div>

                    {/* Active suppressions */}
                    <div className="overflow-hidden rounded-xl border">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
                                    <th className="px-4 py-3 font-medium">Phone</th>
                                    <th className="px-4 py-3 font-medium">Source</th>
                                    <th className="px-4 py-3 font-medium">Reason</th>
                                    <th className="px-4 py-3 font-medium">Created</th>
                                    <th className="px-4 py-3 text-right font-medium">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {suppressions.length === 0 ? (
                                    <tr>
                                        <td colSpan={5}>
                                            <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                                                <div className="grid size-10 place-items-center rounded-full bg-muted">
                                                    <Ban className="h-5 w-5 text-muted-foreground" />
                                                </div>
                                                <p className="text-sm font-medium text-foreground">No active SMS suppressions</p>
                                                <p className="max-w-xs text-xs text-muted-foreground">
                                                    Numbers you opt out of outbound SMS will appear here.
                                                </p>
                                            </div>
                                        </td>
                                    </tr>
                                ) : suppressions.map((row) => (
                                    <tr key={row.id} className="border-b transition-colors last:border-0 hover:bg-muted/40">
                                        <td className="px-4 py-3 font-mono">{row.phone_masked}</td>
                                        <td className="px-4 py-3">
                                            <Badge variant="secondary" className="font-normal capitalize">
                                                {row.source}
                                            </Badge>
                                        </td>
                                        <td className="px-4 py-3 text-muted-foreground">{row.reason || "—"}</td>
                                        <td className="whitespace-nowrap px-4 py-3 text-muted-foreground">
                                            {new Date(row.created_at).toLocaleString()}
                                        </td>
                                        <td className="px-4 py-3 text-right">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="gap-1.5"
                                                onClick={() => handleReleaseSuppression(row.id)}
                                            >
                                                <Unlock className="h-3.5 w-3.5" />
                                                Release
                                            </Button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </CardContent>
            </Card>

            {/* Send SMS Dialog */}
            <Dialog open={smsOpen} onOpenChange={setSmsOpen}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Send SMS</DialogTitle>
                        <DialogDescription>
                            Send an SMS from{" "}
                            <span className="font-mono font-medium">{selectedFrom}</span>
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <div className="space-y-2">
                            <Label htmlFor="sms-location">Location</Label>
                            <UiSelect
                                id="sms-location"
                                value={selectedLocationId}
                                onChange={(e) => setSelectedLocationId(e.target.value)}
                            >
                                <option value="">Select a location</option>
                                {smsLocations
                                    .filter((loc) => !selectedFrom || loc.twilio_from_number === selectedFrom)
                                    .map((loc) => (
                                        <option key={loc.id} value={loc.id}>
                                            {loc.institution_name} — {loc.location_name}
                                        </option>
                                    ))}
                            </UiSelect>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="to-number">To (E.164 format)</Label>
                            <Input
                                id="to-number"
                                placeholder="+12125551234"
                                value={toNumber}
                                onChange={(e) => setToNumber(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="sms-body">Message</Label>
                            <Textarea
                                id="sms-body"
                                placeholder="Type your message..."
                                rows={4}
                                maxLength={1600}
                                value={smsBody}
                                onChange={(e) => setSmsBody(e.target.value)}
                            />
                            <p className="text-xs text-muted-foreground text-right">
                                {smsBody.length} / 1600
                            </p>
                        </div>
                        <Button
                            className="w-full gap-2"
                            onClick={handleSendSms}
                            disabled={sending || !selectedLocationId || !toNumber.trim() || !smsBody.trim()}
                        >
                            {sending ? (
                                <RefreshCw className="h-4 w-4 animate-spin" />
                            ) : (
                                <CheckCircle2 className="h-4 w-4" />
                            )}
                            {sending ? "Sending..." : "Send Message"}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
