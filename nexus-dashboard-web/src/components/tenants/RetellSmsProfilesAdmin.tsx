import { useCallback, useEffect, useMemo, useState } from "react"
import {
    Check,
    CheckCircle2,
    ChevronsUpDown,
    Loader2,
    Pencil,
    Plus,
    Power,
    Search,
    Trash2,
    X,
    XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { listRetellChatAgents, verifyRetellChatAgent } from "@/lib/admin-api"
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
    isActive: boolean
}

const EMPTY_FORM: FormState = {
    displayName: "",
    retellAgentId: "",
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
        isActive: profile.is_active,
    }
}

function apiErrorMessage(error: unknown, fallback: string): string {
    const err = error as { response?: { data?: { detail?: string } }; message?: string }
    return err?.response?.data?.detail || err?.message || fallback
}

function agentLabel(agent: RetellAgent): string {
    return agent.agent_name ? `${agent.agent_name} (${agent.agent_id})` : agent.agent_id
}

export function RetellSmsProfilesAdmin({ locationId }: { locationId: string }) {
    const [profiles, setProfiles] = useState<RetellSmsChatProfile[]>([])
    const [agents, setAgents] = useState<RetellAgent[]>([])
    const [loading, setLoading] = useState(true)
    const [loadingAgents, setLoadingAgents] = useState(false)
    const [showForm, setShowForm] = useState(false)
    const [editingProfile, setEditingProfile] = useState<RetellSmsChatProfile | null>(null)
    const [form, setForm] = useState<FormState>(EMPTY_FORM)
    const [saving, setSaving] = useState(false)
    const [workingId, setWorkingId] = useState<string | null>(null)
    const [isVerifyingAgent, setIsVerifyingAgent] = useState(false)
    const [agentVerificationStatus, setAgentVerificationStatus] = useState<"idle" | "success" | "error">("idle")
    const [agentPickerOpen, setAgentPickerOpen] = useState(false)
    const [agentSearch, setAgentSearch] = useState("")

    const activeCount = useMemo(
        () => profiles.filter((profile) => profile.is_active).length,
        [profiles],
    )

    const agentOptions = useMemo(() => {
        const selected = fieldValue(form.retellAgentId)
        const hasSelected = selected
            ? agents.some((agent) => agent.agent_id === selected)
            : true
        return selected && !hasSelected
            ? [
                {
                    agent_id: selected,
                    agent_name: "Current saved Chat Agent",
                    channel: "chat",
                    version: null,
                    is_published: null,
                },
                ...agents,
            ]
            : agents
    }, [agents, form.retellAgentId])
    const selectedAgentLabel = useMemo(() => {
        const selected = fieldValue(form.retellAgentId)
        if (!selected) return null
        const agent = agentOptions.find((item) => item.agent_id === selected)
        return agent ? agentLabel(agent) : selected
    }, [agentOptions, form.retellAgentId])
    const filteredAgents = useMemo(() => {
        const query = agentSearch.trim().toLowerCase()
        if (!query) return agentOptions
        return agentOptions.filter((agent) =>
            [agent.agent_id, agent.agent_name, agent.is_published ? "published" : "draft"]
                .filter(Boolean)
                .join(" ")
                .toLowerCase()
                .includes(query),
        )
    }, [agentOptions, agentSearch])
    const canUseTypedAgentId = useMemo(() => {
        const typed = fieldValue(agentSearch)
        return Boolean(typed && !agentOptions.some((agent) => agent.agent_id === typed))
    }, [agentOptions, agentSearch])

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
        setLoadingAgents(true)
        void listRetellChatAgents()
            .then(setAgents)
            .catch((error: unknown) => {
                toast.error(apiErrorMessage(error, "Failed to load Retell Chat Agents"))
            })
            .finally(() => setLoadingAgents(false))
    }, [loadProfiles])

    function updateForm(patch: Partial<FormState>) {
        setForm((current) => ({ ...current, ...patch }))
    }

    function openCreateForm() {
        setEditingProfile(null)
        setForm(EMPTY_FORM)
        setAgentVerificationStatus("idle")
        setAgentSearch("")
        setAgentPickerOpen(false)
        setShowForm(true)
    }

    function openEditForm(profile: RetellSmsChatProfile) {
        setEditingProfile(profile)
        setForm(formFromProfile(profile))
        setAgentVerificationStatus("idle")
        setAgentSearch("")
        setAgentPickerOpen(false)
        setShowForm(true)
    }

    function closeForm() {
        setEditingProfile(null)
        setForm(EMPTY_FORM)
        setAgentVerificationStatus("idle")
        setAgentSearch("")
        setAgentPickerOpen(false)
        setShowForm(false)
    }

    async function handleVerifyAgent() {
        const agentId = fieldValue(form.retellAgentId)
        if (!agentId) return
        setIsVerifyingAgent(true)
        setAgentVerificationStatus("idle")
        try {
            await verifyRetellChatAgent(agentId)
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

        setSaving(true)
        const payload = {
            retell_agent_id: retellAgentId,
            display_name: displayName,
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
                            <Label>Retell Chat Agent</Label>
                            <div className="flex items-center gap-2">
                                <Popover open={agentPickerOpen} onOpenChange={setAgentPickerOpen}>
                                    <PopoverTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="outline"
                                            aria-label="Retell Chat Agent"
                                            className={
                                                agentVerificationStatus === "success"
                                                    ? "h-11 min-w-0 flex-1 justify-between border-green-500/50 px-4 text-left font-normal ring-2 ring-green-500/50"
                                                    : agentVerificationStatus === "error"
                                                        ? "h-11 min-w-0 flex-1 justify-between border-destructive/50 px-4 text-left font-normal ring-2 ring-destructive/50"
                                                        : "h-11 min-w-0 flex-1 justify-between px-4 text-left font-normal"
                                            }
                                            disabled={loadingAgents || isVerifyingAgent}
                                        >
                                            <span className="min-w-0 truncate">
                                                {loadingAgents
                                                    ? "Loading Retell Chat Agents..."
                                                    : selectedAgentLabel || "Select Retell Chat Agent"}
                                            </span>
                                            <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                                        </Button>
                                    </PopoverTrigger>
                                    <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-0">
                                        <div className="border-b border-border p-2">
                                            <div className="relative">
                                                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                                <Input
                                                    value={agentSearch}
                                                    onChange={(event) => setAgentSearch(event.target.value)}
                                                    placeholder="Search or paste Chat Agent ID"
                                                    className="h-9 pl-8"
                                                />
                                            </div>
                                        </div>
                                        <div className="max-h-64 overflow-y-auto p-1">
                                            {filteredAgents.map((agent) => (
                                                <button
                                                    type="button"
                                                    key={agent.agent_id}
                                                    className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                                    onClick={() => {
                                                        updateForm({ retellAgentId: agent.agent_id })
                                                        setAgentVerificationStatus("idle")
                                                        setAgentSearch("")
                                                        setAgentPickerOpen(false)
                                                    }}
                                                >
                                                    <span className="min-w-0">
                                                        <span className="block truncate">{agentLabel(agent)}</span>
                                                        <span className="block truncate font-mono text-xs text-muted-foreground">
                                                            Chat Agent
                                                            {agent.is_published === true ? " - published" : ""}
                                                            {agent.is_published === false ? " - draft" : ""}
                                                        </span>
                                                    </span>
                                                    {form.retellAgentId === agent.agent_id && <Check className="h-4 w-4 shrink-0" />}
                                                </button>
                                            ))}
                                            {canUseTypedAgentId && (
                                                <button
                                                    type="button"
                                                    className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                                    onClick={() => {
                                                        const agentId = fieldValue(agentSearch)
                                                        if (!agentId) return
                                                        updateForm({ retellAgentId: agentId })
                                                        setAgentVerificationStatus("idle")
                                                        setAgentSearch("")
                                                        setAgentPickerOpen(false)
                                                    }}
                                                >
                                                    <span className="min-w-0">
                                                        <span className="block truncate">Use typed Chat Agent ID</span>
                                                        <span className="block truncate font-mono text-xs text-muted-foreground">
                                                            {agentSearch.trim()}
                                                        </span>
                                                    </span>
                                                </button>
                                            )}
                                            {filteredAgents.length === 0 && !canUseTypedAgentId && (
                                                <div className="px-2 py-6 text-center text-sm text-muted-foreground">
                                                    No Retell Chat Agents match your search.
                                                </div>
                                            )}
                                        </div>
                                    </PopoverContent>
                                </Popover>
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
                            {agentOptions.length === 0 && !loadingAgents && (
                                <p className="text-xs text-muted-foreground">
                                    No Retell Chat Agents found. Create and publish one in Retell, then refresh this page.
                                </p>
                            )}
                            {agentVerificationStatus === "success" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-green-600">
                                    <CheckCircle2 className="h-4 w-4" /> Chat Agent verified in Retell
                                </p>
                            )}
                            {agentVerificationStatus === "error" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-destructive">
                                    <XCircle className="h-4 w-4" /> Chat Agent not found; check the ID
                                </p>
                            )}
                            <p className="text-xs text-muted-foreground">
                                New conversations automatically use Retell&apos;s latest agent version.
                            </p>
                        </div>

                        <label className="flex items-center gap-2 text-sm font-medium md:col-span-2">
                            <Checkbox
                                checked={form.isActive}
                                onCheckedChange={(checked) => updateForm({ isActive: checked === true })}
                            />
                            Active
                            <span className="font-normal text-muted-foreground">
                                — available for new workflow conversations
                            </span>
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
                                    </p>
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
