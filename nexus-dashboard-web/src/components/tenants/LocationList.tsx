import { useState, useEffect, useCallback } from "react";
import { Plus, RefreshCw, Pencil, Trash2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
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
import {
    Alert,
    AlertDescription,
    AlertTitle,
} from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { LocationForm } from "./LocationForm";
import { toast } from "sonner";
import api from "@/lib/api";
import type { Location, SyncResult } from "@/types";

interface LocationListProps {
    tenantSlug: string;
}

export function LocationList({ tenantSlug }: LocationListProps) {
    const [locations, setLocations] = useState<Location[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [viewMode, setViewMode] = useState<"list" | "form">("list");
    const [editingLocation, setEditingLocation] = useState<Location | undefined>();
    const [deleteTarget, setDeleteTarget] = useState<Location | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);
    const [syncingSlug, setSyncingSlug] = useState<string | null>(null);
    const [syncResult, setSyncResult] = useState<SyncResult | null>(null);

    const fetchLocations = useCallback(async () => {
        setIsLoading(true);
        try {
            const { data } = await api.get<Location[]>(`/admin/tenants/${tenantSlug}/locations`);
            setLocations(data);
        } catch (error) {
            console.error("Failed to fetch locations", error);
            toast.error("Failed to fetch locations");
        } finally {
            setIsLoading(false);
        }
    }, [tenantSlug]);

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
            await api.delete(`/admin/tenants/${tenantSlug}/locations/${deleteTarget.slug}`);
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
                `/admin/tenants/${tenantSlug}/locations/${locationSlug}/sync`
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
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {locations.length === 0 && !isLoading && (
                                <TableRow>
                                    <TableCell colSpan={6} className="h-24 text-center">
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
                <div className="space-y-4">
                    <div className="mb-6">
                        <Button variant="ghost" size="sm" className="mb-4 text-muted-foreground" onClick={() => setViewMode("list")}>
                            &larr; Back to Locations
                        </Button>
                        <div className="border-b pb-4">
                            <h2 className="text-2xl font-bold tracking-tight">{editingLocation ? "Edit Location" : "Add Location"}</h2>
                            <p className="text-sm text-muted-foreground mt-1">
                                {editingLocation
                                    ? `Update settings for ${editingLocation.name}`
                                    : "Create a new location for this tenant"}
                            </p>
                        </div>
                    </div>
                    <div className="max-w-2xl">
                        <LocationForm
                            tenantSlug={tenantSlug}
                            location={editingLocation}
                            onSuccess={handleFormSuccess}
                        />
                    </div>
                </div>
            )}

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
