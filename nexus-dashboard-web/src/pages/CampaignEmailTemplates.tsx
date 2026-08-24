/**
 * Campaign email templates — clinic-authored, reusable across campaigns.
 *
 * Separate from the Email Templates page, which edits the five fixed system
 * notification templates. These are free-form: a clinic creates as many as it
 * wants and references them from a Send Email step by name.
 */
import { useCallback, useEffect, useMemo, useState } from "react"
import {
    Check,
    Code,
    Copy,
    Eye,
    Loader2,
    Mail,
    Plus,
    Save,
    Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CardsSkeleton } from "@/components/ui/skeletons"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    createCampaignEmailTemplate,
    deleteCampaignEmailTemplate,
    listCampaignEmailTemplates,
    listCampaignMergeFields,
    previewCampaignEmailTemplate,
    updateCampaignEmailTemplate,
    type CampaignEmailTemplate,
    type CampaignEmailTemplatePreview,
    type CampaignMergeField,
} from "@/lib/campaign-email-templates-api"

const NEW_TEMPLATE: Omit<CampaignEmailTemplate, "id" | "key"> = {
    name: "",
    subject_template: "",
    html_body: "",
    text_body: "",
    is_active: true,
}

/** Surface the API's message rather than a generic failure — the backend
 *  explains exactly which field is wrong and why. */
function errorMessage(err: unknown, fallback: string): string {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
        ?.detail
    return typeof detail === "string" ? detail : fallback
}

