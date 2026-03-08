import { useEffect, useState } from "react"
import { Loader2, MailPlus, Users } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
    inviteStaff,
    listInstitutionPortalLocations,
    updateLocationTimezone,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"
import { SUPPORTED_TIMEZONES } from "@/lib/timezones"

export default function LocationAdminPanel() {
    const [loading, setLoading] = useState(true)
    const [inviting, setInviting] = useState(false)
    const [savingTimezone, setSavingTimezone] = useState(false)
    const [location, setLocation] = useState<InstitutionPortalLocation | null>(null)
    const [email, setEmail] = useState("")
    const [timezone, setTimezone] = useState("UTC")

    useEffect(() => {
        async function loadLocation() {
            setLoading(true)
            try {
                const locations = await listInstitutionPortalLocations()
                const assigned = locations[0] ?? null
                setLocation(assigned)
                setTimezone(assigned?.timezone || "UTC")
            } catch (error: any) {
                toast.error(error?.response?.data?.detail || "Failed to load location")
            } finally {
                setLoading(false)
            }
        }

        void loadLocation()
    }, [])

    async function handleInvite() {
        if (!location || !email.trim()) return
        setInviting(true)
        try {
            await inviteStaff(location.slug, email.trim())
            toast.success(`Staff invite sent to ${email.trim()}`)
            setEmail("")
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to send invite")
        } finally {
            setInviting(false)
        }
    }

    async function handleSaveTimezone() {
        if (!location) return
        const currentTimezone = location.timezone || "UTC"
        if (timezone === currentTimezone) return

        setSavingTimezone(true)
        try {
            const updated = await updateLocationTimezone(location.slug, timezone)
            setLocation(updated)
            setTimezone(updated.timezone || "UTC")
            toast.success("Timezone updated")
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to update timezone")
        } finally {
            setSavingTimezone(false)
        }
    }

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Location Admin</h1>
                <p className="mt-1 text-muted-foreground">
                    Invite staff for your assigned location.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Assigned Location</CardTitle>
                    <CardDescription>
                        {loading ? "Loading location..." : location ? `${location.name} (${location.slug})` : "No location assigned"}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Fetching location details...
                        </div>
                    ) : null}
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>Location Timezone</CardTitle>
                    <CardDescription>
                        Set the timezone used for scheduling and local time calculations.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-2">
                        <Label>Timezone</Label>
                        <Select
                            value={timezone}
                            onValueChange={setTimezone}
                            disabled={!location || loading || savingTimezone}
                        >
                            <SelectTrigger>
                                <SelectValue placeholder="Select timezone" />
                            </SelectTrigger>
                            <SelectContent>
                                {SUPPORTED_TIMEZONES.map((tz) => (
                                    <SelectItem key={tz.value} value={tz.value}>
                                        {tz.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <Button
                        onClick={handleSaveTimezone}
                        disabled={!location || loading || savingTimezone || timezone === (location?.timezone || "UTC")}
                    >
                        {savingTimezone ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        Save Timezone
                    </Button>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Users className="h-4 w-4" />
                        Invite Staff
                    </CardTitle>
                    <CardDescription>
                        Staff users have location-scoped access and cannot invite other users.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="staff-email">Email</Label>
                        <Input
                            id="staff-email"
                            type="email"
                            placeholder="staff@clinic.com"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                    void handleInvite()
                                }
                            }}
                            disabled={!location || inviting}
                        />
                    </div>
                    <Button onClick={handleInvite} disabled={!location || inviting || !email.trim()}>
                        {inviting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MailPlus className="mr-2 h-4 w-4" />}
                        Send Invite
                    </Button>
                </CardContent>
            </Card>
        </div>
    )
}
