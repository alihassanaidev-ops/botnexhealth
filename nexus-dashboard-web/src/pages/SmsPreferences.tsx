import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, CalendarCheck, Loader2, MessageSquare, PhoneCall, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { FormSkeleton } from "@/components/ui/skeletons"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"
import { listLocations } from "@/lib/tenant-api"
import type { LocationInfo } from "@/types"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    addSmsNotificationRecipient,
    deleteSmsNotificationRecipient,
    listSmsNotificationRecipients,
    updateSmsNotificationRecipient,
    type SmsNotificationRecipient,
} from "@/lib/sms-notification-settings-api"

const APPOINTMENT_REQUEST = "appointment_request"

/** Mirrors the three switches on Email Preferences. Each is a separate
 *  recipient row keyed by (phone, notification_type), so a number receives
 *  only the alert types it is subscribed to. */
const ALERT_TYPES = [
    {
        type: APPOINTMENT_REQUEST,
        label: "Appointment Request",
        description: "Text when the AI captures an appointment request to book manually.",
        icon: CalendarCheck,
        defaultOn: true,
    },
    {
        type: "call_summary",
        label: "Call Summary",
        description: "Text a short summary after every call handled by the AI agent.",
        icon: PhoneCall,
        defaultOn: false,
    },
    {
        type: "urgent_alert",
        label: "Urgent Call Alert",
        description: "Text immediately when a call is flagged as urgent or a complaint.",
        icon: AlertTriangle,
        defaultOn: false,
    },
] as const

function apiErrorMessage(error: unknown, fallback: string): string {
    return (
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || fallback
    )
}

function validPhoneInput(phone: string): boolean {
    const digits = phone.replace(/\D/g, "")
    return digits.length >= 10
}

const ALL_LOCATIONS = "__all__"