export default function CampaignEmailTemplates() {
    const [templates, setTemplates] = useState<CampaignEmailTemplate[]>([])
    const [mergeFields, setMergeFields] = useState<CampaignMergeField[]>([])
    const [loading, setLoading] = useState(true)
    const [selectedKey, setSelectedKey] = useState<string | null>(null)
    const [creating, setCreating] = useState(false)
    const [draft, setDraft] = useState({ ...NEW_TEMPLATE })
    const [saving, setSaving] = useState(false)
    const [bodyTab, setBodyTab] = useState<"html" | "text">("html")
    const [preview, setPreview] = useState<CampaignEmailTemplatePreview | null>(null)
    const [previewing, setPreviewing] = useState(false)
    const [deleteTarget, setDeleteTarget] = useState<CampaignEmailTemplate | null>(null)
    const [copiedField, setCopiedField] = useState<string | null>(null)

    const load = useCallback(async () => {
        setLoading(true)
        try {
            const [list, fields] = await Promise.all([
                listCampaignEmailTemplates(),
                listCampaignMergeFields(),
            ])
            setTemplates(list)
            setMergeFields(fields)
        } catch {
            toast.error("Failed to load campaign email templates")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        void load()
    }, [load])

    const selected = useMemo(
        () => templates.find((t) => t.key === selectedKey) ?? null,
        [templates, selectedKey],
    )

    const openTemplate = (template: CampaignEmailTemplate) => {
        setCreating(false)
        setSelectedKey(template.key)
        setPreview(null)
        setDraft({
            name: template.name,
            subject_template: template.subject_template,
            html_body: template.html_body,
            text_body: template.text_body,
            is_active: template.is_active,
        })
    }

    const startCreate = () => {
        setCreating(true)
        setSelectedKey(null)
        setPreview(null)
        setDraft({ ...NEW_TEMPLATE })
    }

    const closeEditor = () => {
        setCreating(false)
        setSelectedKey(null)
        setPreview(null)
    }

    const save = async () => {
        setSaving(true)
        try {
            if (creating) {
                const created = await createCampaignEmailTemplate({
                    name: draft.name,
                    subject_template: draft.subject_template,
                    html_body: draft.html_body,
                    text_body: draft.text_body,
                    is_active: draft.is_active,
                })
                toast.success(`Created “${created.name}”`)
                setTemplates((prev) => [...prev, created])
                setCreating(false)
                setSelectedKey(created.key)
            } else if (selected) {
                const updated = await updateCampaignEmailTemplate(selected.key, draft)
                toast.success("Template saved")
                setTemplates((prev) =>
                    prev.map((t) => (t.key === updated.key ? updated : t)),
                )
            }
        } catch (err) {
            toast.error(errorMessage(err, "Failed to save template"))
        } finally {
            setSaving(false)
        }
    }

    const runPreview = async () => {
        setPreviewing(true)
        try {
            setPreview(
                await previewCampaignEmailTemplate({
                    subject_template: draft.subject_template,
                    html_body: draft.html_body,
                    text_body: draft.text_body,
                }),
            )
        } catch (err) {
            toast.error(errorMessage(err, "Failed to render preview"))
        } finally {
            setPreviewing(false)
        }
    }

    const confirmDelete = async () => {
        if (!deleteTarget) return
        try {
            await deleteCampaignEmailTemplate(deleteTarget.key)
            toast.success(`Deleted “${deleteTarget.name}”`)
            setTemplates((prev) => prev.filter((t) => t.key !== deleteTarget.key))
            if (selectedKey === deleteTarget.key) closeEditor()
        } catch (err) {
            toast.error(errorMessage(err, "Failed to delete template"))
        } finally {
            setDeleteTarget(null)
        }
    }

    const copyField = async (name: string) => {
        try {
            await navigator.clipboard.writeText(`{{${name}}}`)
            setCopiedField(name)
            setTimeout(() => setCopiedField(null), 1500)
        } catch {
            toast.error("Could not copy to clipboard")
        }
    }

    const editing = creating || selected !== null
    const canSave =
        draft.name.trim() !== "" &&
        draft.subject_template.trim() !== "" &&
        draft.html_body.trim() !== "" &&
        draft.text_body.trim() !== ""

    if (loading) return <CardsSkeleton />

    return (
        <div className="space-y-6">
            <PageHeader
                icon={Mail}
                title="Campaign Email Templates"
                description="Reusable emails you can select from any Send Email step in a campaign."
                actions={
                    !editing ? (
                        <Button onClick={startCreate}>
                            <Plus className="mr-2 h-4 w-4" />
                            New template
                        </Button>
                    ) : undefined
                }
            />

            {!editing && (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {templates.length === 0 && (
                        <Card className="sm:col-span-2 lg:col-span-3">
                            <CardContent className="py-10 text-center text-sm text-muted-foreground">
                                No templates yet. Create one and it becomes selectable in
                                any campaign’s Send Email step.
                            </CardContent>
                        </Card>
                    )}
                    {templates.map((template) => (
                        <Card
                            key={template.key}
                            className="cursor-pointer transition-colors hover:border-primary/50"
                            onClick={() => openTemplate(template)}
                        >
                            <CardHeader className="pb-2">
                                <div className="flex items-start justify-between gap-2">
                                    <CardTitle className="text-base">{template.name}</CardTitle>
                                    {!template.is_active && (
                                        <Badge variant="secondary">Inactive</Badge>
                                    )}
                                </div>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                <p className="truncate text-sm text-muted-foreground">
                                    {template.subject_template}
                                </p>
                                <code className="text-xs text-muted-foreground">
                                    {template.key}
                                </code>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            {editing && (
                <div className="grid gap-6 lg:grid-cols-[2fr,1fr]">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">
                                {creating ? "New template" : selected?.name}
                            </CardTitle>
                            {!creating && selected && (
                                <p className="text-xs text-muted-foreground">
                                    Referenced in campaigns as{" "}
                                    <code>{selected.key}</code> — this cannot be changed,
                                    because published campaigns point at it.
                                </p>
                            )}
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-1.5">
                                <Label htmlFor="tpl-name">Name</Label>
                                <Input
                                    id="tpl-name"
                                    value={draft.name}
                                    placeholder="e.g. Post-Op Day 1"
                                    onChange={(e) =>
                                        setDraft({ ...draft, name: e.target.value })
                                    }
                                />
                            </div>

                            <div className="space-y-1.5">
                                <Label htmlFor="tpl-subject">Subject</Label>
                                <Input
                                    id="tpl-subject"
                                    value={draft.subject_template}
                                    placeholder="How are you feeling, {{patient_first_name}}?"
                                    onChange={(e) =>
                                        setDraft({
                                            ...draft,
                                            subject_template: e.target.value,
                                        })
                                    }
                                />
                            </div>

                            <div className="space-y-1.5">
                                <div className="flex items-center justify-between">
                                    <Label>Body</Label>
                                    <Tabs
                                        value={bodyTab}
                                        onValueChange={(v) => setBodyTab(v as "html" | "text")}
                                    >
                                        <TabsList className="h-7">
                                            <TabsTrigger value="html" className="h-6 gap-1 px-2.5 text-xs">
                                                <Code className="h-3 w-3" />
                                                HTML
                                            </TabsTrigger>
                                            <TabsTrigger value="text" className="h-6 gap-1 px-2.5 text-xs">
                                                Plain text
                                            </TabsTrigger>
                                        </TabsList>
                                    </Tabs>
                                </div>
                                {bodyTab === "html" ? (
                                    <Textarea
                                        rows={14}
                                        className="font-mono text-xs"
                                        value={draft.html_body}
                                        onChange={(e) =>
                                            setDraft({ ...draft, html_body: e.target.value })
                                        }
                                    />
                                ) : (
                                    <Textarea
                                        rows={14}
                                        className="font-mono text-xs"
                                        value={draft.text_body}
                                        onChange={(e) =>
                                            setDraft({ ...draft, text_body: e.target.value })
                                        }
                                    />
                                )}
                                <p className="text-xs text-muted-foreground">
                                    Both are sent together. Some people read the plain-text
                                    version, so keep it filled in.
                                </p>
                            </div>

                            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                                <div>
                                    <Label className="text-sm">Active</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Campaigns cannot be published against an inactive
                                        template.
                                    </p>
                                </div>
                                <Switch
                                    checked={draft.is_active}
                                    onCheckedChange={(c) =>
                                        setDraft({ ...draft, is_active: c })
                                    }
                                />
                            </div>

                            <div className="flex flex-wrap gap-2">
                                <Button onClick={save} disabled={saving || !canSave}>
                                    {saving ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <Save className="mr-2 h-4 w-4" />
                                    )}
                                    Save
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={runPreview}
                                    disabled={previewing}
                                >
                                    {previewing ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <Eye className="mr-2 h-4 w-4" />
                                    )}
                                    Preview
                                </Button>
                                <Button variant="ghost" onClick={closeEditor}>
                                    Close
                                </Button>
                                {!creating && selected && (
                                    <Button
                                        variant="ghost"
                                        className="ml-auto text-destructive"
                                        onClick={() => setDeleteTarget(selected)}
                                    >
                                        <Trash2 className="mr-2 h-4 w-4" />
                                        Delete
                                    </Button>
                                )}
                            </div>

                            {preview && (
                                <div className="space-y-2 rounded-md border border-border p-3">
                                    <p className="text-xs font-medium text-muted-foreground">
                                        Preview with sample details
                                    </p>
                                    <p className="text-sm font-medium">{preview.subject}</p>
                                    <div
                                        className="overflow-x-auto rounded border border-border bg-background p-3 text-sm"
                                        // Preview HTML is rendered by the backend's escaping
                                        // template environment, so patient-shaped values are
                                        // already escaped before they reach here.
                                        dangerouslySetInnerHTML={{ __html: preview.html }}
                                    />
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Merge fields</CardTitle>
                            <p className="text-xs text-muted-foreground">
                                Click to copy. Anything unavailable at send time renders as
                                empty rather than showing the placeholder.
                            </p>
                        </CardHeader>
                        <CardContent className="max-h-[32rem] space-y-1 overflow-y-auto">
                            {mergeFields.map((field) => (
                                <button
                                    key={field.name}
                                    type="button"
                                    onClick={() => void copyField(field.name)}
                                    className="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left hover:bg-muted"
                                >
                                    <span className="min-w-0">
                                        <code className="text-xs">{`{{${field.name}}}`}</code>
                                        <span className="block truncate text-xs text-muted-foreground">
                                            {field.label}
                                        </span>
                                    </span>
                                    {copiedField === field.name ? (
                                        <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                                    ) : (
                                        <Copy className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                    )}
                                </button>
                            ))}
                        </CardContent>
                    </Card>
                </div>
            )}

            <Dialog
                open={deleteTarget !== null}
                onOpenChange={(open) => !open && setDeleteTarget(null)}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete this template?</DialogTitle>
                        <DialogDescription>
                            “{deleteTarget?.name}” will be removed. Any campaign still
                            referencing it will fail to send, so check your campaigns
                            first. Deactivating instead keeps it available to fix.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={confirmDelete}>
                            Delete
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
