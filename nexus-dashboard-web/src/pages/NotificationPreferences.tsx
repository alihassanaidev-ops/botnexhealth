import { useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { toast } from "sonner"
import {
    getNotificationPreferences,
    updateNotificationPreferences,
    type NotificationPreference,
} from "@/lib/notification-settings-api"
import { Phone, AlertTriangle, CalendarCheck, Loader2 } from "lucide-react"

const TEMPLATE_META: Record<string, { label: string; description: string; icon: React.ElementType }> = {
    call_summary: {
        label: "Call Summary",
        description: "Receive an email summary after every call handled by the AI agent.",
        icon: Phone,
    },
    urgent_alert: {
        label: "Urgent Call Alert",
        description: "Get notified immediately when a call is flagged as urgent or a complaint.",
        icon: AlertTriangle,
    },
    appointment_confirmation: {
        label: "Appointment Confirmation",
        description: "Receive confirmation when the AI agent books an appointment on behalf of a patient.",
        icon: CalendarCheck,
    },
}

export default function NotificationPreferences() {
    const [prefs, setPrefs] = useState<NotificationPreference[]>([])
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState<string | null>(null)

    useEffect(() => {
        loadPrefs()
    }, [])

    async function loadPrefs() {
        try {
            const data = await getNotificationPreferences()
            setPrefs(data)
        } catch {
            toast.error("Failed to load preferences")
        } finally {
            setLoading(false)
        }
    }

    async function handleToggle(templateType: string, enabled: boolean) {
        setSaving(templateType)

        // Optimistic update
        const prev = prefs
        setPrefs((p) =>
            p.map((item) =>
                item.template_type === templateType ? { ...item, is_enabled: enabled } : item,
            ),
        )

        try {
            const updated = await updateNotificationPreferences(
                prefs.map((item) =>
                    item.template_type === templateType ? { ...item, is_enabled: enabled } : item,
                ),
            )
            setPrefs(updated)
        } catch {
            setPrefs(prev)
            toast.error("Failed to update preference")
        } finally {
            setSaving(null)
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    return (
        <div className="p-6 max-w-2xl mx-auto space-y-6">
            <div>
                <h1 className="text-2xl font-bold tracking-tight">Email Preferences</h1>
                <p className="text-muted-foreground text-sm mt-1">
                    Choose which email notifications you receive. Changes apply to your account only.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Notification Types</CardTitle>
                    <CardDescription>
                        Toggle off any email type you don't want to receive.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-0 divide-y divide-border">
                    {prefs.map((pref) => {
                        const meta = TEMPLATE_META[pref.template_type]
                        if (!meta) return null
                        const Icon = meta.icon
                        const isSaving = saving === pref.template_type

                        return (
                            <div
                                key={pref.template_type}
                                className="flex items-center justify-between py-4 first:pt-0 last:pb-0"
                            >
                                <div className="flex items-start gap-3">
                                    <div className="mt-0.5 rounded-md bg-muted p-2">
                                        <Icon className="h-4 w-4 text-muted-foreground" />
                                    </div>
                                    <div className="space-y-0.5">
                                        <Label
                                            htmlFor={`toggle-${pref.template_type}`}
                                            className="text-sm font-medium cursor-pointer"
                                        >
                                            {meta.label}
                                        </Label>
                                        <p className="text-xs text-muted-foreground">
                                            {meta.description}
                                        </p>
                                    </div>
                                </div>
                                <Switch
                                    id={`toggle-${pref.template_type}`}
                                    checked={pref.is_enabled}
                                    disabled={isSaving}
                                    onCheckedChange={(checked) =>
                                        handleToggle(pref.template_type, checked)
                                    }
                                />
                            </div>
                        )
                    })}
                </CardContent>
            </Card>
        </div>
    )
}
