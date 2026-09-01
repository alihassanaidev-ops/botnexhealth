/** Campaign template picker + guided clone flow. */
import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft, CheckCircle2, LayoutTemplate, Loader2, Sparkles } from "lucide-react"
import { Badge } from "@/components/foundation/compat/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/foundation/compat/card"
import { Button } from "@/components/foundation/compat/button"
import { Checkbox } from "@/components/foundation/compat/checkbox"
import { Input } from "@/components/foundation/compat/input"
import { Skeleton } from "@/components/foundation/compat/skeleton"
import { Label } from "@/components/foundation/compat/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/foundation/compat/select"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/foundation/compat/dialog"
import { toast } from "sonner"
import { createWorkflowFromTemplate, listTemplates, type CampaignTemplate } from "@/lib/workflow-api"
import { triggerTypeLabel } from "@/lib/workflow/catalog"
import { listAppointmentTypes, listLocations } from "@/lib/tenant-api"
import { listOutboundVoiceProfiles } from "@/lib/outbound-voice-api"
import type { CachedAppointmentType, LocationInfo, OutboundVoiceProfile } from "@/types"
import type { TriggerType } from "@/types/workflow"
import { PageHeader } from "@/components/PageHeader"
import campaignsIcon from "@/assets/icons/presentation/campaigns-outlined.png"

const CATEGORY_LABELS: Record<string, string> = {
    appointment_ops: "Appointment ops",
    callback: "Callback",
    recall: "Recall",
    reactivation: "Reactivation",
    treatment: "Treatment",
}

const CATEGORY_ORDER = ["appointment_ops", "callback", "recall", "reactivation", "treatment"]

function label(value: string) {
    return CATEGORY_LABELS[value] ?? value.replace(/_/g, " ")
}

function requiresVoiceProfile(template: CampaignTemplate) {
    return template.metadata.setup_fields.some(
        (field) => ["voice_profile_id", "voice_agent_id"].includes(field.id) && field.required,
    )
}

function requiresAppointmentTypes(template: CampaignTemplate) {
    return template.metadata.setup_fields.some(
        (field) => field.id === "appointment_type_ids" && field.required,
    )
}

function hasSetupField(template: CampaignTemplate, fieldId: string) {
    return template.metadata.setup_fields.some((field) => field.id === fieldId)
}

function pmsCapabilityStatus(template: CampaignTemplate) {
    return template.metadata.pms_capability_evaluation
}

function isPmsUnsupported(template: CampaignTemplate) {
    const evaluation = pmsCapabilityStatus(template)
    return evaluation ? !evaluation.supported : false
}

function pmsBadgeLabel(template: CampaignTemplate) {
    const requirements = template.metadata.pms_capability_requirements
    if (requirements.length === 0) return null
    const evaluation = pmsCapabilityStatus(template)
    if (!evaluation) return "PMS gated"
    if (evaluation.supported) return "PMS ready"
    return "Unsupported"
}

function setupFieldOptions(template: CampaignTemplate, fieldId: string, fallback: string): string[] {
    const field = template.metadata.setup_fields.find((item) => item.id === fieldId)
    const values = field?.options?.length ? field.options : [field?.default ?? fallback]
    return Array.from(new Set(values.map((value) => String(value).trim()).filter(Boolean)))
}

function setupFieldDefault(template: CampaignTemplate, fieldId: string, fallback: string): string {
    const field = template.metadata.setup_fields.find((item) => item.id === fieldId)
    return String(field?.default ?? fallback).trim()
}

function isNonNegativeWholeNumber(value: string): boolean {
    const normalized = value.trim()
    if (!normalized) return false
    const parsed = Number(normalized)
    return Number.isInteger(parsed) && parsed >= 0
}

function isPositiveWholeNumber(value: string): boolean {
    const normalized = value.trim()
    if (!normalized) return false
    const parsed = Number(normalized)
    return Number.isInteger(parsed) && parsed > 0
}

