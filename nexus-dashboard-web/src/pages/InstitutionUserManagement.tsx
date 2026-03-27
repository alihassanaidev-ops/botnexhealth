import { useEffect, useState, useCallback } from "react"
import { Loader2, MailPlus, RefreshCcw, Users } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useAuth } from "@/context/AuthContext"
import { useCooldown, useCooldownMap } from "@/hooks/use-cooldown"
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

type InstitutionInviteRole = "INSTITUTION_ADMIN" | "LOCATION_ADMIN" | "STAFF"

export default function InstitutionUserManagement() {
    const INVITE_COOLDOWN_SECONDS = 30
    const { user } = useAuth()
    const [loading, setLoading] = useState(true)
    const [invitingUser, setInvitingUser] = useState(false)
    const [actingUserId, setActingUserId] = useState<string | null>(null)
    const [inviteEmail, setInviteEmail] = useState("")
    const [inviteRole, setInviteRole] = useState<InstitutionInviteRole>("LOCATION_ADMIN")
    const [inviteLocationSlug, setInviteLocationSlug] = useState("")
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [users, setUsers] = useState<InstitutionUserRow[]>([])
    const inviteCooldown = useCooldown(INVITE_COOLDOWN_SECONDS)
    const reinviteCooldowns = useCooldownMap(INVITE_COOLDOWN_SECONDS)

    const loadData = useCallback(async () => {
        setLoading(true)
        try {
            const [locationRows, userRows] = await Promise.all([
                listInstitutionPortalLocations(),
                listInstitutionUsers(),
            ])
            setLocations(locationRows)
            setUsers(userRows)
            setInviteLocationSlug(prev => prev || (locationRows.length > 0 ? locationRows[0].slug : ""))
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load user management")
        } finally {
            setLoading(false)
        }
    }, []);

    useEffect(() => {
        void loadData()
    }, [loadData])

    async function handleInviteUser() {
        if (!inviteEmail.trim()) return
        if (inviteCooldown.isActive) return
        if ((inviteRole === "LOCATION_ADMIN" || inviteRole === "STAFF") && !inviteLocationSlug) {
            toast.error("Select a location for this role")
            return
        }

        setInvitingUser(true)
        try {
            await inviteInstitutionUser({
                email: inviteEmail.trim(),
                role: inviteRole,
                location_slug: inviteRole !== "INSTITUTION_ADMIN" ? inviteLocationSlug : undefined,
            })
            toast.success("Invite sent")
            inviteCooldown.start()
            setInviteEmail("")
            setUsers(await listInstitutionUsers())
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
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
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to deactivate user")
        } finally {
            setActingUserId(null)
        }
    }

    async function handleReinviteUser(target: InstitutionUserRow) {
        if (reinviteCooldowns.isActive(target.id)) return
        if (!window.confirm(`Reinvite ${target.email}? This replaces their auth user.`)) return
        setActingUserId(target.id)
        try {
            await reinviteInstitutionUser(target.id)
            toast.success("Reinvite sent")
            reinviteCooldowns.start(target.id)
            setUsers(await listInstitutionUsers())
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to reinvite user")
        } finally {
            setActingUserId(null)
        }
    }

    return (
        <div className="relative space-y-6 bg-background">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
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
                        Institution admins have institution-wide access. Location admins and staff are assigned to a specific location.
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
                                    <SelectItem value="STAFF">Staff</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <Label>Location</Label>
                            <Select
                                value={inviteLocationSlug || undefined}
                                onValueChange={setInviteLocationSlug}
                                disabled={inviteRole === "INSTITUTION_ADMIN"}
                                required={inviteRole !== "INSTITUTION_ADMIN"}
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

                    <Button
                        onClick={handleInviteUser}
                        disabled={invitingUser || inviteCooldown.isActive || !inviteEmail.trim() || loading}
                    >
                        {invitingUser ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MailPlus className="mr-2 h-4 w-4" />}
                        {invitingUser
                            ? "Sending..."
                            : inviteCooldown.isActive
                                ? `Send Invite (${inviteCooldown.remaining}s)`
                                : "Send Invite"}
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
                                const reinviteRemaining = reinviteCooldowns.getRemaining(row.id)
                                return (
                                    <TableRow key={row.id}>
                                        <TableCell className="font-medium">{row.email}</TableCell>
                                        <TableCell>{formatRoleLabel(row.role)}</TableCell>
                                        <TableCell>{row.location_name || "All Locations"}</TableCell>
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
                                                    disabled={busy || isSelf || reinviteRemaining > 0}
                                                    onClick={() => handleReinviteUser(row)}
                                                >
                                                    {busy
                                                        ? <Loader2 className="h-4 w-4 animate-spin" />
                                                        : reinviteRemaining > 0
                                                            ? `Reinvite (${reinviteRemaining}s)`
                                                            : "Reinvite"}
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
