/** Campaign template picker + guided clone flow. */
import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft, CheckCircle2, LayoutTemplate, Loader2, Sparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { createWorkflowFromTemplate, listTemplates, type CampaignTemplate } from "@/lib/workflow-api"
import { triggerTypeLabel } from "@/lib/workflow/catalog"
import { listLocations } from "@/lib/tenant-api"
import type { LocationInfo } from "@/types"
import type { TriggerType } from "@/types/workflow"

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

function requiresVoiceAgent(template: CampaignTemplate) {
    return template.metadata.setup_fields.some((field) => field.id === "voice_agent_id" && field.required)
}

export default function WorkflowTemplates() {
    const navigate = useNavigate()
    const [templates, setTemplates] = useState<CampaignTemplate[]>([])
    const [locations, setLocations] = useState<LocationInfo[]>([])
    const [loading, setLoading] = useState(true)
    const [picked, setPicked] = useState<CampaignTemplate | null>(null)
    const [name, setName] = useState("")
    const [selectedLocationId, setSelectedLocationId] = useState("")
    const [audienceSource, setAudienceSource] = useState("")
    const [channelSequence, setChannelSequence] = useState("")
    const [copyVariant, setCopyVariant] = useState("")
    const [handoffBehavior, setHandoffBehavior] = useState("")
    const [voiceAgentId, setVoiceAgentId] = useState("")
    const [activeCategory, setActiveCategory] = useState<string>("all")
    const [creating, setCreating] = useState(false)

    useEffect(() => {
        ;(async () => {
            setLoading(true)
            try {
                const [templateRows, locationRows] = await Promise.all([
                    listTemplates(),
                    listLocations().catch(() => []),
                ])
                setTemplates(templateRows)
                setLocations(locationRows)
                if (locationRows.length > 0) {
                    setSelectedLocationId((current) => current || locationRows[0].id)
                }
            } catch {
                toast.error("Failed to load templates")
            } finally {
                setLoading(false)
            }
        })()
    }, [])

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
        setPicked(t)
        setName(t.name)
        setAudienceSource(t.metadata.default_audience)
        setChannelSequence(
            t.metadata.supported_channels.map((ch) => ch.toUpperCase()).join(" -> "),
        )
        setCopyVariant(t.metadata.copy_variants[0]?.id ?? "standard")
        setHandoffBehavior(t.metadata.default_staff_handoff_reason ?? "Monitor campaign operations")
        setVoiceAgentId("")
    }

    async function handleCreate() {
        if (!picked) return
        setCreating(true)
        try {
            const wf = await createWorkflowFromTemplate(picked.id, name, {
                locationId: selectedLocationId || null,
                voiceAgentId,
                setupOptions: {
                    audience_source: audienceSource,
                    channel_sequence: channelSequence,
                    copy_variant: copyVariant,
                    staff_handoff_behavior: handoffBehavior,
                },
            })
            toast.success(`Created "${wf.name}"`)
            navigate(`/institution-admin/campaigns/${wf.id}/builder`)
        } catch {
            toast.error("Failed to create campaign from template")
            setCreating(false)
        }
    }

    const voiceRequired = picked ? requiresVoiceAgent(picked) : false
    const createDisabled =
        creating || !name.trim() || !selectedLocationId || (voiceRequired && !voiceAgentId.trim())

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" asChild className="h-8 w-8">
                    <Link to="/institution-admin/campaigns">
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <span className="text-sm text-muted-foreground">Campaigns</span>
            </div>

            <div>
                <h2 className="flex items-center gap-2 text-3xl font-bold tracking-tight">
                    <LayoutTemplate className="h-7 w-7" />
                    Start from a template
                </h2>
                <p className="mt-1 text-muted-foreground">
                    Dental campaign defaults with required fields, readiness checks, and launch metadata.
                </p>
            </div>

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
                        {visibleTemplates.map((t) => (
                            <Card key={t.id} className="flex min-h-[260px] flex-col">
                                <CardHeader className="pb-2">
                                    <div className="mb-2 flex items-center justify-between gap-2">
                                        <Badge variant="secondary" className="capitalize">
                                            {label(t.category)}
                                        </Badge>
                                        {t.metadata.pms_capability_requirements.length > 0 && (
                                            <Badge variant="outline">PMS gated</Badge>
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
                                                    className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                                                >
                                                    {tag}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    <Button size="sm" className="gap-1.5" onClick={() => openPicker(t)}>
                                        <Sparkles className="h-3.5 w-3.5" /> Use template
                                    </Button>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </div>
            )}

            <Dialog open={picked !== null} onOpenChange={(o) => !o && !creating && setPicked(null)}>
                <DialogContent className="max-w-3xl">
                    <DialogHeader>
                        <DialogTitle>Set up campaign</DialogTitle>
                        <DialogDescription>
                            A workflow will be created from "{picked?.name}" with these launch defaults.
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
                                        <Label>Audience source</Label>
                                        <Input
                                            value={audienceSource}
                                            onChange={(e) => setAudienceSource(e.target.value)}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Channel sequence</Label>
                                        <Input
                                            value={channelSequence}
                                            onChange={(e) => setChannelSequence(e.target.value)}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Message copy</Label>
                                        <Select value={copyVariant} onValueChange={setCopyVariant}>
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {(picked.metadata.copy_variants.length > 0
                                                    ? picked.metadata.copy_variants
                                                    : [{ id: "standard", label: "Standard copy" }]
                                                ).map((variant) => (
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
                                        <Input
                                            id="voice-agent"
                                            value={voiceAgentId}
                                            onChange={(e) => setVoiceAgentId(e.target.value)}
                                            placeholder="Retell agent ID"
                                        />
                                    </div>
                                )}
                                <div className="space-y-2">
                                    <Label>Staff handoff behavior</Label>
                                    <Input
                                        value={handoffBehavior}
                                        onChange={(e) => setHandoffBehavior(e.target.value)}
                                    />
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
                                                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                                                <span>{check.replace(/_/g, " ")}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-sm font-medium">Required fields</div>
                                    <div className="mt-2 flex flex-wrap gap-1">
                                        {picked.metadata.required_merge_fields.map((field) => (
                                            <Badge key={field} variant="outline" className="font-mono text-[10px]">
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
                                    <div className="text-xs text-muted-foreground">
                                        PMS capability: {picked.metadata.pms_capability_requirements.join(", ")}
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
