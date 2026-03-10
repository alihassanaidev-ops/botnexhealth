import { useEffect, useState } from "react"
import { Loader2, MailPlus, RefreshCcw, Users } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useAuth } from "@/context/AuthContext"
import {
    deactivateLocationUser,
    inviteStaff,
    listInstitutionPortalLocations,
    listLocationUsers,
    updateLocationTimezone,
    type InstitutionPortalLocation,
    type InstitutionUserRow,
} from "@/lib/institution-portal-api"
import { SUPPORTED_TIMEZONES } from "@/lib/timezones"

export default function LocationAdminPanel() {
    const { user } = useAuth()
    const [loading, setLoading] = useState(true)
    const [inviting, setInviting] = useState(false)
    const [savingTimezone, setSavingTimezone] = useState(false)
    const [location, setLocation] = useState<InstitutionPortalLocation | null>(null)
    const [email, setEmail] = useState("")
    const [timezone, setTimezone] = useState("UTC")
    const [staffUsers, setStaffUsers] = useState<InstitutionUserRow[]>([])
    const [actingUserId, setActingUserId] = useState<string | null>(null)

    async function loadData() {
        setLoading(true)
        try {
            const [locations, users] = await Promise.all([
                listInstitutionPortalLocations(),
                listLocationUsers(),
            ])
            const assigned = locations[0] ?? null
            setLocation(assigned)
            setTimezone(assigned?.timezone || "UTC")
            setStaffUsers(users)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load location")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        void loadData()
    }, [])

    async function handleInvite() {
        if (!location || !email.trim()) return
        setInviting(true)
        try {
            await inviteStaff(location.slug, email.trim())
            toast.success(`Staff invite sent to ${email.trim()}`)
            setEmail("")
            setStaffUsers(await listLocationUsers())
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
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
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to update timezone")
        } finally {
            setSavingTimezone(false)
        }
    }

    async function handleDeactivateUser(target: InstitutionUserRow) {
        if (!window.confirm(`Deactivate ${target.email}?`)) return
        setActingUserId(target.id)
        try {
            await deactivateLocationUser(target.id)
            toast.success("Staff user deactivated")
            setStaffUsers(await listLocationUsers())
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to deactivate user")
        } finally {
            setActingUserId(null)
        }
    }

    return (
        <div className="space-y-6 bg-gradient-to-b from-background via-background to-accent/20">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Location Admin</h1>
                    <p className="mt-1 text-muted-foreground">
                        Manage staff and settings for your assigned location.
                    </p>
                </div>
                <Button variant="outline" onClick={loadData} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                    Refresh
                </Button>
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
                        Staff Management
                    </CardTitle>
                    <CardDescription>
                        Invite and manage staff users for your location. Staff have location-scoped read access.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex items-end gap-3">
                        <div className="flex-1 space-y-2">
                            <Label htmlFor="staff-email">Invite Staff</Label>
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
                    </div>

                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Email</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {staffUsers.map((row) => {
                                const isSelf = row.id === user?.id
                                const busy = actingUserId === row.id
                                return (
                                    <TableRow key={row.id}>
                                        <TableCell className="font-medium">{row.email}</TableCell>
                                        <TableCell>
                                            {row.invite_status === "PENDING" ? (
                                                <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-yellow-50 text-yellow-700 ring-yellow-600/20 dark:bg-yellow-900/20 dark:text-yellow-400 dark:ring-yellow-900/10">
                                                    Pending
                                                </span>
                                            ) : row.is_active ? (
                                                <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-green-50 text-green-700 ring-green-600/20 dark:bg-green-900/20 dark:text-green-400 dark:ring-green-900/10">
                                                    Active
                                                </span>
                                            ) : (
                                                <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-gray-50 text-gray-600 ring-gray-500/10 dark:bg-gray-900/20 dark:text-gray-400 dark:ring-gray-700/10">
                                                    Inactive
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                disabled={busy || isSelf || !row.is_active}
                                                onClick={() => handleDeactivateUser(row)}
                                            >
                                                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Deactivate"}
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                )
                            })}
                            {!staffUsers.length && !loading && (
                                <TableRow>
                                    <TableCell colSpan={3} className="py-10 text-center text-muted-foreground">
                                        No staff users yet. Invite your first staff member above.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    )
}
