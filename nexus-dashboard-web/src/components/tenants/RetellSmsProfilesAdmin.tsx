import { useCallback, useEffect, useMemo, useState } from "react"
import { CheckCircle2, Loader2, Pencil, Plus, Power, Trash2, X, XCircle } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { listRetellAgents, verifyRetellAgent } from "@/lib/admin-api"
import {
    createRetellSmsChatProfile,
    deleteRetellSmsChatProfile,
    listRetellSmsChatProfiles,
    updateRetellSmsChatProfile,
} from "@/lib/retell-sms-api"
import type { RetellAgent, RetellSmsChatProfile } from "@/types"

type FormState = {
    displayName: string
    retellAgentId: string
    agentVersion: string
    purpose: string
    isActive: boolean
}

const EMPTY_FORM: FormState = {
    displayName: "",
    retellAgentId: "",
    agentVersion: "",
    purpose: "",
    isActive: true,
}

function fieldValue(value: string): string | null {
    const trimmed = value.trim()
    return trimmed.length ? trimmed : null
}

function formFromProfile(profile: RetellSmsChatProfile): FormState {
    return {
        displayName: profile.display_name,
        retellAgentId: profile.retell_agent_id ?? "",
        agentVersion: profile.agent_version === null ? "" : String(profile.agent_version),
        purpose: profile.purpose ?? "",
        isActive: profile.is_active,
    }
}

function apiErrorMessage(error: unknown, fallback: string): string {
    const err = error as { response?: { data?: { detail?: string } }; message?: string }
    return err?.response?.data?.detail || err?.message || fallback
}

function agentLabel(agent: RetellAgent): string {
    const details = [agent.channel, agent.is_published === true ? "published" : null]
        .filter(Boolean)
        .join(" · ")
    return `${agent.agent_name || agent.agent_id}${details ? ` — ${details}` : ""}`
}

