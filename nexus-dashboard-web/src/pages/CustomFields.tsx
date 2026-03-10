import { useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { Plus, Pencil, Trash2, ShieldAlert, EyeOff } from "lucide-react"
import type { CustomFieldDefinition } from "@/types"
import {
    listFieldDefinitions,
    createFieldDefinition,
    updateFieldDefinition,
    deactivateFieldDefinition,
} from "@/lib/custom-fields-api"
import type {
    CreateFieldDefinitionPayload,
    UpdateFieldDefinitionPayload,
} from "@/lib/custom-fields-api"

const FIELD_TYPES = ["text", "number", "boolean", "date", "dropdown"] as const
const RETELL_SOURCES = [
    { value: "custom_analysis_data", label: "Custom Analysis Data" },
    { value: "collected_dynamic_variables", label: "Collected Dynamic Variables" },
] as const

const FIELD_KEY_RE = /^[a-z][a-z0-9_]*$/

interface FormState {
    field_name: string
    field_key: string
    field_type: string
    dropdown_options: string
    retell_source: string
    retell_source_key: string
    is_phi: boolean
    is_required: boolean
}

const emptyForm: FormState = {
    field_name: "",
    field_key: "",
    field_type: "text",
    dropdown_options: "",
    retell_source: "",
    retell_source_key: "",
    is_phi: false,
    is_required: false,
}

export default function CustomFields() {
    const [definitions, setDefinitions] = useState<CustomFieldDefinition[]>([])
    const [loading, setLoading] = useState(true)
    const [showInactive, setShowInactive] = useState(false)

    // Create/Edit dialog
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editing, setEditing] = useState<CustomFieldDefinition | null>(null)
    const [form, setForm] = useState<FormState>(emptyForm)
    const [saving, setSaving] = useState(false)

    // Delete dialog
    const [deleteTarget, setDeleteTarget] = useState<CustomFieldDefinition | null>(null)
    const [deleting, setDeleting] = useState(false)

    const fetchData = useCallback(async () => {
        setLoading(true)
        try {
            const data = await listFieldDefinitions("call", showInactive)
            setDefinitions(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load custom fields"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [showInactive])

    useEffect(() => {
        fetchData()
    }, [fetchData])

    const openCreate = () => {
        setEditing(null)
        setForm(emptyForm)
        setDialogOpen(true)
    }

    const openEdit = (defn: CustomFieldDefinition) => {
        setEditing(defn)
        setForm({
            field_name: defn.field_name,
            field_key: defn.field_key,
            field_type: defn.field_type,
            dropdown_options: defn.dropdown_options?.join(", ") ?? "",
            retell_source: defn.retell_source ?? "",
            retell_source_key: defn.retell_source_key ?? "",
            is_phi: defn.is_phi,
            is_required: defn.is_required,
        })
        setDialogOpen(true)
    }

    const handleSave = async () => {
        if (!form.field_name.trim()) {
            toast.error("Field name is required")
            return
        }
        if (!editing && !FIELD_KEY_RE.test(form.field_key)) {
            toast.error("Field key must match ^[a-z][a-z0-9_]*$")
            return
        }

        setSaving(true)
        try {
            if (editing) {
                const payload: UpdateFieldDefinitionPayload = {
                    field_name: form.field_name.trim(),
                    field_type: form.field_type,
                    is_phi: form.is_phi,
                    is_required: form.is_required,
                    dropdown_options:
                        form.field_type === "dropdown"
                            ? form.dropdown_options.split(",").map((s) => s.trim()).filter(Boolean)
                            : undefined,
                    retell_source: form.retell_source || undefined,
                    retell_source_key: form.retell_source_key.trim() || undefined,
                }
                await updateFieldDefinition(editing.id, payload)
                toast.success(`Updated "${form.field_name.trim()}"`)
            } else {
                const payload: CreateFieldDefinitionPayload = {
                    field_name: form.field_name.trim(),
                    field_key: form.field_key.trim(),
                    field_type: form.field_type,
                    is_phi: form.is_phi,
                    is_required: form.is_required,
                    dropdown_options:
                        form.field_type === "dropdown"
                            ? form.dropdown_options.split(",").map((s) => s.trim()).filter(Boolean)
                            : undefined,
                    retell_source: form.retell_source || undefined,
                    retell_source_key: form.retell_source_key.trim() || undefined,
                }
                await createFieldDefinition(payload)
                toast.success(`Created "${form.field_name.trim()}"`)
            }
            setDialogOpen(false)
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to save"
            toast.error(message)
        } finally {
            setSaving(false)
        }
    }

    const handleDeactivate = async () => {
        if (!deleteTarget) return
        setDeleting(true)
        try {
            await deactivateFieldDefinition(deleteTarget.id)
            toast.success(`Deactivated "${deleteTarget.field_name}"`)
            setDeleteTarget(null)
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to deactivate"
            toast.error(message)
        } finally {
            setDeleting(false)
        }
    }

    const handleHardDelete = async () => {
        if (!deleteTarget) return
        setDeleting(true)
        try {
            await deactivateFieldDefinition(deleteTarget.id, true)
            toast.success(`Permanently deleted "${deleteTarget.field_name}"`)
            setDeleteTarget(null)
            await fetchData()
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to delete"
            toast.error(message)
        } finally {
            setDeleting(false)
        }
    }

    const retellLabel = (defn: CustomFieldDefinition) => {
        if (!defn.retell_source_key) return "-"
        const src = RETELL_SOURCES.find((s) => s.value === defn.retell_source)
        return `${src?.label ?? defn.retell_source ?? "—"} → ${defn.retell_source_key}`
    }

    return (
        <div className="flex-1 space-y-4 bg-gradient-to-b from-background via-background to-accent/20 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Custom Fields</h2>
                    <p className="text-muted-foreground">
                        Define additional data fields for calls that auto-populate from Retell webhooks.
                    </p>
                </div>
                <div className="flex items-center space-x-2">
                    <Button onClick={openCreate}>
                        <Plus className="mr-2 h-4 w-4" /> Create
                    </Button>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle>Field Definitions</CardTitle>
                            <CardDescription>
                                {definitions.length} field{definitions.length !== 1 ? "s" : ""} configured.
                            </CardDescription>
                        </div>
                        <div className="flex items-center space-x-2">
                            <Label htmlFor="show-inactive" className="text-sm text-muted-foreground">
                                Show inactive
                            </Label>
                            <Switch
                                id="show-inactive"
                                checked={showInactive}
                                onCheckedChange={setShowInactive}
                            />
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-8 text-muted-foreground">Loading...</div>
                    ) : definitions.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                            <p>No custom fields defined yet.</p>
                            <p className="text-sm mt-1">Click "Create" to add your first custom field.</p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Field Name</TableHead>
                                    <TableHead>Field Key</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Retell Source</TableHead>
                                    <TableHead>PHI</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {definitions.map((defn) => (
                                    <TableRow key={defn.id}>
                                        <TableCell className="font-medium">{defn.field_name}</TableCell>
                                        <TableCell>
                                            <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                                                {defn.field_key}
                                            </code>
                                        </TableCell>
                                        <TableCell>
                                            <Badge variant="outline">{defn.field_type}</Badge>
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground max-w-[250px] truncate">
                                            {retellLabel(defn)}
                                        </TableCell>
                                        <TableCell>
                                            {defn.is_phi && (
                                                <ShieldAlert className="h-4 w-4 text-amber-500" />
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <Badge variant={defn.is_active ? "default" : "secondary"}>
                                                {defn.is_active ? "Active" : "Inactive"}
                                            </Badge>
                                        </TableCell>
                                        <TableCell className="text-right space-x-1">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => openEdit(defn)}
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            {defn.is_active ? (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => setDeleteTarget(defn)}
                                                >
                                                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                                                </Button>
                                            ) : (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => setDeleteTarget(defn)}
                                                >
                                                    <Trash2 className="h-4 w-4 text-destructive" />
                                                </Button>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Create / Edit Dialog */}
            <Dialog
                open={dialogOpen}
                onOpenChange={(open) => {
                    setDialogOpen(open)
                    if (!open) setEditing(null)
                }}
            >
                <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>
                            {editing ? "Edit Field Definition" : "Create Field Definition"}
                        </DialogTitle>
                        <DialogDescription>
                            {editing
                                ? "Update the field definition settings."
                                : "Define a new custom field that can auto-populate from Retell webhooks."}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <div className="space-y-2">
                            <Label htmlFor="field_name">Field Name</Label>
                            <Input
                                id="field_name"
                                placeholder="e.g. Insurance Provider"
                                value={form.field_name}
                                onChange={(e) =>
                                    setForm((f) => ({ ...f, field_name: e.target.value }))
                                }
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="field_key">Field Key</Label>
                            <Input
                                id="field_key"
                                placeholder="e.g. insurance_provider"
                                value={form.field_key}
                                disabled={!!editing}
                                onChange={(e) =>
                                    setForm((f) => ({ ...f, field_key: e.target.value }))
                                }
                            />
                            <p className="text-xs text-muted-foreground">
                                Lowercase letters, numbers, and underscores. Cannot be changed after creation.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="field_type">Field Type</Label>
                            <Select
                                value={form.field_type}
                                onValueChange={(v) => setForm((f) => ({ ...f, field_type: v }))}
                            >
                                <SelectTrigger id="field_type">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {FIELD_TYPES.map((t) => (
                                        <SelectItem key={t} value={t}>
                                            {t.charAt(0).toUpperCase() + t.slice(1)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        {form.field_type === "dropdown" && (
                            <div className="space-y-2">
                                <Label htmlFor="dropdown_options">Dropdown Options</Label>
                                <Input
                                    id="dropdown_options"
                                    placeholder="Option A, Option B, Option C"
                                    value={form.dropdown_options}
                                    onChange={(e) =>
                                        setForm((f) => ({
                                            ...f,
                                            dropdown_options: e.target.value,
                                        }))
                                    }
                                />
                                <p className="text-xs text-muted-foreground">
                                    Comma-separated list of allowed values.
                                </p>
                            </div>
                        )}
                        <div className="space-y-2">
                            <Label htmlFor="retell_source">Retell Source</Label>
                            <Select
                                value={form.retell_source}
                                onValueChange={(v) =>
                                    setForm((f) => ({
                                        ...f,
                                        retell_source: v === "__none__" ? "" : v,
                                    }))
                                }
                            >
                                <SelectTrigger id="retell_source">
                                    <SelectValue placeholder="None (manual only)" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="__none__">None (manual only)</SelectItem>
                                    {RETELL_SOURCES.map((s) => (
                                        <SelectItem key={s.value} value={s.value}>
                                            {s.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        {form.retell_source && (
                            <div className="space-y-2">
                                <Label htmlFor="retell_source_key">Retell Source Key</Label>
                                <Input
                                    id="retell_source_key"
                                    placeholder="e.g. insurance_provider"
                                    value={form.retell_source_key}
                                    onChange={(e) =>
                                        setForm((f) => ({
                                            ...f,
                                            retell_source_key: e.target.value,
                                        }))
                                    }
                                />
                                <p className="text-xs text-muted-foreground">
                                    The exact key name in the Retell webhook payload.
                                </p>
                            </div>
                        )}
                        <div className="flex items-center justify-between">
                            <div className="space-y-0.5">
                                <Label htmlFor="is_phi">PHI (Protected Health Information)</Label>
                                <p className="text-xs text-muted-foreground">
                                    Values will be encrypted at rest.
                                </p>
                            </div>
                            <Switch
                                id="is_phi"
                                checked={form.is_phi}
                                onCheckedChange={(v) =>
                                    setForm((f) => ({ ...f, is_phi: v }))
                                }
                            />
                        </div>
                        <div className="flex items-center justify-between">
                            <div className="space-y-0.5">
                                <Label htmlFor="is_required">Required</Label>
                                <p className="text-xs text-muted-foreground">
                                    Mark this field as required for the entity.
                                </p>
                            </div>
                            <Switch
                                id="is_required"
                                checked={form.is_required}
                                onCheckedChange={(v) =>
                                    setForm((f) => ({ ...f, is_required: v }))
                                }
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleSave} disabled={saving || !form.field_name.trim()}>
                            {saving ? "Saving..." : editing ? "Update" : "Create"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete / Deactivate Dialog */}
            <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Remove Field Definition</DialogTitle>
                        <DialogDescription>
                            What would you like to do with "{deleteTarget?.field_name}"?
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 py-2">
                        {deleteTarget?.is_active && (
                            <div className="rounded-md border border-primary/20 bg-background/70 p-3">
                                <p className="text-sm font-medium">Deactivate</p>
                                <p className="text-xs text-muted-foreground mt-1">
                                    Hides the field from new data but keeps existing values intact.
                                    You can reactivate it later.
                                </p>
                            </div>
                        )}
                        <div className="rounded-md border border-destructive/50 p-3">
                            <p className="text-sm font-medium text-destructive">Delete Permanently</p>
                            <p className="text-xs text-muted-foreground mt-1">
                                Removes the field definition and all stored values permanently.
                                This action cannot be undone.
                            </p>
                        </div>
                    </div>
                    <DialogFooter className="gap-2 sm:gap-0">
                        <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                            Cancel
                        </Button>
                        {deleteTarget?.is_active && (
                            <Button
                                variant="secondary"
                                onClick={handleDeactivate}
                                disabled={deleting}
                            >
                                {deleting ? "..." : "Deactivate"}
                            </Button>
                        )}
                        <Button
                            variant="destructive"
                            onClick={handleHardDelete}
                            disabled={deleting}
                        >
                            {deleting ? "..." : "Delete Permanently"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