export default function WorkflowTemplates() {
    const navigate = useNavigate()
    const [templates, setTemplates] = useState<CampaignTemplate[]>([])
    const [locations, setLocations] = useState<LocationInfo[]>([])
    const [appointmentTypes, setAppointmentTypes] = useState<CachedAppointmentType[]>([])
    const [voiceProfiles, setVoiceProfiles] = useState<OutboundVoiceProfile[]>([])
    const [appointmentTypesLocationId, setAppointmentTypesLocationId] = useState<string | null>(null)
    const [appointmentTypesLoading, setAppointmentTypesLoading] = useState(false)
    const [voiceProfilesLoading, setVoiceProfilesLoading] = useState(false)
    const [loading, setLoading] = useState(true)
    const [picked, setPicked] = useState<CampaignTemplate | null>(null)
    const [name, setName] = useState("")
    const [selectedLocationId, setSelectedLocationId] = useState("")
    const [audienceSource, setAudienceSource] = useState("")
    const [channelSequence, setChannelSequence] = useState("")
    const [copyVariant, setCopyVariant] = useState("")
    const [handoffBehavior, setHandoffBehavior] = useState("")
    const [voiceProfileId, setVoiceProfileId] = useState("")
    const [appointmentTypeIds, setAppointmentTypeIds] = useState<string[]>([])
    const [appointmentReasons, setAppointmentReasons] = useState("")
    const [postOpReasons, setPostOpReasons] = useState("")
    const [callOffsetHoursBefore, setCallOffsetHoursBefore] = useState("24")
    const [retryDelay1Hours, setRetryDelay1Hours] = useState("5")
    const [retryDelay2Hours, setRetryDelay2Hours] = useState("5")
    const [postOpDelayHours, setPostOpDelayHours] = useState("24")
    const [postOpLatestCallHours, setPostOpLatestCallHours] = useState("72")
    const [patientVoiceCooldownHours, setPatientVoiceCooldownHours] = useState("24")
    const [activeCategory, setActiveCategory] = useState<string>("all")
    const [creating, setCreating] = useState(false)

    useEffect(() => {
        ;(async () => {
            setLoading(true)
            let waitsForLocationTemplates = false
            try {
                const locationRows = await listLocations().catch(() => [])
                setLocations(locationRows)
                if (locationRows.length > 0) {
                    waitsForLocationTemplates = true
                    setSelectedLocationId((current) => current || locationRows[0].id)
                } else {
                    setTemplates(await listTemplates())
                }
            } catch {
                toast.error("Failed to load templates")
            } finally {
                if (!waitsForLocationTemplates) setLoading(false)
            }
        })()
    }, [])

    useEffect(() => {
        if (!selectedLocationId) return
        let active = true
        ;(async () => {
            setLoading(true)
            try {
                const templateRows = await listTemplates(selectedLocationId)
                if (active) setTemplates(templateRows)
            } catch {
                if (active) toast.error("Failed to load templates")
            } finally {
                if (active) setLoading(false)
            }
        })()
        return () => {
            active = false
        }
    }, [selectedLocationId])

    useEffect(() => {
        if (!picked || !selectedLocationId || !requiresVoiceProfile(picked)) {
            setVoiceProfiles([])
            setVoiceProfileId("")
            return
        }
        let active = true
        ;(async () => {
            setVoiceProfilesLoading(true)
            try {
                const rows = await listOutboundVoiceProfiles({ locationId: selectedLocationId, isActive: true })
                if (!active) return
                setVoiceProfiles(rows)
                setVoiceProfileId((current) =>
                    rows.some((profile) => profile.id === current) ? current : "",
                )
            } catch {
                if (active) {
                    setVoiceProfiles([])
                    setVoiceProfileId("")
                    toast.error("Failed to load outbound voice profiles")
                }
            } finally {
                if (active) setVoiceProfilesLoading(false)
            }
        })()
        return () => {
            active = false
        }
    }, [picked, selectedLocationId])

    useEffect(() => {
        if (!picked || !selectedLocationId || !requiresAppointmentTypes(picked)) return
        let active = true
        ;(async () => {
            setAppointmentTypesLoading(true)
            try {
                const rows = await listAppointmentTypes(selectedLocationId)
                if (!active) return
                const activeRows = rows.filter((row) => row.is_active)
                setAppointmentTypes(activeRows)
                setAppointmentTypesLocationId(selectedLocationId)
                setAppointmentTypeIds((current) =>
                    current.filter((id) => activeRows.some((row) => row.source_id === id)),
                )
            } catch {
                if (active) {
                    setAppointmentTypes([])
                    setAppointmentTypesLocationId(selectedLocationId)
                    toast.error("Failed to load appointment types")
                }
            } finally {
                if (active) setAppointmentTypesLoading(false)
            }
        })()
        return () => {
            active = false
        }
    }, [picked, selectedLocationId])

    const categories = useMemo(() => {
        const present = Array.from(new Set(templates.map((t) => t.category)))
        return present.sort((a, b) => {
            const ai = CATEGORY_ORDER.indexOf(a)
            const bi = CATEGORY_ORDER.indexOf(b)
            return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
        })
    }, [templates])

    const visibleTemplates =
        activeCategory === "all"
            ? templates
            : templates.filter((template) => template.category === activeCategory)

    function openPicker(t: CampaignTemplate) {
        if (isPmsUnsupported(t)) return
        setPicked(t)
        setName(t.name)
        setAudienceSource(setupFieldDefault(t, "audience_source", t.metadata.default_audience))
        setChannelSequence(
            setupFieldDefault(
                t,
                "channel_sequence",
                t.metadata.supported_channels.map((ch) => ch.toUpperCase()).join(" -> "),
            ),
        )
        setCopyVariant(t.metadata.copy_variants[0]?.id ?? "standard")
        setHandoffBehavior(
            setupFieldDefault(
                t,
                "staff_handoff_behavior",
                t.metadata.default_staff_handoff_reason ?? "Monitor campaign operations",
            ),
        )
        setVoiceProfileId("")
        setVoiceProfiles([])
        setAppointmentTypeIds([])
        setAppointmentTypes([])
        setAppointmentTypesLocationId(null)
        setAppointmentReasons("")
        setPostOpReasons("")
        setCallOffsetHoursBefore(setupFieldDefault(t, "call_offset_hours_before", "24"))
        setRetryDelay1Hours(setupFieldDefault(t, "retry_delay_1_hours", "5"))
        setRetryDelay2Hours(setupFieldDefault(t, "retry_delay_2_hours", "5"))
        setPostOpDelayHours(setupFieldDefault(t, "post_op_delay_hours", "24"))
        setPostOpLatestCallHours(setupFieldDefault(t, "post_op_latest_call_hours", "72"))
        setPatientVoiceCooldownHours(setupFieldDefault(t, "patient_voice_cooldown_hours", "24"))
    }

    async function handleCreate() {
        if (!picked) return
        setCreating(true)
        try {
            const setupOptions: Record<string, unknown> = {
                audience_source: audienceSource,
                channel_sequence: channelSequence,
                copy_variant: copyVariant,
                staff_handoff_behavior: handoffBehavior,
            }
            if (requiresAppointmentTypes(picked)) {
                setupOptions.appointment_type_ids = appointmentTypeIds
            }
            if (hasSetupField(picked, "appointment_reasons")) {
                setupOptions.appointment_reasons = appointmentReasons
                    .split(",")
                    .map((reason) => reason.trim())
                    .filter(Boolean)
            }
            if (hasSetupField(picked, "post_op_reasons")) {
                setupOptions.post_op_reasons = postOpReasons
                    .split(",")
                    .map((reason) => reason.trim())
                    .filter(Boolean)
            }
            if (hasSetupField(picked, "call_offset_hours_before")) {
                setupOptions.call_offset_hours_before = Number(callOffsetHoursBefore)
            }
            if (hasSetupField(picked, "retry_delay_1_hours")) {
                setupOptions.retry_delay_1_hours = Number(retryDelay1Hours)
            }
            if (hasSetupField(picked, "retry_delay_2_hours")) {
                setupOptions.retry_delay_2_hours = Number(retryDelay2Hours)
            }
            if (hasSetupField(picked, "post_op_delay_hours")) {
                setupOptions.post_op_delay_hours = Number(postOpDelayHours)
            }
            if (hasSetupField(picked, "post_op_latest_call_hours")) {
                setupOptions.post_op_latest_call_hours = Number(postOpLatestCallHours)
            }
            if (hasSetupField(picked, "patient_voice_cooldown_hours")) {
                setupOptions.patient_voice_cooldown_hours = Number(patientVoiceCooldownHours)
            }
            const wf = await createWorkflowFromTemplate(picked.id, name, {
                locationId: selectedLocationId || null,
                voiceProfileId,
                setupOptions,
            })
            toast.success(`Created paused campaign "${wf.name}"`)
            navigate(`/institution-admin/campaigns/${wf.id}/builder`)
        } catch {
            toast.error("Failed to create campaign from template")
            setCreating(false)
        }
    }

    const voiceRequired = picked ? requiresVoiceProfile(picked) : false
    const appointmentTypesRequired = picked ? requiresAppointmentTypes(picked) : false
    const appointmentReasonsRequired = picked ? hasSetupField(picked, "appointment_reasons") : false
    const postOpReasonsRequired = picked ? hasSetupField(picked, "post_op_reasons") : false
    const postOpTimingInvalid = Boolean(
        picked &&
        hasSetupField(picked, "post_op_delay_hours") &&
        hasSetupField(picked, "post_op_latest_call_hours") &&
        isNonNegativeWholeNumber(postOpDelayHours) &&
        isPositiveWholeNumber(postOpLatestCallHours) &&
        Number(postOpLatestCallHours) < Number(postOpDelayHours),
    )
    const pickedCapability = picked ? pmsCapabilityStatus(picked) : null
    const audienceSourceOptions = picked
        ? setupFieldOptions(picked, "audience_source", picked.metadata.default_audience)
        : []
    const channelSequenceOptions = picked
        ? setupFieldOptions(
            picked,
            "channel_sequence",
            picked.metadata.supported_channels.map((ch) => ch.toUpperCase()).join(" -> "),
        )
        : []
    const handoffBehaviorOptions = picked
        ? setupFieldOptions(
            picked,
            "staff_handoff_behavior",
            picked.metadata.default_staff_handoff_reason ?? "Monitor campaign operations",
        )
        : []
    const copyVariantOptions = picked
        ? picked.metadata.copy_variants.length > 0
            ? picked.metadata.copy_variants
            : [{ id: "standard", label: "Standard copy" }]
        : []
    const appointmentTypeRows =
        appointmentTypesLocationId === selectedLocationId ? appointmentTypes : []
    function toggleAppointmentType(id: string, checked: boolean) {
        setAppointmentTypeIds((current) =>
            checked
                ? Array.from(new Set([...current, id]))
                : current.filter((currentId) => currentId !== id),
        )
    }
    const createDisabled =
        creating ||
        !name.trim() ||
        !selectedLocationId ||
        (voiceRequired && !voiceProfileId.trim()) ||
        (appointmentTypesRequired && appointmentTypeIds.length === 0) ||
        (appointmentReasonsRequired && appointmentReasons.split(",").every((reason) => !reason.trim())) ||
        (postOpReasonsRequired && postOpReasons.split(",").every((reason) => !reason.trim())) ||
        (picked && hasSetupField(picked, "call_offset_hours_before") && !isNonNegativeWholeNumber(callOffsetHoursBefore)) ||
        (picked && hasSetupField(picked, "retry_delay_1_hours") && !(Number(retryDelay1Hours) > 0)) ||
        (picked && hasSetupField(picked, "retry_delay_2_hours") && !(Number(retryDelay2Hours) > 0)) ||
        (picked && hasSetupField(picked, "post_op_delay_hours") && !isNonNegativeWholeNumber(postOpDelayHours)) ||
        (picked && hasSetupField(picked, "post_op_latest_call_hours") && !isPositiveWholeNumber(postOpLatestCallHours)) ||
        postOpTimingInvalid ||
        (picked && hasSetupField(picked, "patient_voice_cooldown_hours") && !isNonNegativeWholeNumber(patientVoiceCooldownHours)) ||
        pickedCapability?.supported === false

    return (
        <div className="ui-page ui-page-stack">
            <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" asChild className="w-8">
                    <Link to="/institution-admin/campaigns">
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <span className="text-sm text-muted-foreground">Campaigns</span>
            </div>

            <PageHeader
                icon={LayoutTemplate}
                art={campaignsIcon}
                title="Start from a template"
                description="Dental campaign defaults with required fields, readiness checks, and launch metadata."
            />

            {loading ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-40 w-full" />
                    ))}
                </div>
            ) : templates.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
                    <div className="grid size-12 place-items-center rounded-full bg-muted">
                        <LayoutTemplate className="h-6 w-6 opacity-40" />
                    </div>
                    <p className="text-sm font-medium text-foreground/70">No templates available</p>
                </div>
            ) : (
                <div className="space-y-4">
                    <div className="flex flex-wrap gap-2">
                        <Button
                            variant={activeCategory === "all" ? "default" : "outline"}
                            size="sm"
                            onClick={() => setActiveCategory("all")}
                        >
                            All
                        </Button>
                        {categories.map((category) => (
                            <Button
                                key={category}
                                variant={activeCategory === category ? "default" : "outline"}
                                size="sm"
                                onClick={() => setActiveCategory(category)}
                            >
                                {label(category)}
                            </Button>
                        ))}
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        {visibleTemplates.map((t) => {
                            const badgeLabel = pmsBadgeLabel(t)
                            const unsupported = isPmsUnsupported(t)
                            return (
                            <Card key={t.id} className="flex min-h-[260px] flex-col">
                                <CardHeader className="pb-2">
                                    <div className="mb-2 flex items-center justify-between gap-2">
                                        <Badge variant="secondary" className="capitalize">
                                            {label(t.category)}
                                        </Badge>
                                        {badgeLabel && (
                                            <Badge variant={unsupported ? "destructive" : "outline"}>
                                                {badgeLabel}
                                            </Badge>
                                        )}
                                    </div>
                                    <CardTitle className="text-base font-semibold">{t.name}</CardTitle>
                                    <span className="text-xs text-muted-foreground">
                                        {triggerTypeLabel(t.trigger_type as TriggerType)}
                                    </span>
                                </CardHeader>
                                <CardContent className="flex flex-1 flex-col gap-3">
                                    <p className="flex-1 text-sm text-muted-foreground">{t.description}</p>
                                    <div className="space-y-1 text-xs text-muted-foreground">
                                        <div>
                                            Goal: <span className="text-foreground/80">{t.metadata.goal}</span>
                                        </div>
                                        <div>
                                            Channels:{" "}
                                            <span className="text-foreground/80">
                                                {t.metadata.supported_channels
                                                    .map((ch) => ch.toUpperCase())
                                                    .join(", ")}
                                            </span>
                                        </div>
                                    </div>
                                    {t.tags.length > 0 && (
                                        <div className="flex flex-wrap gap-1">
                                            {t.tags.map((tag) => (
                                                <span
                                                    key={tag}
                                                    className="rounded-full bg-muted px-2 py-0.5 text-2xs font-medium text-muted-foreground"
                                                >
                                                    {tag}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    {unsupported && t.metadata.pms_capability_evaluation?.message && (
                                        <p className="text-xs text-destructive">
                                            {t.metadata.pms_capability_evaluation.message}
                                        </p>
                                    )}
                                    <Button
                                        size="sm"
                                        className="gap-1.5"
                                        disabled={unsupported}
                                        onClick={() => openPicker(t)}
                                    >
                                        <Sparkles className="h-3.5 w-3.5" /> Use template
                                    </Button>
                                </CardContent>
                            </Card>
                            )
                        })}
                    </div>
                </div>
            )}

            <Dialog open={picked !== null} onOpenChange={(o) => !o && !creating && setPicked(null)}>
                <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Set up campaign</DialogTitle>
                        <DialogDescription>
                            A paused workflow will be created from "{picked?.name}" with these launch defaults.
                        </DialogDescription>
                    </DialogHeader>
                    {picked && (
                        <div className="grid gap-5 md:grid-cols-[1fr_280px]">
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="campaign-name">Campaign name</Label>
                                    <Input
                                        id="campaign-name"
                                        value={name}
                                        onChange={(e) => setName(e.target.value)}
                                        placeholder="Campaign name"
                                    />
                                </div>
                                <div className="grid gap-4 sm:grid-cols-2">
                                    <div className="space-y-2">
                                        <Label>Location</Label>
                                        <Select value={selectedLocationId} onValueChange={setSelectedLocationId}>
                                            <SelectTrigger>
                                                <SelectValue placeholder="Select location" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {locations.map((location) => (
                                                    <SelectItem key={location.id} value={location.id}>
                                                        {location.name}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="audience-source">Audience source</Label>
                                        <Select
                                            value={audienceSource}
                                            onValueChange={setAudienceSource}
                                        >
                                            <SelectTrigger id="audience-source">
                                                <SelectValue placeholder="Select audience source" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {audienceSourceOptions.map((option) => (
                                                    <SelectItem key={option} value={option}>
                                                        {option}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="channel-sequence">Channel sequence</Label>
                                        <Select
                                            value={channelSequence}
                                            onValueChange={setChannelSequence}
                                        >
                                            <SelectTrigger id="channel-sequence">
                                                <SelectValue placeholder="Select channel sequence" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {channelSequenceOptions.map((option) => (
                                                    <SelectItem key={option} value={option}>
                                                        {option}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="message-copy">Message copy</Label>
                                        <Select value={copyVariant} onValueChange={setCopyVariant}>
                                            <SelectTrigger id="message-copy">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {copyVariantOptions.map((variant) => (
                                                    <SelectItem key={variant.id} value={variant.id}>
                                                        {variant.label}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                                {voiceRequired && (
                                    <div className="space-y-2">
                                        <Label htmlFor="voice-agent">Voice profile</Label>
                                        <Select
                                            value={voiceProfileId || "__none__"}
                                            disabled={voiceProfilesLoading || voiceProfiles.length === 0}
                                            onValueChange={(value) => setVoiceProfileId(value === "__none__" ? "" : value)}
                                        >
                                            <SelectTrigger id="voice-agent">
                                                <SelectValue placeholder="Choose outbound voice profile" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="__none__" disabled={voiceProfiles.length > 0}>
                                                    {voiceProfilesLoading ? "Loading profiles..." : "No profile selected"}
                                                </SelectItem>
                                                {voiceProfiles.map((profile) => (
                                                    <SelectItem key={profile.id} value={profile.id}>
                                                        {profile.display_name || profile.purpose || "Unnamed voice profile"}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        {!voiceProfilesLoading && voiceProfiles.length === 0 && (
                                            <p className="text-xs text-muted-foreground">
                                                No outbound voice profiles are configured for this location. Ask a platform admin to add one.
                                            </p>
                                        )}
                                    </div>
                                )}
                                {hasSetupField(picked, "appointment_reasons") && (
                                    <div className="space-y-2">
                                        <Label htmlFor="appointment-reasons">Eligible GoTracker reasons</Label>
                                        <Input
                                            id="appointment-reasons"
                                            value={appointmentReasons}
                                            onChange={(event) => setAppointmentReasons(event.target.value)}
                                            placeholder="bridge prep, implant surgery"
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Comma-separated. Matching is exact and ignores capitalization.
                                        </p>
                                    </div>
                                )}
                                {hasSetupField(picked, "post_op_reasons") && (
                                    <div className="space-y-2">
                                        <Label htmlFor="post-op-reasons">Eligible completed GoTracker reasons</Label>
                                        <Input
                                            id="post-op-reasons"
                                            value={postOpReasons}
                                            onChange={(event) => setPostOpReasons(event.target.value)}
                                            placeholder="extraction, implant surgery"
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Comma-separated. Matching is exact and ignores capitalization.
                                        </p>
                                    </div>
                                )}
                                {(hasSetupField(picked, "call_offset_hours_before") ||
                                    hasSetupField(picked, "retry_delay_1_hours") ||
                                    hasSetupField(picked, "retry_delay_2_hours") ||
                                    hasSetupField(picked, "post_op_delay_hours") ||
                                    hasSetupField(picked, "post_op_latest_call_hours") ||
                                    hasSetupField(picked, "patient_voice_cooldown_hours")) && (
                                    <div className="grid gap-4 sm:grid-cols-3">
                                        {hasSetupField(picked, "call_offset_hours_before") && (
                                            <div className="space-y-2">
                                                <Label htmlFor="call-offset-hours">Call hours before</Label>
                                                <Input
                                                    id="call-offset-hours"
                                                    type="number"
                                                    min="0"
                                                    step="1"
                                                    value={callOffsetHoursBefore}
                                                    onChange={(event) => setCallOffsetHoursBefore(event.target.value)}
                                                />
                                            </div>
                                        )}
                                        {hasSetupField(picked, "retry_delay_1_hours") && (
                                            <div className="space-y-2">
                                                <Label htmlFor="retry-delay-1">Retry 1 delay (hours)</Label>
                                                <Input
                                                    id="retry-delay-1"
                                                    type="number"
                                                    min="0.25"
                                                    step="0.25"
                                                    value={retryDelay1Hours}
                                                    onChange={(event) => setRetryDelay1Hours(event.target.value)}
                                                />
                                            </div>
                                        )}
                                        {hasSetupField(picked, "retry_delay_2_hours") && (
                                            <div className="space-y-2">
                                                <Label htmlFor="retry-delay-2">Retry 2 delay (hours)</Label>
                                                <Input
                                                    id="retry-delay-2"
                                                    type="number"
                                                    min="0.25"
                                                    step="0.25"
                                                    value={retryDelay2Hours}
                                                    onChange={(event) => setRetryDelay2Hours(event.target.value)}
                                                />
                                            </div>
                                        )}
                                        {hasSetupField(picked, "post_op_delay_hours") && (
                                            <div className="space-y-2">
                                                <Label htmlFor="post-op-delay-hours">
                                                    Hours after completion before calling
                                                </Label>
                                                <Input
                                                    id="post-op-delay-hours"
                                                    type="number"
                                                    min="0"
                                                    step="1"
                                                    value={postOpDelayHours}
                                                    onChange={(event) => setPostOpDelayHours(event.target.value)}
                                                />
                                            </div>
                                        )}
                                        {hasSetupField(picked, "post_op_latest_call_hours") && (
                                            <div className="space-y-2">
                                                <Label htmlFor="post-op-latest-call-hours">
                                                    Latest allowed post-op call (hours after completion)
                                                </Label>
                                                <Input
                                                    id="post-op-latest-call-hours"
                                                    type="number"
                                                    min="1"
                                                    step="1"
                                                    value={postOpLatestCallHours}
                                                    onChange={(event) => setPostOpLatestCallHours(event.target.value)}
                                                />
                                            </div>
                                        )}
                                        {hasSetupField(picked, "patient_voice_cooldown_hours") && (
                                            <div className="space-y-2">
                                                <Label htmlFor="patient-voice-cooldown">Patient cooldown (hours)</Label>
                                                <Input
                                                    id="patient-voice-cooldown"
                                                    type="number"
                                                    min="0"
                                                    step="1"
                                                    value={patientVoiceCooldownHours}
                                                    onChange={(event) => setPatientVoiceCooldownHours(event.target.value)}
                                                />
                                            </div>
                                        )}
                                        {postOpTimingInvalid && (
                                            <p className="text-xs text-destructive sm:col-span-3">
                                                Latest allowed call time must be at least the post-op delay.
                                            </p>
                                        )}
                                    </div>
                                )}
                                {appointmentTypesRequired && (
                                    <div className="space-y-2">
                                        <Label>Major appointment types</Label>
                                        <div className="max-h-56 space-y-2 overflow-y-auto rounded-md border border-border p-2">
                                            {appointmentTypesLoading ? (
                                                <p className="text-xs text-muted-foreground">
                                                    Loading appointment types...
                                                </p>
                                            ) : appointmentTypeRows.length === 0 ? (
                                                <p className="text-xs text-muted-foreground">
                                                    No active appointment types are available for this location yet.
                                                </p>
                                            ) : (
                                                appointmentTypeRows.map((type) => (
                                                    <label
                                                        key={type.source_id}
                                                        className="flex items-center gap-2 rounded px-1.5 py-1 text-sm"
                                                    >
                                                        <Checkbox
                                                            checked={appointmentTypeIds.includes(type.source_id)}
                                                            onCheckedChange={(checked) =>
                                                                toggleAppointmentType(type.source_id, checked === true)
                                                            }
                                                        />
                                                        <span className="min-w-0 flex-1 truncate">{type.name}</span>
                                                        {type.duration_minutes && (
                                                            <span className="shrink-0 text-xs text-muted-foreground">
                                                                {type.duration_minutes}m
                                                            </span>
                                                        )}
                                                    </label>
                                                ))
                                            )}
                                        </div>
                                        <p className="text-xs text-muted-foreground">
                                            Only appointments matching these types will enter this workflow.
                                        </p>
                                    </div>
                                )}
                                <div className="space-y-2">
                                    <Label htmlFor="staff-handoff-behavior">Staff handoff behavior</Label>
                                    <Select
                                        value={handoffBehavior}
                                        onValueChange={setHandoffBehavior}
                                    >
                                        <SelectTrigger id="staff-handoff-behavior">
                                            <SelectValue placeholder="Select staff handoff behavior" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {handoffBehaviorOptions.map((option) => (
                                                <SelectItem key={option} value={option}>
                                                    {option}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <div className="space-y-4 rounded-md border bg-muted/30 p-3">
                                <div>
                                    <div className="text-sm font-medium">Launch checklist preview</div>
                                    <div className="mt-2 space-y-2">
                                        {picked.metadata.required_readiness_checks.map((check) => (
                                            <div
                                                key={check}
                                                className="flex items-center gap-2 text-xs text-muted-foreground"
                                            >
                                                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                                                <span>{check.replace(/_/g, " ")}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-sm font-medium">Required fields</div>
                                    <div className="mt-2 flex flex-wrap gap-1">
                                        {picked.metadata.required_merge_fields.map((field) => (
                                            <Badge key={field} variant="outline" className="font-mono text-2xs">
                                                {`{{${field}}}`}
                                            </Badge>
                                        ))}
                                    </div>
                                </div>
                                <div className="text-xs text-muted-foreground">
                                    Frequency cap: {picked.metadata.default_frequency_cap.max_per_day}/day,
                                    {" "}{picked.metadata.default_frequency_cap.max_per_rolling_7_days}/7 days
                                </div>
                                {picked.metadata.pms_capability_requirements.length > 0 && (
                                    <div className="space-y-1 text-xs text-muted-foreground">
                                        <div>
                                            PMS capability: {picked.metadata.pms_capability_requirements.join(", ")}
                                        </div>
                                        {pickedCapability?.message && (
                                            <div className={pickedCapability.supported ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}>
                                                {pickedCapability.message}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button variant="outline" disabled={creating} onClick={() => setPicked(null)}>
                            Cancel
                        </Button>
                        <Button disabled={createDisabled} onClick={handleCreate} className="gap-1.5">
                            {creating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                            Create &amp; open builder
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