export function RetellSmsProfilesAdmin({ locationId }: { locationId: string }) {
    const [profiles, setProfiles] = useState<RetellSmsChatProfile[]>([])
    const [agents, setAgents] = useState<RetellAgent[]>([])
    const [loading, setLoading] = useState(true)
    const [showForm, setShowForm] = useState(false)
    const [editingProfile, setEditingProfile] = useState<RetellSmsChatProfile | null>(null)
    const [form, setForm] = useState<FormState>(EMPTY_FORM)
    const [saving, setSaving] = useState(false)
    const [workingId, setWorkingId] = useState<string | null>(null)
    const [isVerifyingAgent, setIsVerifyingAgent] = useState(false)
    const [agentVerificationStatus, setAgentVerificationStatus] = useState<"idle" | "success" | "error">("idle")

    const activeCount = useMemo(
        () => profiles.filter((profile) => profile.is_active).length,
        [profiles],
    )

    const loadProfiles = useCallback(async () => {
        setLoading(true)
        try {
            setProfiles(await listRetellSmsChatProfiles({ locationId }))
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to load Retell SMS profiles"))
        } finally {
            setLoading(false)
        }
    }, [locationId])

    useEffect(() => {
        void loadProfiles()
        void listRetellAgents()
            .then(setAgents)
            .catch((error: unknown) => {
                toast.error(apiErrorMessage(error, "Failed to load Retell agents"))
            })
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

    function openEditForm(profile: RetellSmsChatProfile) {
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
        const displayName = fieldValue(form.displayName)
        const retellAgentId = fieldValue(form.retellAgentId)
        if (!displayName) {
            toast.error("Display name is required")
            return
        }
        if (!retellAgentId) {
            toast.error("Retell agent ID is required")
            return
        }

        const agentVersion = fieldValue(form.agentVersion)
        if (agentVersion !== null && (!/^\d+$/.test(agentVersion) || Number(agentVersion) < 0)) {
            toast.error("Agent version must be a non-negative whole number")
            return
        }

        setSaving(true)
        const payload = {
            retell_agent_id: retellAgentId,
            agent_version: agentVersion === null ? null : Number(agentVersion),
            display_name: displayName,
            purpose: fieldValue(form.purpose),
            is_active: form.isActive,
        }
        try {
            if (editingProfile) {
                await updateRetellSmsChatProfile(editingProfile.id, payload)
                toast.success("Retell SMS profile updated")
            } else {
                await createRetellSmsChatProfile({ ...payload, location_id: locationId })
                toast.success("Retell SMS profile created")
            }
            closeForm()
            await loadProfiles()
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to save Retell SMS profile"))
        } finally {
            setSaving(false)
        }
    }

    async function handleActiveChange(profile: RetellSmsChatProfile) {
        setWorkingId(profile.id)
        try {
            await updateRetellSmsChatProfile(profile.id, { is_active: !profile.is_active })
            toast.success(profile.is_active ? "Retell SMS profile deactivated" : "Retell SMS profile activated")
            await loadProfiles()
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to update Retell SMS profile"))
        } finally {
            setWorkingId(null)
        }
    }

    async function handleDelete(profile: RetellSmsChatProfile) {
        if (!window.confirm(`Delete "${profile.display_name}"? Profiles already used by a conversation must be deactivated instead.`)) return
        setWorkingId(profile.id)
        try {
            await deleteRetellSmsChatProfile(profile.id)
            toast.success("Retell SMS profile deleted")
            await loadProfiles()
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to delete Retell SMS profile"))
        } finally {
            setWorkingId(null)
        }
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <p className="text-sm font-medium">{activeCount} active profile{activeCount === 1 ? "" : "s"}</p>
                    <p className="text-xs text-muted-foreground">
                        Retell generates replies; this location&apos;s Twilio number sends and receives them.
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
                        <p className="text-sm font-semibold">{editingProfile ? "Edit Retell SMS profile" : "Add Retell SMS profile"}</p>
                        <Button type="button" variant="ghost" size="icon" aria-label="Close profile form" onClick={closeForm}>
                            <X className="h-4 w-4" />
                        </Button>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="retell-sms-display-name">Display name</Label>
                            <Input
                                id="retell-sms-display-name"
                                value={form.displayName}
                                placeholder="Appointment conversation agent"
                                onChange={(event) => updateForm({ displayName: event.target.value })}
                            />
                            <p className="text-xs text-muted-foreground">This is what workflow builders see.</p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="retell-sms-purpose">Purpose</Label>
                            <Input
                                id="retell-sms-purpose"
                                value={form.purpose}
                                placeholder="appointment_followup"
                                onChange={(event) => updateForm({ purpose: event.target.value })}
                            />
                            <p className="text-xs text-muted-foreground">Optional stable label; only one active profile per purpose.</p>
                        </div>

                        <div className="space-y-2 md:col-span-2">
                            <Label htmlFor="retell-sms-agent-id">Retell agent ID</Label>
                            <div className="flex items-center gap-2">
                                <Input
                                    id="retell-sms-agent-id"
                                    list={`retell-sms-agent-options-${locationId}`}
                                    value={form.retellAgentId}
                                    placeholder="agent_..."
                                    onChange={(event) => {
                                        updateForm({ retellAgentId: event.target.value })
                                        setAgentVerificationStatus("idle")
                                    }}
                                />
                                <datalist id={`retell-sms-agent-options-${locationId}`}>
                                    {agents.map((agent) => (
                                        <option key={agent.agent_id} value={agent.agent_id}>{agentLabel(agent)}</option>
                                    ))}
                                </datalist>
                                <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    className="shrink-0"
                                    disabled={!fieldValue(form.retellAgentId) || isVerifyingAgent}
                                    onClick={() => void handleVerifyAgent()}
                                >
                                    {isVerifyingAgent ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                                    Verify
                                </Button>
                            </div>
                            {agentVerificationStatus === "success" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-green-600">
                                    <CheckCircle2 className="h-4 w-4" /> Agent verified in Retell
                                </p>
                            )}
                            {agentVerificationStatus === "error" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-destructive">
                                    <XCircle className="h-4 w-4" /> Agent not found; check the ID
                                </p>
                            )}
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="retell-sms-agent-version">Pinned agent version</Label>
                            <Input
                                id="retell-sms-agent-version"
                                type="number"
                                min="0"
                                step="1"
                                value={form.agentVersion}
                                placeholder="Use Retell default"
                                onChange={(event) => updateForm({ agentVersion: event.target.value })}
                            />
                            <p className="text-xs text-muted-foreground">Leave blank to use the agent&apos;s current/default version.</p>
                        </div>

                        <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                            <div>
                                <Label htmlFor="retell-sms-active">Active</Label>
                                <p className="text-xs text-muted-foreground">Available for new workflow conversations.</p>
                            </div>
                            <Switch
                                id="retell-sms-active"
                                checked={form.isActive}
                                onCheckedChange={(checked) => updateForm({ isActive: checked })}
                            />
                        </div>

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

            <div className="rounded-lg border border-border bg-background/60">
                {loading ? (
                    <div className="p-4 text-sm text-muted-foreground">Loading Retell SMS profiles...</div>
                ) : profiles.length === 0 ? (
                    <div className="p-4 text-sm text-muted-foreground">No Retell SMS profiles configured for this location.</div>
                ) : (
                    <div className="divide-y divide-border">
                        {profiles.map((profile) => (
                            <div key={profile.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                                <div className="min-w-0">
                                    <div className="flex items-center gap-2">
                                        <p className="truncate font-medium">{profile.display_name}</p>
                                        <Badge variant={profile.is_active ? "default" : "secondary"}>
                                            {profile.is_active ? "Active" : "Inactive"}
                                        </Badge>
                                    </div>
                                    <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                                        {profile.retell_agent_id || "Agent ID unavailable"}
                                        {profile.agent_version === null ? "" : ` · version ${profile.agent_version}`}
                                    </p>
                                    {profile.purpose && <p className="mt-1 text-xs text-muted-foreground">Purpose: {profile.purpose}</p>}
                                </div>
                                <div className="flex gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        aria-label={`${profile.is_active ? "Deactivate" : "Activate"} ${profile.display_name}`}
                                        disabled={workingId === profile.id}
                                        onClick={() => void handleActiveChange(profile)}
                                    >
                                        {workingId === profile.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Power className="h-4 w-4" />}
                                        <span className="ml-1.5">{profile.is_active ? "Deactivate" : "Activate"}</span>
                                    </Button>
                                    <Button type="button" variant="outline" size="icon" aria-label={`Edit ${profile.display_name}`} onClick={() => openEditForm(profile)}>
                                        <Pencil className="h-4 w-4" />
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="icon"
                                        aria-label={`Delete ${profile.display_name}`}
                                        disabled={workingId === profile.id}
                                        onClick={() => void handleDelete(profile)}
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}
