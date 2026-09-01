import { useState, useEffect, useCallback } from "react";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/foundation/compat/table";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/foundation/compat/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/foundation/compat/dialog";
import { Badge } from "@/components/foundation/compat/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/foundation/compat/card";
import { RefreshCw, ChevronLeft, ChevronRight, Trash2, MailPlus, UserCog, Loader2, Pencil, Plus } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/foundation/compat/button";
import { TableSkeleton } from "@/components/foundation/compat/skeletons";
import { Input } from "@/components/foundation/compat/input";
import { Label } from "@/components/foundation/compat/label";
import { toast } from "sonner";

import {
    inviteAdminUser,
    listAdminInstitutionLocations,
    listInstitutionsDetailed,
    listAdminUsers,
    removeAdminUser,
    reinviteAdminUser,
    updateAdminUser,
    type AdminManagedRole,
    type AdminUserRow,
    type AdminUserStatus,
} from "@/lib/admin-api";
import { useCooldownMap } from "@/hooks/use-cooldown";
import { formatRoleLabel } from "@/lib/utils";
import type { InstitutionDetail, Location } from "@/types";

const ROLE_OPTIONS = ["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"] as const;
const REINVITE_COOLDOWN_SECONDS = 30;
const PAGE_SIZE = 50;

function statusBadge(user: AdminUserRow) {
    if (user.deleted_at) {
        return <Badge variant="secondary" className="bg-muted text-muted-foreground">Removed</Badge>;
    }
    if (user.invite_status === "PENDING") {
        return <Badge variant="secondary" className="bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-900">Pending</Badge>;
    }
    if (!user.is_active) {
        return <Badge variant="secondary" className="bg-muted text-muted-foreground">Inactive</Badge>;
    }
    return <Badge variant="secondary" className="bg-primary/10 text-primary border border-border">Active</Badge>;
}

