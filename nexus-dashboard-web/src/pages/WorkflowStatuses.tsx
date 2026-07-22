/**
 * Manage Workflow Statuses — tenant-defined, human-assigned call workflow
 * states (Pending, Completed, …). INSTITUTION_ADMIN or LOCATION_ADMIN.
 *
 * Statuses are institution-scoped (one shared vocabulary across locations) and
 * soft-capped; archiving frees a slot. Deleting/archiving keeps historical call
 * assignments valid.
 */

import { useEffect, useState } from "react"
import { Tag, Plus, Pencil, Loader2, Archive, ArchiveRestore, RefreshCcw } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import {
    createWorkflowStatus,
    deleteWorkflowStatus,
    listWorkflowStatuses,
    updateWorkflowStatus,
} from "@/lib/workflow-status-api"
import { STATUS_COLORS, statusBadgeClasses, statusSwatchClass } from "@/lib/status-colors"
import { cn } from "@/lib/utils"
import type { WorkflowStatus } from "@/types"

const MAX_ACTIVE = 20

interface EditorState {
    open: boolean
    editing: WorkflowStatus | null
    name: string
    color: string
}

const EMPTY_EDITOR: EditorState = { open: false, editing: null, name: "", color: "zinc" }

export default function WorkflowStatuses() {
    const [statuses, setStatuses] = useState<WorkflowStatus[]>([])
    const [loading, setLoading] = useState(true)
    const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR)
    const [saving, setSaving] = useState(false)

    async function refresh() {
        setLoading(true)
        try {
            setStatuses(await listWorkflowStatuses(true)) // include inactive
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to load statuses")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { refresh() }, [])

    const activeCount = statuses.filter((s) => s.is_active).length
    const atCap = activeCount >= MAX_ACTIVE

    function openCreate() {
        setEditor({ open: true, editing: null, name: "", color: "zinc" })
    }
    function openEdit(s: WorkflowStatus) {
        setEditor({ open: true, editing: s, name: s.name, color: s.color })
    }

    async function handleSave() {
        const name = editor.name.trim()
        if (!name) { toast.error("Name is required"); return }
        setSaving(true)
        try {
            if (editor.editing) {
                await updateWorkflowStatus(editor.editing.id, { name, color: editor.color })
                toast.success("Status updated")
            } else {
                await createWorkflowStatus({ name, color: editor.color })
                toast.success("Status created")
            }
            setEditor(EMPTY_EDITOR)
            await refresh()
        } catch (e) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail ?? (e instanceof Error ? e.message : "Failed to save"))
        } finally {
            setSaving(false)
        }
    }

    async function toggleActive(s: WorkflowStatus) {
        try {
            if (s.is_active) {
                await deleteWorkflowStatus(s.id) // soft-delete (archive)
                toast.success(`"${s.name}" archived`)
            } else {
                await updateWorkflowStatus(s.id, { is_active: true })
                toast.success(`"${s.name}" restored`)
            }
            await refresh()
        } catch (e) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail ?? (e instanceof Error ? e.message : "Failed to update"))
        }
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>

            {/* Header */}
            <PageHeader
                icon={Tag}
                title="Call Statuses"
                description="Workflow states your team assigns to calls (e.g. Pending, Completed). Distinct from the AI tags applied automatically."
                actions={
                    <>
                        <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="gap-1.5">
                            <RefreshCcw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                            Refresh
                        </Button>
                        <Button size="sm" className="gap-1.5" onClick={openCreate} disabled={atCap}>
                            <Plus className="h-4 w-4" />
                            New status
                        </Button>
                    </>
                }
            />

            <Card>
                <CardContent className="p-0">
                    <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                        <p className="text-xs text-muted-foreground">
                            <span className="font-medium text-foreground">{activeCount}</span> of {MAX_ACTIVE} active
                        </p>
                        {atCap && (
                            <p className="text-xs text-amber-600 dark:text-amber-400">
                                Limit reached — archive one to add another.
                            </p>
                        )}
                    </div>

                    {loading ? (
                        <div className="space-y-2 p-4">
                            {Array.from({ length: 5 }).map((_, i) => (
                                <Skeleton key={i} className="h-10 w-full" />
                            ))}
                        </div>
                    ) : statuses.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 px-4 py-16 text-center text-muted-foreground">
                            <div className="grid size-12 place-items-center rounded-full bg-muted">
                                <Tag className="h-6 w-6 opacity-40" />
                            </div>
                            <p className="text-sm font-medium text-foreground/70">No statuses yet</p>
                            <p className="text-xs">Create your first workflow status to start triaging calls.</p>
                        </div>
                    ) : (
                        <ul className="divide-y divide-border">
                            {statuses.map((s) => (
                                <li
                                    key={s.id}
                                    className={cn(
                                        "flex items-center justify-between gap-3 px-4 py-2.5",
                                        !s.is_active && "opacity-60",
                                    )}
                                >
                                    <div className="flex items-center gap-3">
                                        <span
                                            className={cn(
                                                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
                                                statusBadgeClasses(s.color),
                                            )}
                                        >
                                            <span className={cn("h-1.5 w-1.5 rounded-full", statusSwatchClass(s.color))} />
                                            {s.name}
                                        </span>
                                        {!s.is_active && (
                                            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Archived</span>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-1">
                                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(s)} aria-label="Edit">
                                            <Pencil className="h-3.5 w-3.5" />
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8"
                                            onClick={() => toggleActive(s)}
                                            aria-label={s.is_active ? "Archive" : "Restore"}
                                        >
                                            {s.is_active ? <Archive className="h-3.5 w-3.5" /> : <ArchiveRestore className="h-3.5 w-3.5" />}
                                        </Button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </CardContent>
            </Card>

            {/* Create / edit dialog */}
            <Dialog open={editor.open} onOpenChange={(o) => !o && setEditor(EMPTY_EDITOR)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>{editor.editing ? "Edit status" : "New status"}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div>
                            <label className="mb-1 block text-xs font-medium text-muted-foreground">Name</label>
                            <Input
                                autoFocus
                                placeholder="e.g. Needs review"
                                value={editor.name}
                                maxLength={60}
                                onChange={(e) => setEditor((s) => ({ ...s, name: e.target.value }))}
                                onKeyDown={(e) => { if (e.key === "Enter") handleSave() }}
                            />
                        </div>
                        <div>
                            <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Color</label>
                            <div className="flex flex-wrap gap-2">
                                {STATUS_COLORS.map((c) => (
                                    <button
                                        key={c.key}
                                        type="button"
                                        onClick={() => setEditor((s) => ({ ...s, color: c.key }))}
                                        aria-label={c.label}
                                        title={c.label}
                                        className={cn(
                                            "h-7 w-7 rounded-full ring-offset-2 ring-offset-background transition",
                                            c.swatch,
                                            editor.color === c.key ? "ring-2 ring-foreground" : "hover:scale-110",
                                        )}
                                    />
                                ))}
                            </div>
                        </div>
                        <div>
                            <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Preview</label>
                            <span
                                className={cn(
                                    "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
                                    statusBadgeClasses(editor.color),
                                )}
                            >
                                <span className={cn("h-1.5 w-1.5 rounded-full", statusSwatchClass(editor.color))} />
                                {editor.name.trim() || "Status name"}
                            </span>
                        </div>
                    </div>
                    <div className="flex justify-end gap-2">
                        <Button variant="outline" size="sm" onClick={() => setEditor(EMPTY_EDITOR)} disabled={saving}>
                            Cancel
                        </Button>
                        <Button size="sm" className="gap-1.5" onClick={handleSave} disabled={saving}>
                            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                            {editor.editing ? "Save" : "Create"}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
