import { useCallback, useEffect, useMemo, useState } from "react"
import { Check, CheckCircle2, ChevronsUpDown, Loader2, Pencil, Plus, Search, Trash2, X, XCircle } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/foundation/compat/badge"
import { Button } from "@/components/foundation/compat/button"
import { Checkbox } from "@/components/foundation/compat/checkbox"
import { Input } from "@/components/foundation/compat/input"
import { Label } from "@/components/foundation/compat/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/foundation/compat/popover"
import {
    createAdminOutboundVoiceProfile,
    deleteAdminOutboundVoiceProfile,
    listRetellAgents,
    listRetellPhoneNumbers,
    listAdminOutboundVoiceProfiles,
    updateAdminOutboundVoiceProfile,
    verifyRetellAgent,
} from "@/lib/admin-api"
import type { OutboundVoiceProfile, RetellAgent, RetellPhoneNumber } from "@/types"

type FormState = {
    displayName: string
    retellAgentId: string
    retellFromNumber: string
    isActive: boolean
}

const EMPTY_FORM: FormState = {
    displayName: "",
    retellAgentId: "",
    retellFromNumber: "",
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
        retellFromNumber: profile.retell_from_number ?? "",
        isActive: profile.is_active,
    }
}

function phoneLabel(number: RetellPhoneNumber) {
    const display = number.phone_number_pretty || number.phone_number
    return number.nickname ? `${number.nickname} (${display})` : display
}

