import { useState, useEffect, useCallback } from "react";
import { Plus, RefreshCw, Pencil, Trash2, Loader2, MessageSquare, UserPlus, MailPlus, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { LocationForm } from "./LocationForm";
import { LocationHoursDialog } from "./LocationHoursDialog";
import { toast } from "sonner";
import api from "@/lib/api";
import type { Location, SyncResult } from "@/types";

interface LocationListProps {
    institutionSlug: string;
}

export function LocationList({ institutionSlug }: LocationListProps) {
    const [locations, setLocations] = useState<Location[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [viewMode, setViewMode] = useState<"list" | "form">("list");
    const [editingLocation, setEditingLocation] = useState<Location | undefined>();
    const [hoursTarget, setHoursTarget] = useState<Location | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<Location | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);
    const [syncingSlug, setSyncingSlug] = useState<string | null>(null);
    const [syncResult, setSyncResult] = useState<SyncResult | null>(null);

    // Invite state
    const [inviteTarget, setInviteTarget] = useState<Location | null>(null);
    const [inviteEmail, setInviteEmail] = useState("");
    const [isInviting, setIsInviting] = useState(false);

    const fetchLocations = useCallback(async () => {
        setIsLoading(true);
        try {
            const { data } = await api.get<Location[]>(`/admin/institutions/${institutionSlug}/locations`);
            setLocations(data);
        } catch (error) {
            console.error("Failed to fetch locations", error);
            toast.error("Failed to fetch locations");
        } finally {
            setIsLoading(false);
        }
    }, [institutionSlug]);

    useEffect(() => {
        fetchLocations();
    }, [fetchLocations]);

    function openCreateSheet() {
        setEditingLocation(undefined);
        setViewMode("form");
    }

    function openEditSheet(location: Location) {
        setEditingLocation(location);
        setViewMode("form");
    }

    function handleFormSuccess() {
        setViewMode("list");
        setEditingLocation(undefined);
        fetchLocations();
    }

    async function handleDelete() {
        if (!deleteTarget) return;
        setIsDeleting(true);
        try {
            await api.delete(`/admin/institutions/${institutionSlug}/locations/${deleteTarget.slug}`);
            toast.success(`Location "${deleteTarget.name}" deleted`);
            setDeleteTarget(null);
            fetchLocations();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to delete location");
        } finally {
            setIsDeleting(false);
        }
    }

    async function handleSync(locationSlug: string) {
        setSyncingSlug(locationSlug);
        setSyncResult(null);
        try {
            const { data } = await api.post<SyncResult>(
                `/admin/institutions/${institutionSlug}/locations/${locationSlug}/sync`
            );
            setSyncResult(data);
            if (data.success) {
                toast.success(
                    `Synced ${data.providers_synced} providers and ${data.appointment_types_synced} appointment types`
                );
            } else {
                toast.error("Sync completed with errors");
            }
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Sync failed");
        } finally {
            setSyncingSlug(null);
        }
    }

    async function handleInvite() {
        if (!inviteTarget || !inviteEmail.trim()) return;
        setIsInviting(true);
        try {
            await api.post(
                `/admin/institutions/${institutionSlug}/locations/${inviteTarget.slug}/invite`,
                { email: inviteEmail.trim() }
            );
            toast.success(`Invite sent to ${inviteEmail.trim()}`);
            setInviteTarget(null);
            setInviteEmail("");
            fetchLocations();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to send invite");
        } finally {
            setIsInviting(false);
        }
    }

    async function handleReinvite(loc: Location) {
        if (!loc.user) return;
        try {
            await api.post(
                `/admin/institutions/${institutionSlug}/locations/${loc.slug}/reinvite`,
                { email: loc.user.email }
            );
            toast.success(`Re-invite sent to ${loc.user.email}`);
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to re-invite");
        }
    }

    return (
        <div className="space-y-4">
            {viewMode === "list" && (
                <div className="flex items-center justify-between">
                    <p className="text-sm text-muted-foreground">
                        {locations.length} location{locations.length !== 1 ? "s" : ""}
                    </p>
                    <div className="flex items-center gap-2">
                        <Button variant="outline" size="sm" onClick={fetchLocations} disabled={isLoading}>
                            <RefreshCw className={`mr-1 h-3 w-3 ${isLoading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                        <Button size="sm" onClick={openCreateSheet}>
                            <Plus className="mr-1 h-3 w-3" /> Add Location
                        </Button>
                    </div>
                </div>
            )}

            {syncResult && (
                <Alert variant={syncResult.success ? "default" : "destructive"}>
                    <AlertTitle>{syncResult.success ? "Sync Successful" : "Sync Errors"}</AlertTitle>
                    <AlertDescription>
                        <p>Providers synced: {syncResult.providers_synced}</p>
                        <p>Appointment types synced: {syncResult.appointment_types_synced}</p>
                        {syncResult.errors.length > 0 && (
                            <ul className="mt-2 list-disc pl-4">
                                {syncResult.errors.map((err, i) => (
                                    <li key={i} className="text-sm">{err}</li>
                                ))}
                            </ul>
                        )}
                    </AlertDescription>
                </Alert>
            )}

            {viewMode === "list" && (
                <div className="border rounded-md">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Slug</TableHead>
                                <TableHead>NexHealth Loc ID</TableHead>
                                <TableHead>Retell Agent</TableHead>
                                <TableHead>SMS Number</TableHead>
                                <TableHead>User</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {locations.length === 0 && !isLoading && (
                                <TableRow>
                                    <TableCell colSpan={8} className="h-24 text-center">
                                        No locations found. Add one to get started.
                                    </TableCell>
                                </TableRow>
                            )}
                            {locations.map((loc) => (
                                <TableRow key={loc.id}>
                                    <TableCell className="font-medium">{loc.name}</TableCell>
                                    <TableCell className="font-mono text-sm">{loc.slug}</TableCell>
                                    <TableCell className="font-mono text-sm">
                                        {loc.nexhealth_location_id || <span className="text-muted-foreground">-</span>}
                                    </TableCell>
                                    <TableCell className="font-mono text-sm">
                                        {loc.retell_agent_id
                                            ? <span title={loc.retell_agent_id}>{loc.retell_agent_id.slice(0, 12)}...</span>
                                            : <span className="text-muted-foreground">-</span>}
                                    </TableCell>
                                    <TableCell className="font-mono text-sm">
                                        {loc.twilio_from_number
                                            ? (
                                                <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
                                                    <MessageSquare className="h-3 w-3 shrink-0" />
                                                    {loc.twilio_from_number}
                                                </span>
                                            )
                                            : <span className="text-muted-foreground">-</span>}
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        {loc.user ? (
                                            <div className="flex items-center gap-1.5">
                                                <span className="truncate max-w-[160px]" title={loc.user.email}>
                                                    {loc.user.email}
                                                </span>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-6 w-6 shrink-0"
                                                    onClick={() => handleReinvite(loc)}
                                                    title="Re-send invite"
                                                >
                                                    <MailPlus className="h-3.5 w-3.5" />
                                                </Button>
                                            </div>
                                        ) : (
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                className="h-7 text-xs"
                                                onClick={() => {
                                                    setInviteTarget(loc);
                                                    setInviteEmail("");
                                                }}
                                            >
                                                <UserPlus className="mr-1 h-3 w-3" />
                                                Invite
                                            </Button>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant={loc.is_active ? "default" : "secondary"}>
                                            {loc.is_active ? "Active" : "Inactive"}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex items-center justify-end gap-1">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => handleSync(loc.slug)}
                                                disabled={syncingSlug === loc.slug}
                                                title="Sync PMS data"
                                            >
                                                {syncingSlug === loc.slug ? (
                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                ) : (
                                                    <RefreshCw className="h-4 w-4" />
                                                )}
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => setHoursTarget(loc)}
                                                title="Operating Hours & Breaks"
                                            >
                                                <Clock className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => openEditSheet(loc)}
                                                title="Edit location"
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => setDeleteTarget(loc)}
                                                title="Delete location"
                                            >
                                                <Trash2 className="h-4 w-4 text-destructive" />
                                            </Button>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {/* Create / Edit Form View */}
            {viewMode === "form" && (
                <div>
                    <LocationForm
                        institutionSlug={institutionSlug}
                        location={editingLocation}
                        onSuccess={handleFormSuccess}
                        onCancel={() => setViewMode("list")}
                    />
                </div>
            )}

            {/* Location Hours Dialog */}
            <LocationHoursDialog
                institutionSlug={institutionSlug}
                location={hoursTarget}
                onClose={() => setHoursTarget(null)}
            />

            {/* Invite Location User Dialog */}
            <Dialog open={!!inviteTarget} onOpenChange={(open) => { if (!open) { setInviteTarget(null); setInviteEmail(""); } }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Invite Location User</DialogTitle>
                        <DialogDescription>
                            Send an invite to a user for <strong>{inviteTarget?.name}</strong>. They will receive an email to set up their account with <code>LOCATION_ADMIN</code> role access.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2 py-2">
                        <Label htmlFor="invite-email">Email Address</Label>
                        <Input
                            id="invite-email"
                            type="email"
                            placeholder="user@clinic.com"
                            value={inviteEmail}
                            onChange={(e) => setInviteEmail(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") handleInvite(); }}
                            disabled={isInviting}
                        />
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => { setInviteTarget(null); setInviteEmail(""); }} disabled={isInviting}>
                            Cancel
                        </Button>
                        <Button onClick={handleInvite} disabled={isInviting || !inviteEmail.trim()}>
                            {isInviting ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Sending...
                                </>
                            ) : (
                                <>
                                    <UserPlus className="mr-2 h-4 w-4" />
                                    Send Invite
                                </>
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete Confirmation Dialog */}
            <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete Location</DialogTitle>
                        <DialogDescription>
                            Are you sure you want to delete &quot;{deleteTarget?.name}&quot;? This action cannot be undone.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={isDeleting}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleDelete} disabled={isDeleting}>
                            {isDeleting ? "Deleting..." : "Delete"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
