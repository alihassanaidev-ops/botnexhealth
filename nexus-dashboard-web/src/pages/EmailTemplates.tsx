import { useCallback, useEffect, useRef, useState } from "react"
import {
    ArrowLeft,
    Eye,
    Code,
    Loader2,
    Mail,
    RotateCcw,
    Save,
    AlertTriangle,
    CalendarCheck,
    Phone,
    Copy,
    Check,
} from "lucide-react"
import { toast } from "sonner"
import { Link } from "react-router-dom"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Separator } from "@/components/ui/separator"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    listEmailTemplates,
    updateEmailTemplate,
    resetEmailTemplate,
    livePreviewEmailTemplate,
    type EmailTemplate,
    type EmailTemplatePreviewResponse,
} from "@/lib/email-templates-api"

const TEMPLATE_META: Record<string, { label: string; description: string; icon: React.ElementType; color: string }> = {
    call_summary: {
        label: "Standard Call Summary",
        description: "Sent after every call is processed and classified",
        icon: Phone,
        color: "bg-blue-500/10 text-blue-500",
    },
    urgent_alert: {
        label: "Urgent Call Alert",
        description: "Sent for emergency or complaint calls requiring immediate attention",
        icon: AlertTriangle,
        color: "bg-red-500/10 text-red-500",
    },
    appointment_confirmation: {
        label: "Appointment Confirmation",
        description: "Sent to staff when AI books an appointment for a patient",
        icon: CalendarCheck,
        color: "bg-green-500/10 text-green-500",
    },
    patient_appointment_confirmation: {
        label: "Patient Appointment Confirmation",
        description: "Sent to the patient's email when AI books their appointment. Disabled by default — enable to start sending.",
        icon: Mail,
        color: "bg-purple-500/10 text-purple-500",
    },
}

const TEMPLATE_ORDER = [
    "call_summary",
    "urgent_alert",
    "appointment_confirmation",
    "patient_appointment_confirmation",
]