export default function SmsPreferences() {
    const { isLoading: institutionLoading } = useInstitution()
    const { user } = useAuth()
    // Location admins can only add for their own site, so the picker is
    // institution-admin only — the API forces their scope regardless.
    const canChooseLocation = user?.role === "INSTITUTION_ADMIN"
    const [locations, setLocations] = useState<LocationInfo[]>([])
    const [locationId, setLocationId] = useState<string>(ALL_LOCATIONS)
    const [recipients, setRecipients] = useState<SmsNotificationRecipient[]>([])
    const [loading, setLoading] = useState(true)
    const [phoneNumber, setPhoneNumber] = useState("")
    const [saving, setSaving] = useState(false)

    const loadRecipients = useCallback(async () => {
        setLoading(true)
        try {
            setRecipients(await listSmsNotificationRecipients())
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to load SMS recipients"))
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        if (!institutionLoading) {
            void loadRecipients()
        }
    }, [institutionLoading, loadRecipients])

    useEffect(() => {
        if (!canChooseLocation) return
        listLocations()
            .then(setLocations)
            .catch(() => setLocations([]))
    }, [canChooseLocation])

    async function handleAddRecipient() {
        const nextPhone = phoneNumber.trim()
        if (!validPhoneInput(nextPhone)) {
            toast.error("Enter a valid phone number")
            return
        }

        setSaving(true)
        try {
            // Seed one row per alert type up front: the API only ever returns
            // the masked number, so a later toggle has no full number to POST.
            // Appointment Request starts on to match previous behaviour; the
            // other two are opt-in so adding a number can't silently start
            // texting on every call.
            for (const alert of ALERT_TYPES) {
                const all = await addSmsNotificationRecipient({
                    phone_number: nextPhone,
                    notification_type: alert.type,
                    location_id: locationId === ALL_LOCATIONS ? null : locationId,
                })
                const created = all.find(
                    (r) => r.notification_type === alert.type
                        && r.phone_number_masked.slice(-4) === nextPhone.replace(/\D/g, "").slice(-4),
                )
                if (created && !alert.defaultOn && created.is_active) {
                    await updateSmsNotificationRecipient(created.id, { is_active: false })
                }
            }
            setPhoneNumber("")
            await loadRecipients()
            toast.success("SMS recipient added")
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to add SMS recipient"))
        } finally {
            setSaving(false)
        }
    }

    async function handleToggleRecipient(id: string, currentActive: boolean) {
        try {
            await updateSmsNotificationRecipient(id, { is_active: !currentActive })
            setRecipients((prev) =>
                prev.map((recipient) =>
                    recipient.id === id
                        ? { ...recipient, is_active: !currentActive }
                        : recipient,
                ),
            )
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to update SMS recipient"))
        }
    }

    /** Removing a number drops every alert-type row we created for it. */
    async function handleDeleteNumber(ids: string[]) {
        try {
            for (const id of ids) {
                await deleteSmsNotificationRecipient(id)
            }
            setRecipients((prev) => prev.filter((recipient) => !ids.includes(recipient.id)))
            toast.success("SMS recipient removed")
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to remove SMS recipient"))
        }
    }

    /** One row per (number, type) — group them so each number renders once. */
    const groupedRecipients = recipients.reduce<
        { key: string; masked: string; locationId: string | null; rows: SmsNotificationRecipient[] }[]
    >((groups, recipient) => {
        // Key on number AND location: the same number can be subscribed
        // separately for two locations and must not collapse into one card.
        const locationId = recipient.location_id ?? null
        const key = `${recipient.phone_number_masked}|${locationId ?? "all"}`
        const existing = groups.find((g) => g.key === key)
        if (existing) existing.rows.push(recipient)
        else groups.push({ key, masked: recipient.phone_number_masked, locationId, rows: [recipient] })
        return groups
    }, [])

    if (institutionLoading || loading) {
        return <FormSkeleton rows={5} />
    }

    return (
        <div className="p-6 max-w-3xl mx-auto space-y-6">
            <PageHeader
                icon={MessageSquare}
                title="SMS Preferences"
                description="Choose which automated alerts each phone number receives."
            />

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Automated SMS Recipients</CardTitle>
                    <CardDescription>
                        Add a number, then pick the alerts it should receive. Messages carry
                        triage details and a dashboard link only — never patient names or DOB.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-3 sm:flex-row">
                        <Input
                            type="tel"
                            placeholder="+1 555 123 4567"
                            value={phoneNumber}
                            onChange={(event) => setPhoneNumber(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                    event.preventDefault()
                                    void handleAddRecipient()
                                }
                            }}
                            className="h-9"
                        />
                        {canChooseLocation && (
                            <Select value={locationId} onValueChange={setLocationId}>
                                <SelectTrigger className="h-9 sm:w-56">
                                    <SelectValue placeholder="All locations" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ALL_LOCATIONS}>All locations</SelectItem>
                                    {locations.map((loc) => (
                                        <SelectItem key={loc.id} value={loc.id}>
                                            {loc.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        )}
                        <Button
                            type="button"
                            onClick={handleAddRecipient}
                            disabled={saving || !phoneNumber.trim()}
                        >
                            {saving ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Plus className="mr-2 h-4 w-4" />
                            )}
                            Add
                        </Button>
                    </div>

                    {groupedRecipients.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                            No SMS recipients configured yet.
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {groupedRecipients.map((group) => (
                                <div key={group.key} className="rounded-lg border border-border">
                                    <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                                        <div className="flex items-center gap-2">
                                            <span className="font-mono text-sm font-medium tabular-nums">
                                                {group.masked}
                                            </span>
                                            <span className="text-[11px] text-muted-foreground">
                                                {group.locationId
                                                    ? locations.find((l) => l.id === group.locationId)?.name
                                                      ?? "This location"
                                                    : "All locations"}
                                            </span>
                                        </div>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            onClick={() =>
                                                handleDeleteNumber(group.rows.map((r) => r.id))
                                            }
                                        >
                                            <Trash2 className="h-4 w-4 text-muted-foreground hover:text-red-500" />
                                            <span className="sr-only">Remove SMS recipient</span>
                                        </Button>
                                    </div>
                                    <div className="divide-y divide-border">
                                        {ALERT_TYPES.map((alert) => {
                                            const row = group.rows.find(
                                                (r) => r.notification_type === alert.type,
                                            )
                                            const Icon = alert.icon
                                            return (
                                                <div
                                                    key={alert.type}
                                                    className="flex items-start gap-3 px-4 py-3"
                                                >
                                                    <span className="mt-0.5 rounded-md bg-muted p-1.5">
                                                        <Icon className="h-4 w-4 text-muted-foreground" />
                                                    </span>
                                                    <div className="min-w-0 flex-1">
                                                        <p className="text-sm font-medium">{alert.label}</p>
                                                        <p className="text-xs leading-relaxed text-muted-foreground">
                                                            {alert.description}
                                                        </p>
                                                    </div>
                                                    <Switch
                                                        className="mt-0.5"
                                                        checked={row?.is_active ?? false}
                                                        disabled={!row}
                                                        onCheckedChange={() => {
                                                            if (row) {
                                                                void handleToggleRecipient(
                                                                    row.id,
                                                                    row.is_active,
                                                                )
                                                            }
                                                        }}
                                                    />
                                                </div>
                                            )
                                        })}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                </CardContent>
            </Card>
        </div>
    )
}
