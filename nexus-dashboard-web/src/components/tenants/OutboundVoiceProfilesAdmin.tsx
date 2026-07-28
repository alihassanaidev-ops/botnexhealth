import { useCallback, useEffect, useMemo, useState } from "react"
import { CheckCircle2, Loader2, Pencil, Plus, Trash2, X, XCircle } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    createAdminOutboundVoiceProfile,
    deleteAdminOutboundVoiceProfile,
    listAdminOutboundVoiceProfiles,
    updateAdminOutboundVoiceProfile,
    verifyRetellAgent,
} from "@/lib/admin-api"
import type { OutboundVoiceProfile } from "@/types"

type FormState = {
    displayName: string
    retellAgentId: string
    isActive: boolean
}

const EMPTY_FORM: FormState = {
    displayName: "",
    retellAgentId: "",
    isActive: true,
}

function fieldValue(value: string) {
    const trimmed = value.trim()
    return trimmed.length ? trimmed : null
}

function formFromProfile(profile: OutboundVoiceProfile): FormState {
    return {
        displayName: profile.display_name ?? "",
        retellAgentId: profile.retell_agent_id ?? "",
        isActive: profile.is_active,
    }
}

function apiErrorMessage(error: unknown, fallback: string) {
    const err = error as { response?: { data?: { detail?: string } }; message?: string }
    return err?.response?.data?.detail || err?.message || fallback
}

