import { useEffect, useState, useCallback } from "react"
import { Loader2, MailPlus, RefreshCcw, Users } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
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
    updateInstitutionUserLocations,
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
    const [inviteExtraLocationSlugs, setInviteExtraLocationSlugs] = useState<string[]>([])
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [users, setUsers] = useState<InstitutionUserRow[]>([])
    const [editingUser, setEditingUser] = useState<InstitutionUserRow | null>(null)
    const [editorPrimarySlug, setEditorPrimarySlug] = useState("")
    const [editorExtraSlugs, setEditorExtraSlugs] = useState<string[]>([])
    const [savingLocations, setSavingLocations] = useState(false)
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
            const extraSlugs = inviteExtraLocationSlugs.filter((s) => s !== inviteLocationSlug)
            await inviteInstitutionUser({
                email: inviteEmail.trim(),
                role: inviteRole,
                location_slug: inviteRole !== "INSTITUTION_ADMIN" ? inviteLocationSlug : undefined,
                location_slugs:
                    inviteRole !== "INSTITUTION_ADMIN" && extraSlugs.length > 0
                        ? [inviteLocationSlug, ...extraSlugs]
                        : undefined,
            })
            toast.success("Invite sent")
            inviteCooldown.start()
            setInviteEmail("")
            setInviteExtraLocationSlugs([])
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

    function openLocationsEditor(target: InstitutionUserRow) {
        const slugById = new Map(locations.map((l) => [l.id, l.slug]))
        const assigned = (target.location_ids ?? (target.location_id ? [target.location_id] : []))
            .map((id) => slugById.get(id))
            .filter((slug): slug is string => Boolean(slug))
        setEditorPrimarySlug(assigned[0] ?? "")
        setEditorExtraSlugs(assigned.slice(1))
        setEditingUser(target)
    }

    async function handleSaveLocations() {
        if (!editingUser || !editorPrimarySlug) return
        setSavingLocations(true)
        try {
            const extras = editorExtraSlugs.filter((s) => s !== editorPrimarySlug)
            await updateInstitutionUserLocations(editingUser.id, [editorPrimarySlug, ...extras])
            toast.success("Locations updated")
            setEditingUser(null)
            setUsers(await listInstitutionUsers())
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to update locations")
        } finally {
            setSavingLocations(false)
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
            <PageHeader
                icon={Users}
                title="Institution User Management"
                description="Invite institution admins and location admins, and manage account status."
                actions={
                    <Button variant="outline" onClick={loadData} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                        Refresh
                    </Button>
                }
            />

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

                    {inviteRole !== "INSTITUTION_ADMIN" && locations.length > 1 && (
                        <div>
                            <Label className="text-xs text-muted-foreground">
                                Additional locations (optional — lets one account work across offices)
                            </Label>
                            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-2">
                                {locations
                                    .filter((location) => location.slug !== inviteLocationSlug)
                                    .map((location) => (
                                        <label
                                            key={location.id}
                                            className="flex items-center gap-2 text-sm"
                                        >
                                            <Checkbox
                                                checked={inviteExtraLocationSlugs.includes(location.slug)}
                                                onCheckedChange={(checked) =>
                                                    setInviteExtraLocationSlugs((prev) =>
                                                        checked
                                                            ? [...prev, location.slug]
                                                            : prev.filter((s) => s !== location.slug)
                                                    )
                                                }
                                            />
                                            {location.name}
                                        </label>
                                    ))}
                            </div>
                        </div>
                    )}

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
                                        <TableCell>
                                            {row.location_names?.length
                                                ? row.location_names.join(", ")
                                                : row.location_name || "All Locations"}
                                        </TableCell>
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
                                                {(row.role === "LOCATION_ADMIN" || row.role === "STAFF") && (
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        disabled={busy}
                                                        onClick={() => openLocationsEditor(row)}
                                                    >
                                                        Locations
                                                    </Button>
                                                )}
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

            <Dialog open={editingUser !== null} onOpenChange={(open) => { if (!open) setEditingUser(null) }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Assigned locations</DialogTitle>
                        <DialogDescription>
                            {editingUser?.email} — pick a primary location and any additional
                            locations this account may work in. Multi-location users choose
                            their active location from the sidebar after signing in.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div>
                            <Label>Primary location</Label>
                            <Select value={editorPrimarySlug || undefined} onValueChange={setEditorPrimarySlug}>
                                <SelectTrigger>
                                    <SelectValue placeholder="Select primary location" />
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
                        {locations.length > 1 && (
                            <div>
                                <Label className="text-xs text-muted-foreground">Additional locations</Label>
                                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-2">
                                    {locations
                                        .filter((location) => location.slug !== editorPrimarySlug)
                                        .map((location) => (
                                            <label key={location.id} className="flex items-center gap-2 text-sm">
                                                <Checkbox
                                                    checked={editorExtraSlugs.includes(location.slug)}
                                                    onCheckedChange={(checked) =>
                                                        setEditorExtraSlugs((prev) =>
                                                            checked
                                                                ? [...prev, location.slug]
                                                                : prev.filter((s) => s !== location.slug)
                                                        )
                                                    }
                                                />
                                                {location.name}
                                            </label>
                                        ))}
                                </div>
                            </div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setEditingUser(null)} disabled={savingLocations}>
                            Cancel
                        </Button>
                        <Button onClick={handleSaveLocations} disabled={savingLocations || !editorPrimarySlug}>
                            {savingLocations ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            Save
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