export default function EmailTemplates() {
    const [templates, setTemplates] = useState<EmailTemplate[]>([])
    const [loading, setLoading] = useState(true)
    const [selectedType, setSelectedType] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)
    const [resetDialogOpen, setResetDialogOpen] = useState(false)
    const [resetting, setResetting] = useState(false)

    // Editor state
    const [editName, setEditName] = useState("")
    const [editSubject, setEditSubject] = useState("")
    const [editHtml, setEditHtml] = useState("")
    const [editText, setEditText] = useState("")
    const [editActive, setEditActive] = useState(true)
    const [hasChanges, setHasChanges] = useState(false)

    // Preview state
    const [preview, setPreview] = useState<EmailTemplatePreviewResponse | null>(null)
    const [previewLoading, setPreviewLoading] = useState(false)
    const [editorTab, setEditorTab] = useState<string>("html")
    const previewTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

    const selectedTemplate = templates.find((t) => t.template_type === selectedType) ?? null

    // Load templates
    const fetchTemplates = useCallback(async () => {
        try {
            setLoading(true)
            const data = await listEmailTemplates()
            setTemplates(data)
        } catch {
            toast.error("Failed to load email templates")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchTemplates()
    }, [fetchTemplates])

    // Select a template for editing
    const selectTemplate = useCallback((t: EmailTemplate) => {
        setSelectedType(t.template_type)
        setEditName(t.name)
        setEditSubject(t.subject_template)
        setEditHtml(t.html_body)
        setEditText(t.text_body)
        setEditActive(t.is_active)
        setHasChanges(false)
        setPreview(null)
        setEditorTab("html")
    }, [])

    // Track changes
    useEffect(() => {
        if (!selectedTemplate) return
        const changed =
            editName !== selectedTemplate.name ||
            editSubject !== selectedTemplate.subject_template ||
            editHtml !== selectedTemplate.html_body ||
            editText !== selectedTemplate.text_body ||
            editActive !== selectedTemplate.is_active
        setHasChanges(changed)
    }, [editName, editSubject, editHtml, editText, editActive, selectedTemplate])

    // Debounced live preview
    const triggerPreview = useCallback(() => {
        if (!selectedType) return
        if (previewTimeoutRef.current) clearTimeout(previewTimeoutRef.current)
        previewTimeoutRef.current = setTimeout(async () => {
            try {
                setPreviewLoading(true)
                const result = await livePreviewEmailTemplate({
                    subject_template: editSubject,
                    html_body: editHtml,
                    text_body: editText,
                    template_type: selectedType,
                })
                setPreview(result)
            } catch {
                // Silent fail for live preview
            } finally {
                setPreviewLoading(false)
            }
        }, 600)
    }, [selectedType, editSubject, editHtml, editText])

    useEffect(() => {
        if (selectedType) triggerPreview()
        return () => {
            if (previewTimeoutRef.current) clearTimeout(previewTimeoutRef.current)
        }
    }, [triggerPreview, selectedType])

    // Save template
    const handleSave = async () => {
        if (!selectedType) return
        try {
            setSaving(true)
            const updated = await updateEmailTemplate(selectedType, {
                name: editName,
                subject_template: editSubject,
                html_body: editHtml,
                text_body: editText,
                is_active: editActive,
            })
            setTemplates((prev) => prev.map((t) => (t.template_type === selectedType ? updated : t)))
            setHasChanges(false)
            toast.success("Template saved successfully")
        } catch (err: unknown) {
            const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(message || "Failed to save template")
        } finally {
            setSaving(false)
        }
    }

    // Reset template
    const handleReset = async () => {
        if (!selectedType) return
        try {
            setResetting(true)
            const reset = await resetEmailTemplate(selectedType)
            setTemplates((prev) => prev.map((t) => (t.template_type === selectedType ? reset : t)))
            selectTemplate(reset)
            toast.success("Template reset to default")
        } catch {
            toast.error("Failed to reset template")
        } finally {
            setResetting(false)
            setResetDialogOpen(false)
        }
    }

    // Copy variable to clipboard
    const [copiedVar, setCopiedVar] = useState<string | null>(null)
    const copyVariable = (key: string) => {
        navigator.clipboard.writeText(`{{ ${key} }}`)
        setCopiedVar(key)
        setTimeout(() => setCopiedVar(null), 1500)
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        )
    }

    // Template list view
    if (!selectedType) {
        const sorted = [...templates].sort(
            (a, b) => TEMPLATE_ORDER.indexOf(a.template_type) - TEMPLATE_ORDER.indexOf(b.template_type),
        )

        return (
            <div className="space-y-6">
                <div>
                    <div className="flex items-center gap-2 mb-1">
                        <Link
                            to="/institution-admin/settings"
                            className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                        >
                            Settings
                        </Link>
                        <span className="text-muted-foreground/50">/</span>
                        <span className="text-sm font-medium">Email Templates</span>
                    </div>
                    <h1 className="text-2xl font-bold tracking-tight">Email Templates</h1>
                    <p className="text-muted-foreground mt-1">
                        Customize the notification emails sent to your team. Each template is linked
                        to a specific notification type.
                    </p>
                </div>

                <div className="grid gap-4">
                    {sorted.map((t) => {
                        const meta = TEMPLATE_META[t.template_type]
                        if (!meta) return null
                        const Icon = meta.icon
                        return (
                            <Card
                                key={t.id}
                                className="cursor-pointer hover:border-primary/40 transition-colors"
                                onClick={() => selectTemplate(t)}
                            >
                                <CardHeader className="pb-3">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className={`rounded-lg p-2 ${meta.color}`}>
                                                <Icon className="h-5 w-5" />
                                            </div>
                                            <div>
                                                <CardTitle className="text-base">{meta.label}</CardTitle>
                                                <CardDescription className="text-xs mt-0.5">
                                                    {meta.description}
                                                </CardDescription>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <Badge
                                                variant={t.is_active ? "default" : "secondary"}
                                                className={
                                                    t.is_active
                                                        ? "bg-green-500/10 text-green-500 hover:bg-green-500/20 border-0"
                                                        : "bg-zinc-500/10 text-zinc-400 hover:bg-zinc-500/20 border-0"
                                                }
                                            >
                                                {t.is_active ? "Active" : "Disabled"}
                                            </Badge>
                                            <Badge variant="outline" className="text-xs font-mono">
                                                {t.template_type}
                                            </Badge>
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent className="pt-0">
                                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                                        <span>
                                            <Mail className="inline h-3 w-3 mr-1" />
                                            Subject: <span className="text-foreground/70">{t.subject_template}</span>
                                        </span>
                                    </div>
                                </CardContent>
                            </Card>
                        )
                    })}
                </div>
            </div>
        )
    }

    // Editor view
    const meta = TEMPLATE_META[selectedType]
    const Icon = meta?.icon ?? Mail

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                            if (hasChanges) {
                                if (!confirm("You have unsaved changes. Discard them?")) return
                            }
                            setSelectedType(null)
                        }}
                    >
                        <ArrowLeft className="h-4 w-4" />
                    </Button>
                    <div className={`rounded-lg p-2 ${meta?.color ?? ""}`}>
                        <Icon className="h-5 w-5" />
                    </div>
                    <div>
                        <h1 className="text-lg font-semibold">{meta?.label ?? selectedType}</h1>
                        <p className="text-xs text-muted-foreground">{meta?.description}</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setResetDialogOpen(true)}
                    >
                        <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
                        Reset to Default
                    </Button>
                    <Button
                        size="sm"
                        onClick={handleSave}
                        disabled={!hasChanges || saving}
                    >
                        {saving ? (
                            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                        ) : (
                            <Save className="h-3.5 w-3.5 mr-1.5" />
                        )}
                        Save Changes
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                {/* Left: Editor */}
                <div className="space-y-4">
                    {/* Template settings */}
                    <Card>
                        <CardContent className="pt-5 space-y-4">
                            <div className="flex items-center justify-between">
                                <div className="space-y-1">
                                    <Label htmlFor="template-name">Template Name</Label>
                                    <Input
                                        id="template-name"
                                        value={editName}
                                        onChange={(e) => setEditName(e.target.value)}
                                        className="max-w-sm"
                                    />
                                </div>
                                <div className="flex items-center gap-2">
                                    <Label htmlFor="template-active" className="text-sm">
                                        Active
                                    </Label>
                                    <Switch
                                        id="template-active"
                                        checked={editActive}
                                        onCheckedChange={setEditActive}
                                    />
                                </div>
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="template-subject">Subject Line</Label>
                                <Input
                                    id="template-subject"
                                    value={editSubject}
                                    onChange={(e) => setEditSubject(e.target.value)}
                                    placeholder="Email subject with {{ variables }}"
                                    className="font-mono text-sm"
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Code editor */}
                    <Card>
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm font-medium">Template Body</CardTitle>
                                <Tabs value={editorTab} onValueChange={setEditorTab}>
                                    <TabsList className="h-8">
                                        <TabsTrigger value="html" className="text-xs px-2.5 h-6 gap-1">
                                            <Code className="h-3 w-3" />
                                            HTML
                                        </TabsTrigger>
                                        <TabsTrigger value="text" className="text-xs px-2.5 h-6 gap-1">
                                            <Mail className="h-3 w-3" />
                                            Plain Text
                                        </TabsTrigger>
                                    </TabsList>
                                </Tabs>
                            </div>
                        </CardHeader>
                        <CardContent className="pt-0">
                            {editorTab === "html" ? (
                                <textarea
                                    value={editHtml}
                                    onChange={(e) => setEditHtml(e.target.value)}
                                    className="w-full h-[400px] rounded-md border border-border bg-muted/30 p-3 font-mono text-xs leading-relaxed resize-y focus:outline-none focus:ring-2 focus:ring-primary/40"
                                    spellCheck={false}
                                />
                            ) : (
                                <textarea
                                    value={editText}
                                    onChange={(e) => setEditText(e.target.value)}
                                    className="w-full h-[400px] rounded-md border border-border bg-muted/30 p-3 font-mono text-xs leading-relaxed resize-y focus:outline-none focus:ring-2 focus:ring-primary/40"
                                    spellCheck={false}
                                />
                            )}
                        </CardContent>
                    </Card>

                    {/* Available variables */}
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium">Available Variables</CardTitle>
                            <CardDescription className="text-xs">
                                Click to copy. Use <code className="bg-muted px-1 rounded">{"{{ variable_name }}"}</code> syntax in your template.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-0">
                            <div className="flex flex-wrap gap-1.5">
                                {(selectedTemplate?.variables ?? []).map((v) => (
                                    <button
                                        key={v.key}
                                        onClick={() => copyVariable(v.key)}
                                        className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/50 px-2 py-1 text-xs font-mono hover:bg-muted transition-colors"
                                        title={`${v.label}: ${v.sample}`}
                                    >
                                        {copiedVar === v.key ? (
                                            <Check className="h-3 w-3 text-green-500" />
                                        ) : (
                                            <Copy className="h-3 w-3 text-muted-foreground" />
                                        )}
                                        {v.key}
                                    </button>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                </div>

                {/* Right: Preview */}
                <div className="space-y-4">
                    <Card className="sticky top-4">
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm font-medium flex items-center gap-1.5">
                                    <Eye className="h-4 w-4" />
                                    Live Preview
                                </CardTitle>
                                {previewLoading && (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                                )}
                            </div>
                            {preview && (
                                <div className="text-xs text-muted-foreground mt-1">
                                    <span className="font-medium text-foreground/70">Subject:</span>{" "}
                                    {preview.subject}
                                </div>
                            )}
                        </CardHeader>
                        <Separator />
                        <CardContent className="p-0">
                            {preview ? (
                                <iframe
                                    srcDoc={preview.html}
                                    className="w-full border-0 rounded-b-lg"
                                    style={{ height: "600px" }}
                                    title="Email preview"
                                    sandbox="allow-same-origin"
                                />
                            ) : (
                                <div className="flex items-center justify-center h-[600px] text-muted-foreground text-sm">
                                    {previewLoading ? "Loading preview..." : "Preview will appear here"}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>

            {/* Reset confirmation dialog */}
            <Dialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Reset to Default?</DialogTitle>
                        <DialogDescription>
                            This will replace your custom template with the original default.
                            This action cannot be undone.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setResetDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleReset}
                            disabled={resetting}
                        >
                            {resetting ? (
                                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                            ) : (
                                <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
                            )}
                            Reset Template
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
