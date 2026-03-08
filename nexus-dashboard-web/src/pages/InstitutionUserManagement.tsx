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
    deactivateInstitutionUser,
    listInstitutionPortalLocations,
    listInstitutionUsers,
    reinviteInstitutionUser,
    inviteInstitutionUser,
    type InstitutionPortalLocation,
    type InstitutionUserRow,
} from "@/lib/institution-portal-api"
import { formatRoleLabel } from "@/lib/utils"

type InstitutionInviteRole = "INSTITUTION_ADMIN" | "LOCATION_ADMIN"

export default function InstitutionUserManagement() {
    const { user } = useAuth()
    const [loading, setLoading] = useState(true)
    const [invitingUser, setInvitingUser] = useState(false)
    const [actingUserId, setActingUserId] = useState<string | null>(null)
    const [inviteEmail, setInviteEmail] = useState("")
    const [inviteRole, setInviteRole] = useState<InstitutionInviteRole>("LOCATION_ADMIN")
    const [inviteLocationSlug, setInviteLocationSlug] = useState("")
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [users, setUsers] = useState<InstitutionUserRow[]>([])

    async function loadData() {
        setLoading(true)
        try {
            const [locationRows, userRows] = await Promise.all([
                listInstitutionPortalLocations(),
                listInstitutionUsers(),
            ])
            setLocations(locationRows)
            setUsers(userRows)
            if (!inviteLocationSlug && locationRows.length > 0) {
                setInviteLocationSlug(locationRows[0].slug)
            }
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to load user management")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        void loadData()
    }, [])

    async function handleInviteUser() {
        if (!inviteEmail.trim()) return
        if (inviteRole === "LOCATION_ADMIN" && !inviteLocationSlug) {
            toast.error("Select a location for location admin invite")
            return
        }

        setInvitingUser(true)
        try {
            await inviteInstitutionUser({
                email: inviteEmail.trim(),
                role: inviteRole,
                location_slug: inviteRole === "LOCATION_ADMIN" ? inviteLocationSlug : undefined,
            })
            toast.success("Invite sent")
            setInviteEmail("")
            setUsers(await listInstitutionUsers())
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to invite user")
        } finally {
            setInvitingUser(false)
        }
    }

    async function handleDeactivateUser(target: InstitutionUserRow) {
        if (!window.confirm(`Deactivate ${target.email}?`)) return
        setActingUserId(target.id)
        try {
            await deactivateInstitutionUser(target.id)
            toast.success("User deactivated")
            setUsers(await listInstitutionUsers())
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to deactivate user")
        } finally {
            setActingUserId(null)
        }
    }

    async function handleReinviteUser(target: InstitutionUserRow) {
        if (!window.confirm(`Reinvite ${target.email}? This replaces their auth user.`)) return
        setActingUserId(target.id)
        try {
            await reinviteInstitutionUser(target.id)
            toast.success("Reinvite sent")
            setUsers(await listInstitutionUsers())
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to reinvite user")
        } finally {
            setActingUserId(null)
        }
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Institution User Management</h1>
                    <p className="mt-1 text-muted-foreground">
                        Invite institution admins and location admins, and manage account status.
                    </p>
                </div>
                <Button variant="outline" onClick={loadData} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                    Refresh
                </Button>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Users className="h-4 w-4" />
                        Users
                    </CardTitle>
                    <CardDescription>
                        Institution admins have institution-wide access. Location admins are restricted to one location.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-4">
                        <div className="md:col-span-2">
                            <Label htmlFor="invite-user-email">Email</Label>
                            <Input
                                id="invite-user-email"
                                type="email"
                                placeholder="user@institution.com"
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                            />
                        </div>
                        <div>
                            <Label>Role</Label>
                            <Select
                                value={inviteRole}
                                onValueChange={(value) => setInviteRole(value as InstitutionInviteRole)}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="INSTITUTION_ADMIN">Institution Admin</SelectItem>
                                    <SelectItem value="LOCATION_ADMIN">Location Admin</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <Label>Location</Label>
                            <Select
                                value={inviteLocationSlug || undefined}
                                onValueChange={setInviteLocationSlug}
                                disabled={inviteRole === "INSTITUTION_ADMIN"}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Required for location roles" />
                                </SelectTrigger>
                                <SelectContent>
                                    {locations.map((location) => (
                                        <SelectItem key={location.id} value={location.slug}>
                                            {location.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <Button onClick={handleInviteUser} disabled={invitingUser || !inviteEmail.trim() || loading}>
                        {invitingUser ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MailPlus className="mr-2 h-4 w-4" />}
                        Send Invite
                    </Button>

                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Email</TableHead>
                                <TableHead>Role</TableHead>
                                <TableHead>Location</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {users.map((row) => {
                                const isSelf = row.id === user?.id
                                const busy = actingUserId === row.id
                                return (
                                    <TableRow key={row.id}>
                                        <TableCell className="font-medium">{row.email}</TableCell>
                                        <TableCell>{formatRoleLabel(row.role)}</TableCell>
                                        <TableCell>{row.location_name || "All Locations"}</TableCell>
                                        <TableCell>{row.is_active ? "Active" : "Inactive"}</TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-2">
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    disabled={busy || isSelf || !row.is_active}
                                                    onClick={() => handleDeactivateUser(row)}
                                                >
                                                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Deactivate"}
                                                </Button>
                                                <Button
                                                    variant="secondary"
                                                    size="sm"
                                                    disabled={busy || isSelf}
                                                    onClick={() => handleReinviteUser(row)}
                                                >
                                                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Reinvite"}
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                )
                            })}
                            {!users.length && !loading && (
                                <TableRow>
                                    <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                                        No institution users found.
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