export function OutboundVoiceProfilesAdmin({
    institutionSlug,
    locationSlug,
}: {
    institutionSlug: string
    locationSlug: string
}) {
    const [profiles, setProfiles] = useState<OutboundVoiceProfile[]>([])
    const [loading, setLoading] = useState(true)
    const [showForm, setShowForm] = useState(false)
    const [editingProfile, setEditingProfile] = useState<OutboundVoiceProfile | null>(null)
    const [saving, setSaving] = useState(false)
    const [deletingId, setDeletingId] = useState<string | null>(null)
    const [form, setForm] = useState<FormState>(EMPTY_FORM)
    const [isVerifyingAgent, setIsVerifyingAgent] = useState(false)
    const [agentVerificationStatus, setAgentVerificationStatus] = useState<"idle" | "success" | "error">("idle")

    const activeCount = useMemo(() => profiles.filter((profile) => profile.is_active).length, [profiles])

    const loadProfiles = useCallback(async () => {
        setLoading(true)
        try {
            setProfiles(await listAdminOutboundVoiceProfiles(institutionSlug, locationSlug))
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to load outbound voice profiles"))
        } finally {
            setLoading(false)
        }
    }, [institutionSlug, locationSlug])

    useEffect(() => {
        void loadProfiles()
    }, [loadProfiles])

    function updateForm(patch: Partial<FormState>) {
        setForm((current) => ({ ...current, ...patch }))
    }

    function openCreateForm() {
        setEditingProfile(null)
        setForm(EMPTY_FORM)
        setAgentVerificationStatus("idle")
        setShowForm(true)
    }

    function openEditForm(profile: OutboundVoiceProfile) {
        setEditingProfile(profile)
        setForm(formFromProfile(profile))
        setAgentVerificationStatus("idle")
        setShowForm(true)
    }

    function closeForm() {
        setEditingProfile(null)
        setForm(EMPTY_FORM)
        setAgentVerificationStatus("idle")
        setShowForm(false)
    }

    async function handleVerifyAgent() {
        const agentId = fieldValue(form.retellAgentId)
        if (!agentId) return
        setIsVerifyingAgent(true)
        setAgentVerificationStatus("idle")
        try {
            await verifyRetellAgent(agentId)
            setAgentVerificationStatus("success")
        } catch {
            setAgentVerificationStatus("error")
        } finally {
            setIsVerifyingAgent(false)
        }
    }

    async function handleSubmit() {
        if (!fieldValue(form.displayName)) {
            toast.error("Display name is required")
            return
        }
        if (!fieldValue(form.retellAgentId)) {
            toast.error("Retell agent ID is required")
            return
        }

        setSaving(true)
        try {
            const payload = {
                display_name: fieldValue(form.displayName),
                retell_agent_id: fieldValue(form.retellAgentId),
                is_active: form.isActive,
            }
            if (editingProfile) {
                await updateAdminOutboundVoiceProfile(institutionSlug, locationSlug, editingProfile.id, payload)
                toast.success("Outbound voice profile updated")
            } else {
                await createAdminOutboundVoiceProfile(institutionSlug, locationSlug, payload)
                toast.success("Outbound voice profile created")
            }
            closeForm()
            await loadProfiles()
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to save outbound voice profile"))
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(profile: OutboundVoiceProfile) {
        if (!window.confirm(`Delete "${profile.display_name || "Unnamed voice profile"}"?`)) return
        setDeletingId(profile.id)
        try {
            await deleteAdminOutboundVoiceProfile(institutionSlug, locationSlug, profile.id)
            toast.success("Outbound voice profile deleted")
            await loadProfiles()
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to delete outbound voice profile"))
        } finally {
            setDeletingId(null)
        }
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <p className="text-sm font-medium">{activeCount} active profile{activeCount === 1 ? "" : "s"}</p>
                    <p className="text-xs text-muted-foreground">
                        Technical outbound agents. Clinics only see display names in workflows.
                    </p>
                </div>
                <Button type="button" size="sm" onClick={openCreateForm}>
                    <Plus className="mr-1.5 h-3.5 w-3.5" />
                    Add profile
                </Button>
            </div>

            {showForm && (
                <div className="rounded-lg border border-border bg-background/70 p-4">
                    <div className="mb-4 flex items-center justify-between">
                        <p className="text-sm font-semibold">{editingProfile ? "Edit profile" : "Add profile"}</p>
                        <Button type="button" variant="ghost" size="icon" onClick={closeForm}>
                            <X className="h-4 w-4" />
                        </Button>
                    </div>
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label>Display name</Label>
                            <Input value={form.displayName} onChange={(event) => updateForm({ displayName: event.target.value })} />
                        </div>
                        <div className="space-y-2">
                            <Label>Retell agent ID</Label>
                            <div className="flex items-center gap-2">
                                <Input
                                    value={form.retellAgentId}
                                    disabled={isVerifyingAgent}
                                    className={
                                        agentVerificationStatus === "success"
                                            ? "border-green-500/50 ring-2 ring-green-500/50"
                                            : agentVerificationStatus === "error"
                                                ? "border-destructive/50 ring-2 ring-destructive/50"
                                                : undefined
                                    }
                                    onChange={(event) => {
                                        updateForm({ retellAgentId: event.target.value })
                                        setAgentVerificationStatus("idle")
                                    }}
                                />
                                <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    className="shrink-0"
                                    disabled={!fieldValue(form.retellAgentId) || isVerifyingAgent}
                                    onClick={() => void handleVerifyAgent()}
                                >
                                    {isVerifyingAgent ? (
                                        <>
                                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                            Verifying...
                                        </>
                                    ) : "Verify"}
                                </Button>
                            </div>
                            {agentVerificationStatus === "success" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-green-600">
                                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                                    Agent verified - this ID is active in Retell
                                </p>
                            )}
                            {agentVerificationStatus === "error" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-destructive">
                                    <XCircle className="h-4 w-4 shrink-0" />
                                    Agent not found - check the ID and try again
                                </p>
                            )}
                        </div>
                        <label className="flex items-center gap-2 pt-7 text-sm font-medium">
                            <Checkbox checked={form.isActive} onCheckedChange={(checked) => updateForm({ isActive: checked === true })} />
                            Active
                        </label>
                        <div className="flex gap-2 md:col-span-2">
                            <Button type="button" onClick={() => void handleSubmit()} disabled={saving}>
                                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                {editingProfile ? "Save profile" : "Create profile"}
                            </Button>
                            <Button type="button" variant="outline" onClick={closeForm} disabled={saving}>Cancel</Button>
                        </div>
                    </div>
                </div>
            )}

            <div className="divide-y divide-border rounded-lg border border-border bg-background/60">
                {loading ? (
                    <div className="p-4 text-sm text-muted-foreground">Loading outbound voice profiles...</div>
                ) : profiles.length === 0 ? (
                    <div className="p-4 text-sm text-muted-foreground">No outbound voice profiles configured.</div>
                ) : profiles.map((profile) => (
                    <div key={profile.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                        <div className="min-w-0">
                            <div className="flex items-center gap-2">
                                <p className="font-medium">{profile.display_name || "Unnamed voice profile"}</p>
                                <Badge variant={profile.is_active ? "default" : "secondary"}>{profile.is_active ? "Active" : "Inactive"}</Badge>
                            </div>
                            <p className="mt-1 font-mono text-xs text-muted-foreground">
                                {profile.retell_agent_id || "missing agent"}
                            </p>
                        </div>
                        <div className="flex gap-2">
                            <Button type="button" variant="outline" size="icon" onClick={() => openEditForm(profile)}>
                                <Pencil className="h-4 w-4" />
                            </Button>
                            <Button type="button" variant="outline" size="icon" onClick={() => void handleDelete(profile)} disabled={deletingId === profile.id}>
                                {deletingId === profile.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                            </Button>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}
