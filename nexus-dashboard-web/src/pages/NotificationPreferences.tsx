import { useCallback, useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { toast } from "sonner"
import {
    getNotificationPreferences,
    updateNotificationPreferences,
    listExternalRecipients,
    addExternalRecipient,
    updateExternalRecipient,
    deleteExternalRecipient,
    type NotificationPreference,
    type ExternalRecipient,
} from "@/lib/notification-settings-api"
import { Phone, AlertTriangle, CalendarCheck, Loader2, Plus, Trash2, Bell } from "lucide-react"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import { Button } from "@/components/ui/button"

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
    const { user } = useAuth()
    const { pmsType } = useInstitution()
    // No-PMS clinics can't truly book — the AI captures a *request* staff book
    // manually — so the appointment toggle reads "Appointment Request" for them.
    const noPms = pmsType === "none"
    // External recipients are institution-wide config — the backend restricts
    // their CRUD to INSTITUTION_ADMIN, so only show/fetch that section for them.
    // (Per-user toggles below remain available to every role.)
    const isAdmin = user?.role === "INSTITUTION_ADMIN"

    const [prefs, setPrefs] = useState<NotificationPreference[]>([])
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState<string | null>(null)

    // External notification recipients state
    const [extRecipients, setExtRecipients] = useState<ExternalRecipient[]>([])
    const [extRecipientsLoading, setExtRecipientsLoading] = useState(false)
    const [extEmail, setExtEmail] = useState("")
    const [extTypes, setExtTypes] = useState<Record<string, boolean>>({
        call_summary: true,
        urgent_alert: true,
        appointment_confirmation: true,
    })
    const [extSaving, setExtSaving] = useState(false)

    const loadExtRecipients = useCallback(async () => {
        if (!isAdmin) return
        setExtRecipientsLoading(true)
        try {
            const rows = await listExternalRecipients()
            setExtRecipients(rows)
        } catch {
            toast.error("Failed to load external recipients")
        } finally {
            setExtRecipientsLoading(false)
        }
    }, [isAdmin])

    const loadPrefs = useCallback(async () => {
        try {
            const data = await getNotificationPreferences()
            setPrefs(data)
        } catch {
            toast.error("Failed to load preferences")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        loadPrefs()
        if (isAdmin) {
            void loadExtRecipients()
        }
    }, [loadPrefs, loadExtRecipients, isAdmin])

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

    async function handleAddExtRecipient() {
        const email = extEmail.trim().toLowerCase()
        if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
            toast.error("Enter a valid email address")
            return
        }
        const selectedTypes = Object.entries(extTypes)
            .filter(([, v]) => v)
            .map(([k]) => k)
        if (selectedTypes.length === 0) {
            toast.error("Select at least one notification type")
            return
        }
        setExtSaving(true)
        try {
            await addExternalRecipient({ email, template_types: selectedTypes })
            setExtEmail("")
            setExtTypes({ call_summary: true, urgent_alert: true, appointment_confirmation: true })
            await loadExtRecipients()
            toast.success(`Added ${email} as notification recipient`)
        } catch (err: unknown) {
            const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(message || "Failed to add recipient")
        } finally {
            setExtSaving(false)
        }
    }

    async function handleToggleExtRecipient(id: string, currentActive: boolean) {
        try {
            await updateExternalRecipient(id, { is_active: !currentActive })
            setExtRecipients((prev) =>
                prev.map((r) => (r.id === id ? { ...r, is_active: !currentActive } : r)),
            )
        } catch {
            toast.error("Failed to update recipient")
        }
    }

    async function handleDeleteExtRecipient(id: string) {
        try {
            await deleteExternalRecipient(id)
            setExtRecipients((prev) => prev.filter((r) => r.id !== id))
            toast.success("Recipient removed")
        } catch {
            toast.error("Failed to delete recipient")
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
        <div className="p-6 max-w-4xl mx-auto space-y-6">
            <div>
                <h1 className="text-2xl font-bold tracking-tight">Email Preferences</h1>
                <p className="text-muted-foreground text-sm mt-1">
                    Manage how you and others receive email notifications.
                </p>
            </div>

            <div className="grid gap-6 md:grid-cols-2">
                {/* Personal Preferences */}
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Personal Notifications</CardTitle>
                        <CardDescription>
                            Choose which notifications you receive on your account ({user?.email}).
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-0 divide-y divide-border">
                        {prefs.map((pref) => {
                            let meta = TEMPLATE_META[pref.template_type]
                            if (noPms && pref.template_type === "appointment_confirmation") {
                                meta = {
                                    ...meta,
                                    label: "Appointment Request",
                                    description:
                                        "Get notified when the AI agent captures an appointment request to book manually in your system.",
                                }
                            }
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
                                            <p className="text-[10px] leading-relaxed text-muted-foreground">
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

                {/* External Recipients Section */}
                {isAdmin && (
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <Bell className="h-4 w-4" />
                                External Recipients
                            </CardTitle>
                            <CardDescription>
                                Notify people outside the platform (e.g. personal emails, office managers).
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {/* Add form */}
                            <div className="flex flex-col gap-3 p-3 border border-border rounded-lg bg-muted/30">
                                <div className="flex gap-2">
                                    <Input
                                        type="email"
                                        placeholder="email@example.com"
                                        value={extEmail}
                                        onChange={(e) => setExtEmail(e.target.value)}
                                        className="flex-1 h-8 text-sm"
                                    />
                                    <Button
                                        size="sm"
                                        onClick={handleAddExtRecipient}
                                        disabled={extSaving || !extEmail.trim()}
                                    >
                                        {extSaving ? (
                                            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                                        ) : (
                                            <Plus className="h-3 w-3 mr-1" />
                                        )}
                                        Add
                                    </Button>
                                </div>
                                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px]">
                                    {([
                                        ["call_summary", "Call Summary"],
                                        ["urgent_alert", "Urgent Alert"],
                                        ["appointment_confirmation", "Appt."],
                                    ] as const).map(([key, label]) => (
                                        <label
                                            key={key}
                                            className="flex items-center gap-1.5 cursor-pointer"
                                        >
                                            <Checkbox
                                                checked={extTypes[key]}
                                                onCheckedChange={(checked) =>
                                                    setExtTypes((prev) => ({
                                                        ...prev,
                                                        [key]: !!checked,
                                                    }))
                                                }
                                            />
                                            <span>{label}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>

                            {/* List */}
                            {extRecipientsLoading ? (
                                <div className="flex justify-center py-4">
                                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                                </div>
                            ) : extRecipients.length === 0 ? (
                                <p className="text-[11px] text-muted-foreground text-center py-4">
                                    No external recipients configured.
                                </p>
                            ) : (
                                <div className="max-h-[300px] overflow-auto">
                                    <Table>
                                        <TableHeader className="hidden">
                                            <TableRow>
                                                <TableHead>Email</TableHead>
                                                <TableHead>Type</TableHead>
                                                <TableHead>Active</TableHead>
                                                <TableHead />
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {extRecipients.map((r) => (
                                                <TableRow key={r.id} className="hover:bg-transparent">
                                                    <TableCell className="p-2 py-3">
                                                        <div className="space-y-1">
                                                            <div className="text-xs font-medium truncate max-w-[140px]">
                                                                {r.email}
                                                            </div>
                                                            <Badge
                                                                variant="outline"
                                                                className="text-[9px] h-4 px-1 uppercase tracking-tighter"
                                                            >
                                                                {r.template_type.replace(/_/g, " ")}
                                                            </Badge>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="p-2 text-right">
                                                        <div className="flex items-center justify-end gap-2">
                                                            <Switch
                                                                className="scale-75 h-4 w-7"
                                                                checked={r.is_active}
                                                                onCheckedChange={() =>
                                                                    handleToggleExtRecipient(
                                                                        r.id,
                                                                        r.is_active,
                                                                    )
                                                                }
                                                            />
                                                            <Button
                                                                variant="ghost"
                                                                size="icon"
                                                                className="h-7 w-7"
                                                                onClick={() =>
                                                                    handleDeleteExtRecipient(r.id)
                                                                }
                                                            >
                                                                <Trash2 className="h-3 w-3 text-muted-foreground hover:text-red-500" />
                                                            </Button>
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    )
}
