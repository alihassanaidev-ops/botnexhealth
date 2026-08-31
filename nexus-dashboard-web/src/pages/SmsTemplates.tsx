import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2, MessageSquare, RotateCcw, Save } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { FormSkeleton } from "@/components/ui/skeletons"
import {
    listSmsTemplates,
    livePreviewSmsTemplate,
    resetSmsTemplate,
    updateSmsTemplate,
    type SmsTemplate,
} from "@/lib/sms-templates-api"

/** Staff notification templates, in the same order as Email Templates. These
 *  text the clinic's own staff numbers — the patient-facing SMS templates
 *  (booked confirmation, request acknowledgement) are not edited here. */
const STAFF_TEMPLATE_TYPES = ["call_summary", "urgent_alert", "appointment_request"]

const TEMPLATE_BLURBS: Record<string, string> = {
    call_summary: "Texted to your team after every call is processed and classified.",
    urgent_alert: "Texted for emergency or complaint calls requiring immediate attention.",
    appointment_request:
        "Texted to staff when a caller requests an appointment that must be booked manually (no PMS).",
}

/** Carriers bill per 160-char GSM segment; warn once a message spans several. */
const SEGMENT_LENGTH = 160

function apiErrorMessage(error: unknown, fallback: string): string {
    return (
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || fallback
    )
}

function segmentCount(text: string): number {
    return Math.max(1, Math.ceil(text.length / SEGMENT_LENGTH))
}

function TemplateEditor({
    template,
    onSaved,
}: {
    template: SmsTemplate
    onSaved: (updated: SmsTemplate) => void
}) {
    const [body, setBody] = useState(template.body)
    const [isActive, setIsActive] = useState(template.is_active)
    const [preview, setPreview] = useState<string | null>(null)
    const [saving, setSaving] = useState(false)
    const [resetting, setResetting] = useState(false)

    useEffect(() => {
        setBody(template.body)
        setIsActive(template.is_active)
    }, [template.id, template.body, template.is_active])

    const dirty = body !== template.body || isActive !== template.is_active

    // Debounced live preview so the sample text tracks what's being typed
    // rather than what was last saved.
    useEffect(() => {
        let cancelled = false
        const timer = setTimeout(async () => {
            try {
                const result = await livePreviewSmsTemplate({
                    body,
                    template_type: template.template_type,
                })
                if (!cancelled) setPreview(result.body)
            } catch {
                if (!cancelled) setPreview(null)
            }
        }, 400)
        return () => {
            cancelled = true
            clearTimeout(timer)
        }
    }, [body, template.template_type])

    async function handleSave() {
        setSaving(true)
        try {
            const updated = await updateSmsTemplate(template.template_type, {
                body,
                is_active: isActive,
            })
            onSaved(updated)
            toast.success("Template saved")
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to save template"))
        } finally {
            setSaving(false)
        }
    }

    async function handleReset() {
        setResetting(true)
        try {
            const restored = await resetSmsTemplate(template.template_type)
            onSaved(restored)
            setBody(restored.body)
            setIsActive(restored.is_active)
            toast.success("Template reset to default")
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to reset template"))
        } finally {
            setResetting(false)
        }
    }

    const segments = segmentCount(preview ?? body)

    return (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                        <CardTitle className="text-base">{template.name}</CardTitle>
                        <CardDescription>
                            {TEMPLATE_BLURBS[template.template_type]
                                ?? "Staff notification text message."}{" "}
                            PHI-free — no patient name or date of birth.
                        </CardDescription>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        <Label htmlFor={`active-${template.id}`} className="text-xs text-muted-foreground">
                            Active
                        </Label>
                        <Switch
                            id={`active-${template.id}`}
                            checked={isActive}
                            onCheckedChange={setIsActive}
                        />
                    </div>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="space-y-1.5">
                    <Label htmlFor={`body-${template.id}`}>Message body</Label>
                    <Textarea
                        id={`body-${template.id}`}
                        value={body}
                        onChange={(event) => setBody(event.target.value)}
                        rows={5}
                        className="font-mono text-xs"
                    />
                    <p className="text-xs text-muted-foreground">
                        {(preview ?? body).length} characters &middot;{" "}
                        {segments} SMS segment{segments === 1 ? "" : "s"}
                        {segments > 1 && " — each segment is billed separately"}
                        . An unsubscribe footer is appended automatically at send time.
                    </p>
                </div>

                {template.variables.length > 0 && (
                    <div className="space-y-1.5">
                        <Label>Available variables</Label>
                        <div className="flex flex-wrap gap-1.5">
                            {template.variables.map((variable) => (
                                <Badge
                                    key={variable.key}
                                    variant="outline"
                                    className="cursor-pointer font-mono text-[11px]"
                                    onClick={() => setBody((prev) => `${prev}{{ ${variable.key} }}`)}
                                    title={`${variable.label} — click to insert`}
                                >
                                    {`{{ ${variable.key} }}`}
                                </Badge>
                            ))}
                        </div>
                    </div>
                )}

                <div className="space-y-1.5">
                    <Label>Preview</Label>
                    <div className="rounded-lg border bg-muted p-3 text-sm leading-relaxed">
                        {preview ?? <span className="text-muted-foreground">Rendering…</span>}
                    </div>
                </div>

                <div className="flex items-center justify-end gap-2">
                    <Button variant="outline" onClick={handleReset} disabled={resetting || saving}>
                        {resetting ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                            <RotateCcw className="mr-2 h-4 w-4" />
                        )}
                        Reset to default
                    </Button>
                    <Button onClick={handleSave} disabled={!dirty || saving || resetting}>
                        {saving ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                            <Save className="mr-2 h-4 w-4" />
                        )}
                        Save changes
                    </Button>
                </div>
            </CardContent>
        </Card>
    )
}

export default function SmsTemplates() {
    const [templates, setTemplates] = useState<SmsTemplate[]>([])
    const [loading, setLoading] = useState(true)

    const load = useCallback(async () => {
        setLoading(true)
        try {
            setTemplates(await listSmsTemplates())
        } catch (error) {
            toast.error(apiErrorMessage(error, "Failed to load SMS templates"))
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        void load()
    }, [load])

    const visible = useMemo(
        () =>
            STAFF_TEMPLATE_TYPES.map((type) =>
                templates.find((t) => t.template_type === type),
            ).filter((t): t is SmsTemplate => Boolean(t)),
        [templates],
    )

    function handleSaved(updated: SmsTemplate) {
        setTemplates((prev) =>
            prev.map((t) => (t.template_type === updated.template_type ? updated : t)),
        )
    }

    if (loading) return <FormSkeleton rows={6} />

    return (
        <div className="p-6 max-w-3xl mx-auto space-y-6">
            <PageHeader
                icon={MessageSquare}
                title="SMS Templates"
                description="Customize the notification texts sent to your team. Each template is linked to a specific notification type."
            />

            {visible.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                    No SMS templates available for this clinic.
                </div>
            ) : (
                visible.map((template) => (
                    <TemplateEditor key={template.id} template={template} onSaved={handleSaved} />
                ))
            )}
        </div>
    )
}