function agentLabel(agent: RetellAgent) {
    return agent.agent_name ? `${agent.agent_name} (${agent.agent_id})` : agent.agent_id
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
    const [loadingAgents, setLoadingAgents] = useState(false)
    const [loadingNumbers, setLoadingNumbers] = useState(false)
    const [showForm, setShowForm] = useState(false)
    const [editingProfile, setEditingProfile] = useState<OutboundVoiceProfile | null>(null)
    const [agents, setAgents] = useState<RetellAgent[]>([])
    const [phoneNumbers, setPhoneNumbers] = useState<RetellPhoneNumber[]>([])
    const [saving, setSaving] = useState(false)
    const [deletingId, setDeletingId] = useState<string | null>(null)
    const [form, setForm] = useState<FormState>(EMPTY_FORM)
    const [isVerifyingAgent, setIsVerifyingAgent] = useState(false)
    const [agentVerificationStatus, setAgentVerificationStatus] = useState<"idle" | "success" | "error">("idle")
    const [agentPickerOpen, setAgentPickerOpen] = useState(false)
    const [agentSearch, setAgentSearch] = useState("")
    const [phonePickerOpen, setPhonePickerOpen] = useState(false)
    const [phoneNumberSearch, setPhoneNumberSearch] = useState("")
    const [profileSearch, setProfileSearch] = useState("")

    const activeCount = useMemo(() => profiles.filter((profile) => profile.is_active).length, [profiles])
    const agentOptions = useMemo(() => {
        const selected = fieldValue(form.retellAgentId)
        const hasSelected = selected
            ? agents.some((agent) => agent.agent_id === selected)
            : true
        return selected && !hasSelected
            ? [
                {
                    agent_id: selected,
                    agent_name: "Current saved agent",
                    channel: null,
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
            [
                agent.agent_id,
                agent.agent_name,
                agent.channel,
                agent.is_published === true ? "published" : null,
                agent.is_published === false ? "draft" : null,
            ]
                .filter(Boolean)
                .join(" ")
                .toLowerCase()
                .includes(query),
        )
    }, [agentOptions, agentSearch])
    const canUseTypedAgentId = useMemo(() => {
        const typed = fieldValue(agentSearch)
        if (!typed) return false
        return !agentOptions.some((agent) => agent.agent_id === typed)
    }, [agentOptions, agentSearch])
    const phoneNumberOptions = useMemo(() => {
        const selected = fieldValue(form.retellFromNumber)
        const hasSelected = selected
            ? phoneNumbers.some((number) => number.phone_number === selected)
            : true
        return selected && !hasSelected
            ? [
                {
                    phone_number: selected,
                    phone_number_pretty: selected,
                    nickname: "Current saved number",
                    phone_number_type: null,
                    inbound_agents: null,
                    outbound_agents: null,
                },
                ...phoneNumbers,
            ]
            : phoneNumbers
    }, [form.retellFromNumber, phoneNumbers])
    const selectedPhoneLabel = useMemo(() => {
        const selected = fieldValue(form.retellFromNumber)
        if (!selected) return null
        const number = phoneNumberOptions.find((item) => item.phone_number === selected)
        return number ? phoneLabel(number) : selected
    }, [form.retellFromNumber, phoneNumberOptions])
    const filteredPhoneNumbers = useMemo(() => {
        const query = phoneNumberSearch.trim().toLowerCase()
        if (!query) return phoneNumberOptions

        return phoneNumberOptions.filter((number) =>
            [
                number.phone_number,
                number.phone_number_pretty,
                number.nickname,
                number.phone_number_type,
            ]
                .filter(Boolean)
                .join(" ")
                .toLowerCase()
                .includes(query),
        )
    }, [phoneNumberOptions, phoneNumberSearch])
    const filteredProfiles = useMemo(() => {
        const query = profileSearch.trim().toLowerCase()
        if (!query) return profiles

        return profiles.filter((profile) =>
            [
                profile.display_name,
                profile.retell_agent_id,
                profile.retell_from_number,
                profile.is_active ? "active" : "inactive",
            ]
                .filter(Boolean)
                .join(" ")
                .toLowerCase()
                .includes(query),
        )
    }, [profiles, profileSearch])

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

    const loadAgents = useCallback(async () => {
        setLoadingAgents(true)
        try {
            setAgents(await listRetellAgents())
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to load Retell agents"))
        } finally {
            setLoadingAgents(false)
        }
    }, [])

    const loadPhoneNumbers = useCallback(async () => {
        setLoadingNumbers(true)
        try {
            setPhoneNumbers(await listRetellPhoneNumbers())
        } catch (error: unknown) {
            toast.error(apiErrorMessage(error, "Failed to load Retell phone numbers"))
        } finally {
            setLoadingNumbers(false)
        }
    }, [])

    useEffect(() => {
        void loadProfiles()
    }, [loadProfiles])

    useEffect(() => {
        void loadAgents()
    }, [loadAgents])

    useEffect(() => {
        void loadPhoneNumbers()
    }, [loadPhoneNumbers])

    function updateForm(patch: Partial<FormState>) {
        setForm((current) => ({ ...current, ...patch }))
    }

    function openCreateForm() {
        setEditingProfile(null)
        setForm(EMPTY_FORM)
        setAgentVerificationStatus("idle")
        setAgentSearch("")
        setAgentPickerOpen(false)
        setPhoneNumberSearch("")
        setPhonePickerOpen(false)
        setShowForm(true)
    }

    function openEditForm(profile: OutboundVoiceProfile) {
        setEditingProfile(profile)
        setForm(formFromProfile(profile))
        setAgentVerificationStatus("idle")
        setAgentSearch("")
        setAgentPickerOpen(false)
        setPhoneNumberSearch("")
        setPhonePickerOpen(false)
        setShowForm(true)
    }

    function closeForm() {
        setEditingProfile(null)
        setForm(EMPTY_FORM)
        setAgentVerificationStatus("idle")
        setAgentSearch("")
        setAgentPickerOpen(false)
        setPhoneNumberSearch("")
        setPhonePickerOpen(false)
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
        if (!fieldValue(form.retellFromNumber)) {
            toast.error("Retell from number is required")
            return
        }

        setSaving(true)
        try {
            const payload = {
                display_name: fieldValue(form.displayName),
                retell_agent_id: fieldValue(form.retellAgentId),
                retell_from_number: fieldValue(form.retellFromNumber),
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
                                <Popover open={agentPickerOpen} onOpenChange={setAgentPickerOpen}>
                                    <PopoverTrigger asChild>
                                        <Button
                                            type="button"
                                            size="lg"
                                            variant="outline"
                                            className={
                                                agentVerificationStatus === "success"
                                                    ? " min-w-0 flex-1 justify-between border-green-500/50 px-4 text-left font-normal ring-2 ring-green-500/50"
                                                    : agentVerificationStatus === "error"
                                                        ? " min-w-0 flex-1 justify-between border-destructive/50 px-4 text-left font-normal ring-2 ring-destructive/50"
                                                        : " min-w-0 flex-1 justify-between px-4 text-left font-normal"
                                            }
                                            disabled={loadingAgents || isVerifyingAgent}
                                        >
                                            <span className="min-w-0 truncate">
                                                {loadingAgents
                                                    ? "Loading Retell agents..."
                                                    : selectedAgentLabel || "Select Retell agent"}
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
                                                    placeholder="Search or paste Retell agent ID"
                                                    className="h-9 pl-8"
                                                />
                                            </div>
                                        </div>
                                        <div className="max-h-64 overflow-y-auto p-1">
                                            <button
                                                type="button"
                                                className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                                onClick={() => {
                                                    updateForm({ retellAgentId: "" })
                                                    setAgentVerificationStatus("idle")
                                                    setAgentSearch("")
                                                    setAgentPickerOpen(false)
                                                }}
                                            >
                                                <span className="min-w-0 truncate text-muted-foreground">Select Retell agent</span>
                                                {!form.retellAgentId && <Check className="h-4 w-4 shrink-0" />}
                                            </button>
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
                                                            {agent.channel || "voice agent"}
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
                                                        <span className="block truncate">Use typed agent ID</span>
                                                        <span className="block truncate font-mono text-xs text-muted-foreground">
                                                            {agentSearch.trim()}
                                                        </span>
                                                    </span>
                                                </button>
                                            )}
                                            {filteredAgents.length === 0 && !canUseTypedAgentId && (
                                                <div className="px-2 py-6 text-center text-sm text-muted-foreground">
                                                    No Retell agents match your search.
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
                                    {isVerifyingAgent ? (
                                        <>
                                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                            Verifying...
                                        </>
                                    ) : "Verify"}
                                </Button>
                            </div>
                            {agentOptions.length === 0 && !loadingAgents && (
                                <p className="text-xs text-muted-foreground">
                                    No Retell voice agents found for this account. Paste an agent ID in the search box to use it manually.
                                </p>
                            )}
                            {agentVerificationStatus === "success" && (
                                <p className="flex items-center gap-1.5 text-sm font-medium text-green-600 dark:text-green-400">
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
                        <div className="space-y-2">
                            <Label>Retell from number</Label>
                            <Popover open={phonePickerOpen} onOpenChange={setPhonePickerOpen}>
                                <PopoverTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="lg"
                                        className="w-full justify-between px-4 text-left font-normal"
                                        disabled={loadingNumbers}
                                    >
                                        <span className="min-w-0 truncate">
                                            {loadingNumbers
                                                ? "Loading Retell numbers..."
                                                : selectedPhoneLabel || "Select Retell number"}
                                        </span>
                                        <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                                    </Button>
                                </PopoverTrigger>
                                <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-0">
                                    <div className="border-b border-border p-2">
                                        <div className="relative">
                                            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                            <Input
                                                value={phoneNumberSearch}
                                                onChange={(event) => setPhoneNumberSearch(event.target.value)}
                                                placeholder="Search Retell numbers"
                                                className="h-9 pl-8"
                                            />
                                        </div>
                                    </div>
                                    <div className="max-h-64 overflow-y-auto p-1">
                                        <button
                                            type="button"
                                            className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                            onClick={() => {
                                                updateForm({ retellFromNumber: "" })
                                                setPhoneNumberSearch("")
                                                setPhonePickerOpen(false)
                                            }}
                                        >
                                            <span className="min-w-0 truncate text-muted-foreground">Select Retell number</span>
                                            {!form.retellFromNumber && <Check className="h-4 w-4 shrink-0" />}
                                        </button>
                                        {filteredPhoneNumbers.map((number) => (
                                            <button
                                                type="button"
                                                key={number.phone_number}
                                                className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                                onClick={() => {
                                                    updateForm({ retellFromNumber: number.phone_number })
                                                    setPhoneNumberSearch("")
                                                    setPhonePickerOpen(false)
                                                }}
                                            >
                                                <span className="min-w-0">
                                                    <span className="block truncate">{phoneLabel(number)}</span>
                                                    {number.phone_number_pretty && number.phone_number_pretty !== number.phone_number && (
                                                        <span className="block truncate font-mono text-xs text-muted-foreground">
                                                            {number.phone_number}
                                                        </span>
                                                    )}
                                                </span>
                                                {form.retellFromNumber === number.phone_number && <Check className="h-4 w-4 shrink-0" />}
                                            </button>
                                        ))}
                                        {filteredPhoneNumbers.length === 0 && (
                                            <div className="px-2 py-6 text-center text-sm text-muted-foreground">
                                                No Retell numbers match your search.
                                            </div>
                                        )}
                                    </div>
                                </PopoverContent>
                            </Popover>
                            {phoneNumberOptions.length === 0 && !loadingNumbers && (
                                <p className="text-xs text-muted-foreground">
                                    No Retell phone numbers found for this account.
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

            <div className="rounded-lg border border-border bg-background/60">
                <div className="border-b border-border p-3">
                    <div className="relative max-w-md">
                        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <Input
                            value={profileSearch}
                            onChange={(event) => setProfileSearch(event.target.value)}
                            placeholder="Search outbound profiles"
                            className="h-9 pl-8"
                        />
                    </div>
                </div>
                {loading ? (
                    <div className="p-4 text-sm text-muted-foreground">Loading outbound voice profiles...</div>
                ) : profiles.length === 0 ? (
                    <div className="p-4 text-sm text-muted-foreground">No outbound voice profiles configured.</div>
                ) : filteredProfiles.length === 0 ? (
                    <div className="p-4 text-sm text-muted-foreground">No outbound voice profiles match your search.</div>
                ) : (
                    <div className="max-h-72 divide-y divide-border overflow-y-auto">
                        {filteredProfiles.map((profile) => (
                            <div key={profile.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                                <div className="min-w-0">
                                    <div className="flex items-center gap-2">
                                        <p className="truncate font-medium">{profile.display_name || "Unnamed voice profile"}</p>
                                        <Badge variant={profile.is_active ? "default" : "secondary"}>{profile.is_active ? "Active" : "Inactive"}</Badge>
                                    </div>
                                    <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                                        {profile.retell_agent_id || "missing agent"}
                                    </p>
                                    <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                                        From: {profile.retell_from_number || "missing Retell number"}
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
                )}
            </div>
        </div>
    )
}
