import { useEffect, useMemo, useState } from "react"
import { Building2, Loader2, MailPlus, MapPin, Settings2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { OperatingHoursEntry } from "@/types"
import {
    getInstitutionPortalMe,
    getLocationOperatingHours,
    inviteInstitutionAdmin,
    inviteLocationAdmin,
    listInstitutionPortalLocations,
    updateLocationOperatingHours,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"

const DAYS = [
    { value: 0, label: "Monday" },
    { value: 1, label: "Tuesday" },
    { value: 2, label: "Wednesday" },
    { value: 3, label: "Thursday" },
    { value: 4, label: "Friday" },
    { value: 5, label: "Saturday" },
    { value: 6, label: "Sunday" },
]

function defaultHours(): OperatingHoursEntry[] {
    return DAYS.map((day) => ({
        day_of_week: day.value,
        is_open: day.value >= 0 && day.value <= 4,
        open_time: day.value >= 0 && day.value <= 4 ? "09:00" : null,
        close_time: day.value >= 0 && day.value <= 4 ? "17:00" : null,
    }))
}

interface HoursDialogProps {
    location: InstitutionPortalLocation | null
    onClose: () => void
}

function HoursDialog({ location, onClose }: HoursDialogProps) {
    const [loading, setLoading] = useState(false)
    const [saving, setSaving] = useState(false)
    const [hours, setHours] = useState<OperatingHoursEntry[]>(defaultHours)

    useEffect(() => {
        if (!location) return
        setLoading(true)
        getLocationOperatingHours(location.slug)
            .then((rows) => {
                if (!rows.length) {
                    setHours(defaultHours())
                    return
                }
                setHours(
                    DAYS.map((day) => {
                        const found = rows.find((h) => h.day_of_week === day.value)
                        if (!found) {
                            return {
                                day_of_week: day.value,
                                is_open: false,
                                open_time: null,
                                close_time: null,
                            }
                        }
                        return {
                            day_of_week: found.day_of_week,
                            is_open: found.is_open,
                            open_time: found.open_time,
                            close_time: found.close_time,
                        }
                    }),
                )
            })
            .catch((error) => {
                toast.error(error?.response?.data?.detail || "Failed to load operating hours")
            })
            .finally(() => setLoading(false))
    }, [location])

    function setHour(day: number, patch: Partial<OperatingHoursEntry>) {
        setHours((prev) => prev.map((h) => (h.day_of_week === day ? { ...h, ...patch } : h)))
    }

    async function saveHours() {
        if (!location) return
        setSaving(true)
        try {
            await updateLocationOperatingHours(
                location.slug,
                hours.map((h) => ({
                    ...h,
                    open_time: h.is_open ? h.open_time : null,
                    close_time: h.is_open ? h.close_time : null,
                })),
            )
            toast.success("Operating hours saved")
            onClose()
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to save operating hours")
        } finally {
            setSaving(false)
        }
    }

    if (!location) return null

    return (
        <Dialog open={!!location} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Operating Hours: {location.name}</DialogTitle>
                </DialogHeader>
                {loading ? (
                    <div className="flex items-center justify-center py-12">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="space-y-3">
                        {hours.map((hour) => (
                            <div key={hour.day_of_week} className="flex items-center gap-3 rounded-md border p-3">
                                <div className="flex w-40 items-center gap-2">
                                    <Switch
                                        checked={hour.is_open}
                                        onCheckedChange={(value) => setHour(hour.day_of_week, { is_open: value })}
                                    />
                                    <span className="text-sm font-medium">
                                        {DAYS.find((d) => d.value === hour.day_of_week)?.label}
                                    </span>
                                </div>
                                <Input
                                    type="time"
                                    className="w-36"
                                    disabled={!hour.is_open}
                                    value={hour.open_time || "09:00"}
                                    onChange={(e) => setHour(hour.day_of_week, { open_time: e.target.value })}
                                />
                                <span className="text-muted-foreground">to</span>
                                <Input
                                    type="time"
                                    className="w-36"
                                    disabled={!hour.is_open}
                                    value={hour.close_time || "17:00"}
                                    onChange={(e) => setHour(hour.day_of_week, { close_time: e.target.value })}
                                />
                                {!hour.is_open && (
                                    <span className="text-xs text-muted-foreground">Closed</span>
                                )}
                            </div>
                        ))}
                        <div className="flex justify-end pt-2">
                            <Button onClick={saveHours} disabled={saving}>
                                {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Save Hours
                            </Button>
                        </div>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    )
}

export default function InstitutionAdminPanel() {
    const [loading, setLoading] = useState(true)
    const [institutionName, setInstitutionName] = useState("")
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [selectedLocation, setSelectedLocation] = useState<InstitutionPortalLocation | null>(null)

    const [institutionAdminEmail, setInstitutionAdminEmail] = useState("")
    const [invitingInstitutionAdmin, setInvitingInstitutionAdmin] = useState(false)

    const [locationAdminEmail, setLocationAdminEmail] = useState("")
    const [locationSlug, setLocationSlug] = useState("")
    const [invitingLocationAdmin, setInvitingLocationAdmin] = useState(false)

    const activeLocations = useMemo(
        () => locations.filter((location) => location.is_active),
        [locations],
    )

    async function loadData() {
        setLoading(true)
        try {
            const [me, locationRows] = await Promise.all([
                getInstitutionPortalMe(),
                listInstitutionPortalLocations(),
            ])
            setInstitutionName(me.name)
            setLocations(locationRows)
            if (!locationSlug && locationRows.length) {
                setLocationSlug(locationRows[0].slug)
            }
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to load institution admin panel")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        void loadData()
    }, [])

    async function handleInviteInstitutionAdmin() {
        if (!institutionAdminEmail.trim()) return
        setInvitingInstitutionAdmin(true)
        try {
            await inviteInstitutionAdmin(institutionAdminEmail.trim())
            toast.success("Institution admin invite sent")
            setInstitutionAdminEmail("")
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to invite institution admin")
        } finally {
            setInvitingInstitutionAdmin(false)
        }
    }

    async function handleInviteLocationAdmin() {
        if (!locationSlug || !locationAdminEmail.trim()) return
        setInvitingLocationAdmin(true)
        try {
            await inviteLocationAdmin(locationSlug, locationAdminEmail.trim())
            toast.success("Location admin invite sent")
            setLocationAdminEmail("")
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to invite location admin")
        } finally {
            setInvitingLocationAdmin(false)
        }
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Institution Admin Panel</h1>
                    <p className="mt-1 text-muted-foreground">
                        Manage your locations and invite admin users for your institution.
                    </p>
                </div>
                <Button variant="outline" onClick={loadData} disabled={loading}>
                    {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Refresh
                </Button>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Institution</CardDescription>
                        <CardTitle className="text-lg">{institutionName || "—"}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        <Building2 className="mr-2 inline h-4 w-4" />
                        Centralized admin scope
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Total Locations</CardDescription>
                        <CardTitle className="text-3xl">{locations.length}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        Includes active and inactive
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Active Locations</CardDescription>
                        <CardTitle className="text-3xl">{activeLocations.length}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        <MapPin className="mr-2 inline h-4 w-4" />
                        Live operational locations
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>Invite Institution Admin</CardTitle>
                        <CardDescription>
                            Add another institution admin in your institution scope.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <Label htmlFor="institution-admin-email">Email</Label>
                        <Input
                            id="institution-admin-email"
                            type="email"
                            placeholder="admin@institution.com"
                            value={institutionAdminEmail}
                            onChange={(e) => setInstitutionAdminEmail(e.target.value)}
                        />
                        <Button
                            onClick={handleInviteInstitutionAdmin}
                            disabled={invitingInstitutionAdmin || !institutionAdminEmail.trim()}
                        >
                            {invitingInstitutionAdmin && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            <MailPlus className="mr-2 h-4 w-4" />
                            Send Invite
                        </Button>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Invite Location Admin</CardTitle>
                        <CardDescription>
                            Assign location admins to specific locations.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <Label>Location</Label>
                        <Select value={locationSlug} onValueChange={setLocationSlug}>
                            <SelectTrigger>
                                <SelectValue placeholder="Select location" />
                            </SelectTrigger>
                            <SelectContent>
                                {locations.map((location) => (
                                    <SelectItem key={location.id} value={location.slug}>
                                        {location.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Label htmlFor="location-admin-email">Email</Label>
                        <Input
                            id="location-admin-email"
                            type="email"
                            placeholder="location-admin@institution.com"
                            value={locationAdminEmail}
                            onChange={(e) => setLocationAdminEmail(e.target.value)}
                        />
                        <Button
                            onClick={handleInviteLocationAdmin}
                            disabled={invitingLocationAdmin || !locationSlug || !locationAdminEmail.trim()}
                        >
                            {invitingLocationAdmin && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            <MailPlus className="mr-2 h-4 w-4" />
                            Send Invite
                        </Button>
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Locations</CardTitle>
                    <CardDescription>
                        View all assigned locations and update working hours.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Location</TableHead>
                                <TableHead>Phone</TableHead>
                                <TableHead>Timezone</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {locations.map((location) => (
                                <TableRow key={location.id}>
                                    <TableCell className="font-medium">{location.name}</TableCell>
                                    <TableCell>{location.phone || "—"}</TableCell>
                                    <TableCell>{location.timezone || "—"}</TableCell>
                                    <TableCell>{location.is_active ? "Active" : "Inactive"}</TableCell>
                                    <TableCell className="text-right">
                                        <Button variant="outline" size="sm" onClick={() => setSelectedLocation(location)}>
                                            <Settings2 className="mr-2 h-4 w-4" />
                                            Edit Hours
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {!locations.length && (
                                <TableRow>
                                    <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                                        No locations found for this institution.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <HoursDialog location={selectedLocation} onClose={() => setSelectedLocation(null)} />
        </div>
    )
}