export default function AdminUserManagement() {
    const [users, setUsers] = useState<AdminUserRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);

    const [search, setSearch] = useState("");
    const [roleFilter, setRoleFilter] = useState<string>("ALL");
    const [statusFilter, setStatusFilter] = useState<AdminUserStatus>("active");

    const [removeTarget, setRemoveTarget] = useState<AdminUserRow | null>(null);
    const [isRemoving, setIsRemoving] = useState(false);
    const [editorOpen, setEditorOpen] = useState(false);
    const [editTarget, setEditTarget] = useState<AdminUserRow | null>(null);
    const [editorEmail, setEditorEmail] = useState("");
    const [editorRole, setEditorRole] = useState<AdminManagedRole>("INSTITUTION_ADMIN");
    const [editorInstitutionId, setEditorInstitutionId] = useState("");
    const [editorLocationId, setEditorLocationId] = useState("");
    const [institutions, setInstitutions] = useState<InstitutionDetail[]>([]);
    const [locations, setLocations] = useState<Location[]>([]);
    const [loadingInstitutions, setLoadingInstitutions] = useState(true);
    const [loadingLocations, setLoadingLocations] = useState(false);
    const [savingUser, setSavingUser] = useState(false);
    const reinviteCooldowns = useCooldownMap(REINVITE_COOLDOWN_SECONDS);

    const fetchUsers = useCallback(
        async (currentPage: number, q: string, role: string, status: AdminUserStatus) => {
            setLoading(true);
            try {
                const data = await listAdminUsers(
                    {
                        q: q || undefined,
                        role: role === "ALL" ? undefined : role,
                        status,
                    },
                    currentPage,
                    PAGE_SIZE
                );
                setUsers(data.items);
                setTotal(data.total);
                setPage(currentPage);
            } catch (err: unknown) {
                const error = err as { response?: { data?: { detail?: string } } };
                toast.error(error?.response?.data?.detail || "Failed to load users");
            } finally {
                setLoading(false);
            }
        },
        []
    );

    useEffect(() => {
        const timer = setTimeout(() => {
            fetchUsers(1, search, roleFilter, statusFilter);
        }, 300);
        return () => clearTimeout(timer);
    }, [search, roleFilter, statusFilter, fetchUsers]);

    useEffect(() => {
        let cancelled = false;
        async function fetchInstitutions() {
            setLoadingInstitutions(true);
            try {
                const rows = await listInstitutionsDetailed();
                if (!cancelled) setInstitutions(rows);
            } catch (err: unknown) {
                const error = err as { response?: { data?: { detail?: string } } };
                toast.error(error?.response?.data?.detail || "Failed to load institutions");
            } finally {
                if (!cancelled) setLoadingInstitutions(false);
            }
        }
        void fetchInstitutions();
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        if (!editorOpen || !editorInstitutionId) {
            setLocations([]);
            return;
        }
        const institution = institutions.find((row) => row.id === editorInstitutionId);
        if (!institution) return;

        let cancelled = false;
        async function fetchLocations() {
            setLoadingLocations(true);
            try {
                const rows = await listAdminInstitutionLocations(institution!.slug);
                if (!cancelled) setLocations(rows);
            } catch (err: unknown) {
                const error = err as { response?: { data?: { detail?: string } } };
                if (!cancelled) {
                    setLocations([]);
                    toast.error(error?.response?.data?.detail || "Failed to load locations");
                }
            } finally {
                if (!cancelled) setLoadingLocations(false);
            }
        }
        void fetchLocations();
        return () => { cancelled = true; };
    }, [editorOpen, editorInstitutionId, institutions]);

    function openCreateEditor() {
        setEditTarget(null);
        setEditorEmail("");
        setEditorRole("INSTITUTION_ADMIN");
        setEditorInstitutionId(institutions[0]?.id || "");
        setEditorLocationId("");
        setEditorOpen(true);
    }

    function openEditEditor(user: AdminUserRow) {
        setEditTarget(user);
        setEditorEmail(user.email);
        setEditorRole(user.role as AdminManagedRole);
        setEditorInstitutionId(user.institution_id || "");
        setEditorLocationId(user.location_id || "");
        setEditorOpen(true);
    }

    async function handleSaveUser() {
        const email = editorEmail.trim();
        if (!email || !email.includes("@")) {
            toast.error("Enter a valid email address");
            return;
        }
        if (!editorInstitutionId) {
            toast.error("Select an institution");
            return;
        }
        if (editorRole === "LOCATION_ADMIN" && !editorLocationId) {
            toast.error("Select a location for the location admin");
            return;
        }

        const payload = {
            email,
            role: editorRole,
            institution_id: editorInstitutionId,
            location_id: editorRole === "LOCATION_ADMIN" ? editorLocationId : undefined,
        };
        setSavingUser(true);
        try {
            if (editTarget) {
                await updateAdminUser(editTarget.id, payload);
                toast.success(`Updated ${email}`);
                setEditorOpen(false);
                await fetchUsers(page, search, roleFilter, statusFilter);
            } else {
                await inviteAdminUser(payload);
                toast.success(`Invite sent to ${email}`);
                setEditorOpen(false);
                setStatusFilter("pending");
                await fetchUsers(1, search, roleFilter, "pending");
            }
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || (editTarget ? "Failed to update user" : "Failed to add user"));
        } finally {
            setSavingUser(false);
        }
    }

    async function handleRemove() {
        if (!removeTarget) return;
        setIsRemoving(true);
        try {
            await removeAdminUser(removeTarget.id);
            toast.success(`Removed ${removeTarget.email}`);
            setRemoveTarget(null);
            fetchUsers(page, search, roleFilter, statusFilter);
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to remove user");
        } finally {
            setIsRemoving(false);
        }
    }

    async function handleReinvite(user: AdminUserRow) {
        if (reinviteCooldowns.isActive(user.id)) return;
        try {
            await reinviteAdminUser(user.id);
            toast.success(`Invite re-sent to ${user.email}`);
            reinviteCooldowns.start(user.id);
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to re-invite");
        }
    }

    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={UserCog}
                title="Users"
                description="Manage users across all institutions. Removing a user frees their email for re-invite."
                actions={
                    <div className="flex items-center gap-2">
                        <Button onClick={openCreateEditor} disabled={loadingInstitutions || institutions.length === 0}>
                            <Plus className="mr-2 h-4 w-4" />
                            Add User
                        </Button>
                        <Button
                            variant="outline"
                            onClick={() => fetchUsers(page, search, roleFilter, statusFilter)}
                            disabled={loading}
                        >
                            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </div>
                }
            />

            <Card>
                <CardHeader>
                    <CardTitle>Directory</CardTitle>
                    <CardDescription>Search and act on platform users.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex flex-wrap items-center gap-3">
                        <Input
                            type="text"
                            placeholder="Search by email…"
                            className="max-w-xs"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                        <Select value={roleFilter} onValueChange={setRoleFilter}>
                            <SelectTrigger className="w-[200px]">
                                <SelectValue placeholder="Role" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="ALL">All roles</SelectItem>
                                {ROLE_OPTIONS.map((r) => (
                                    <SelectItem key={r} value={r}>{formatRoleLabel(r)}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as AdminUserStatus)}>
                            <SelectTrigger className="w-[180px]">
                                <SelectValue placeholder="Status" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="active">Active</SelectItem>
                                <SelectItem value="pending">Pending</SelectItem>
                                <SelectItem value="deleted">Removed</SelectItem>
                                <SelectItem value="all">All</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {loading && users.length === 0 ? (
                        <TableSkeleton rows={6} cols={4} />
                    ) : (
                        <div className="space-y-4">
                            <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Email</TableHead>
                                            <TableHead>Role</TableHead>
                                            <TableHead>Institution</TableHead>
                                            <TableHead>Location</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {users.length === 0 && !loading && (
                                            <TableRow>
                                                <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                                                    No users found.
                                                </TableCell>
                                            </TableRow>
                                        )}
                                        {users.map((user) => {
                                            const isSuperAdmin = user.role === "SUPER_ADMIN";
                                            const isRemoved = !!user.deleted_at;
                                            const reinviteRemaining = reinviteCooldowns.getRemaining(user.id);
                                            return (
                                                <TableRow key={user.id}>
                                                    <TableCell className="font-medium">{user.email}</TableCell>
                                                    <TableCell>{formatRoleLabel(user.role)}</TableCell>
                                                    <TableCell className="text-sm text-muted-foreground">
                                                        {user.institution_name || <span className="text-muted-foreground">—</span>}
                                                    </TableCell>
                                                    <TableCell className="text-sm text-muted-foreground">
                                                        {user.location_name || <span className="text-muted-foreground">—</span>}
                                                    </TableCell>
                                                    <TableCell>{statusBadge(user)}</TableCell>
                                                    <TableCell className="text-right">
                                                        <div className="flex items-center justify-end gap-1">
                                                            {!isRemoved && user.invite_status === "PENDING" && !isSuperAdmin && (
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon"
                                                                    onClick={() => handleReinvite(user)}
                                                                    disabled={reinviteRemaining > 0}
                                                                    title={reinviteRemaining > 0 ? `Reinvite available in ${reinviteRemaining}s` : "Re-send invite"}
                                                                >
                                                                    <MailPlus className="h-4 w-4" />
                                                                </Button>
                                                            )}
                                                            {!isRemoved && (user.role === "INSTITUTION_ADMIN" || user.role === "LOCATION_ADMIN") && (
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon"
                                                                    onClick={() => openEditEditor(user)}
                                                                    title="Edit user"
                                                                    aria-label={`Edit ${user.email}`}
                                                                >
                                                                    <Pencil className="h-4 w-4" />
                                                                </Button>
                                                            )}
                                                            {!isRemoved && !isSuperAdmin && (
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon"
                                                                    onClick={() => setRemoveTarget(user)}
                                                                    title="Remove user"
                                                                >
                                                                    <Trash2 className="h-4 w-4 text-destructive" />
                                                                </Button>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            </div>

                            <div className="flex flex-col gap-3 border-t border-border px-2 pt-4 sm:flex-row sm:items-center sm:justify-between">
                                <div className="text-sm text-muted-foreground">
                                    {total > 0
                                        ? `Showing ${(page - 1) * PAGE_SIZE + 1} to ${Math.min(page * PAGE_SIZE, total)} of ${total} users`
                                        : "No users found"}
                                </div>
                                <div className="flex items-center space-x-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => fetchUsers(page - 1, search, roleFilter, statusFilter)}
                                        disabled={page === 1 || loading}
                                    >
                                        <ChevronLeft className="h-4 w-4 mr-1" />
                                        Previous
                                    </Button>
                                    <div className="text-sm font-medium mx-2">
                                        Page {page} of {totalPages}
                                    </div>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => fetchUsers(page + 1, search, roleFilter, statusFilter)}
                                        disabled={page >= totalPages || loading}
                                    >
                                        Next
                                        <ChevronRight className="h-4 w-4 ml-1" />
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={editorOpen} onOpenChange={(open) => !savingUser && setEditorOpen(open)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>{editTarget ? "Edit User" : "Add User"}</DialogTitle>
                        <DialogDescription>
                            {editTarget
                                ? "Update the admin's email, role, and tenant assignment."
                                : "Invite an institution admin or a location admin."}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="grid gap-4 py-2">
                        <div className="grid gap-2">
                            <Label htmlFor="admin-user-email">Email</Label>
                            <Input
                                id="admin-user-email"
                                type="email"
                                autoComplete="off"
                                placeholder="admin@institution.com"
                                value={editorEmail}
                                onChange={(event) => setEditorEmail(event.target.value)}
                            />
                        </div>

                        <div className="grid gap-2">
                            <Label>Role</Label>
                            <Select
                                value={editorRole}
                                onValueChange={(value) => {
                                    const role = value as AdminManagedRole;
                                    setEditorRole(role);
                                    if (role === "INSTITUTION_ADMIN") setEditorLocationId("");
                                }}
                            >
                                <SelectTrigger aria-label="Role">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="INSTITUTION_ADMIN">Institution Admin</SelectItem>
                                    <SelectItem value="LOCATION_ADMIN">Location Admin</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="grid gap-2">
                            <Label>Institution</Label>
                            <Select
                                value={editorInstitutionId || undefined}
                                onValueChange={(value) => {
                                    setEditorInstitutionId(value);
                                    setEditorLocationId("");
                                }}
                            >
                                <SelectTrigger aria-label="Institution">
                                    <SelectValue placeholder="Select an institution" />
                                </SelectTrigger>
                                <SelectContent>
                                    {institutions.map((institution) => (
                                        <SelectItem key={institution.id} value={institution.id}>
                                            {institution.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {editorRole === "LOCATION_ADMIN" && (
                            <div className="grid gap-2">
                                <Label>Location</Label>
                                <Select
                                    value={editorLocationId || undefined}
                                    onValueChange={setEditorLocationId}
                                    disabled={!editorInstitutionId || loadingLocations}
                                >
                                    <SelectTrigger aria-label="Location">
                                        <SelectValue placeholder={loadingLocations ? "Loading locations…" : "Select a location"} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {locations.map((location) => (
                                            <SelectItem key={location.id} value={location.id}>
                                                {location.name}{!location.is_active ? " (inactive)" : ""}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                {!loadingLocations && editorInstitutionId && locations.length === 0 && (
                                    <p className="text-sm text-muted-foreground">This institution has no locations.</p>
                                )}
                            </div>
                        )}
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={savingUser}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleSaveUser}
                            disabled={savingUser || loadingInstitutions || (editorRole === "LOCATION_ADMIN" && loadingLocations)}
                        >
                            {savingUser && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            {savingUser ? "Saving…" : editTarget ? "Save Changes" : "Send Invite"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Remove Confirmation Dialog */}
            <Dialog open={!!removeTarget} onOpenChange={(open) => !open && setRemoveTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Remove User</DialogTitle>
                        <DialogDescription>
                            Remove <strong>{removeTarget?.email}</strong>? They will lose access immediately and
                            their email will be freed for re-invite. This does not delete their audit history.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setRemoveTarget(null)} disabled={isRemoving}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleRemove} disabled={isRemoving}>
                            {isRemoving ? "Removing…" : "Remove"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
