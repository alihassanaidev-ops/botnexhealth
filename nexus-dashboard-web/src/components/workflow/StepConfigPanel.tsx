/**
 * Right-side configuration panel (Sheet) for the selected node or the trigger.
 *
 * Uses controlled shadcn primitives (Input/Textarea/Select/Switch/Label) with immediate
 * immutable propagation. Validation is centralized in `lib/workflow/validation.ts` and
 * surfaced in the ValidationPanel, so this panel does not duplicate per-field zod (the
 * definition is a 6-variant discriminated union; centralized validation is the single
 * source of truth). Edges are authored via the next-step selectors here — not by
 * dragging on the canvas (Plan 02 architecture decision).
 */
import { useEffect, useMemo, useState } from "react"
import { Check, ChevronDown, ChevronsUpDown, GitBranch, Flag, Plus, Search, Trash2 } from "lucide-react"
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { NODE_META, CONDITION_OP_LABELS, TRIGGER_META } from "@/lib/workflow/catalog"
import { listPhoneCountryRegions, listWorkflowLlmModels, type PhoneCountryRegion } from "@/lib/workflow-api"
import { SmsPreview, EmailPreview } from "./MessagePreview"
import { useMergeFields } from "@/lib/workflow/merge-fields"
import { addVoiceOutcomeBranch, TRIGGER_NODE_ID, VOICE_OUTCOME_BRANCH_VALUES } from "@/lib/workflow/graph"
import {
    contextFieldsForTrigger,
    contextValueAtPath,
    formatContextValue,
    GOTRACKER_APPOINTMENT_WEBHOOK_SAMPLE,
    SAMPLE_WORKFLOW_CONTEXT,
} from "@/lib/workflow/context-fields"
import type { OutboundVoiceProfile } from "@/types"
import type {
    ConditionNode,
    ConditionOp,
    ConditionRule,
    DripNode,
    JsonMapperNode,
    LlmNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    TriggerType,
    UpdateGoTrackerAppointmentNode,
    UpdatePatientStatusNode,
    WaitNode,
    WorkflowDefinition,
    WorkflowLlmModel,
    WorkflowNode,
    WorkflowTrigger,
} from "@/types/workflow"

const NONE = "__none__"
const CONDITION_OPS: ConditionOp[] = ["eq", "neq", "in", "in_case_insensitive", "not_in", "is_null", "is_not_null", "contains", "not_contains"]
const CUSTOM_CONDITION_FIELD = "__custom_field__"
const CUSTOM_RELATIVE_WAIT = "__custom__"
const GOTRACKER_STATUS_OPTIONS = [
    { id: 1, label: "Booked" },
    { id: 2, label: "Booked + Waiting" },
    { id: 3, label: "Cancelled" },
    { id: 4, label: "Late" },
    { id: 5, label: "No Show" },
    { id: 6, label: "Office Cancel" },
    { id: 7, label: "Pending" },
    { id: 8, label: "Short Cancel" },
    { id: 9, label: "Waiting" },
]
const FALLBACK_PHONE_COUNTRIES: PhoneCountryRegion[] = [
    { region: "US", calling_code: "+1" },
    { region: "GB", calling_code: "+44" },
    { region: "CA", calling_code: "+1" },
]
const REGION_DISPLAY_NAMES = typeof Intl !== "undefined" && "DisplayNames" in Intl
    ? new Intl.DisplayNames(["en"], { type: "region" })
    : null

function phoneCountryLabel(country: PhoneCountryRegion): string {
    const name = REGION_DISPLAY_NAMES?.of(country.region) || country.region
    return `${name} (${country.calling_code})`
}

export interface StepConfigPanelProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    def: WorkflowDefinition
    /** Selected node id, `TRIGGER_NODE_ID` for the trigger, or null. */
    selectedId: string | null
    onNodeChange: (node: WorkflowNode) => void
    onDefinitionChange: (def: WorkflowDefinition) => void
    onTriggerChange: (trigger: WorkflowTrigger) => void
    onDeleteNode: (id: string) => void
    onSetEntry: (id: string) => void
    locationId?: string | null
    voiceProfiles?: OutboundVoiceProfile[]
    readOnly?: boolean
}

export default function StepConfigPanel(props: StepConfigPanelProps) {
    const { open, onOpenChange, def, selectedId } = props
    const isTrigger = selectedId === TRIGGER_NODE_ID
    const node = !isTrigger ? def.nodes.find((n) => n.id === selectedId) : undefined

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent className="w-full overflow-y-auto sm:max-w-md [background:hsl(var(--background))] shadow-2xl">
                {isTrigger ? (
                    <TriggerForm
                        trigger={def.trigger}
                        onChange={props.onTriggerChange}
                        readOnly={props.readOnly}
                    />
                ) : node ? (
                    <NodeForm {...props} node={node} />
                ) : (
                    <div className="py-10 text-center text-sm text-muted-foreground">
                        Select a step on the canvas to configure it.
                    </div>
                )}
            </SheetContent>
        </Sheet>
    )
}

// ---------------------------------------------------------------------------
// Trigger form
// ---------------------------------------------------------------------------
function TriggerForm({
    trigger,
    onChange,
    readOnly,
}: {
    trigger: WorkflowTrigger
    onChange: (t: WorkflowTrigger) => void
    readOnly?: boolean
}) {
    const meta = TRIGGER_META[trigger.type]
    return (
        <>
            <SheetHeader>
                <SheetTitle className="flex items-center gap-2">
                    <meta.icon className="h-4 w-4" /> Trigger
                </SheetTitle>
                <SheetDescription>How contacts enter this workflow.</SheetDescription>
            </SheetHeader>
            <div className="space-y-4 py-4">
                <Field label="Trigger type">
                    <Select
                        value={trigger.type}
                        onValueChange={(v) => onChange(defaultTrigger(v as TriggerType))}
                        disabled={readOnly}
                    >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                            {(Object.keys(TRIGGER_META) as TriggerType[]).map((t) => (
                                <SelectItem key={t} value={t}>{TRIGGER_META[t].label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </Field>

                {trigger.type === "appointment_offset" && (
                    <>
                        <Field label="Hours relative to appointment" hint="Negative = before (e.g. -24 = 24h before).">
                            <Input
                                type="number"
                                value={trigger.offset_hours}
                                disabled={readOnly}
                                onChange={(e) => onChange({ ...trigger, offset_hours: toInt(e.target.value, 0) })}
                            />
                        </Field>
                        <ContextPreview triggerType={trigger.type} />
                    </>
                )}
                {trigger.type === "appointment_state_changed" && (
                    <>
                        <Field label="GoTracker status">
                            <Select
                                value={trigger.status_ids[0] ? String(trigger.status_ids[0]) : NONE}
                                disabled={readOnly}
                                onValueChange={(value) => onChange({
                                    ...trigger,
                                    status_ids: value === NONE ? [] : [toInt(value, 1)],
                                })}
                            >
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={NONE}>Any status</SelectItem>
                                    {GOTRACKER_STATUS_OPTIONS.map((status) => (
                                        <SelectItem key={status.id} value={String(status.id)}>
                                            {status.id} · {status.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </Field>
                        <div className="grid grid-cols-2 gap-2">
                            <Field label="Confirmed">
                                <TriStateBooleanSelect
                                    value={trigger.confirmed}
                                    disabled={readOnly}
                                    trueLabel="Is true"
                                    falseLabel="Is false"
                                    onChange={(value) => onChange({ ...trigger, confirmed: value })}
                                />
                            </Field>
                            <Field label="Preconfirmed">
                                <TriStateBooleanSelect
                                    value={trigger.preconfirmed}
                                    disabled={readOnly}
                                    trueLabel="Is true"
                                    falseLabel="Is false"
                                    onChange={(value) => onChange({ ...trigger, preconfirmed: value })}
                                />
                            </Field>
                        </div>
                        <Field label="Campaign goal">
                            <Input
                                value={trigger.campaign_goal ?? ""}
                                disabled={readOnly}
                                placeholder="post_op_followup"
                                onChange={(e) => onChange({
                                    ...trigger,
                                    campaign_goal: e.target.value.trim() || null,
                                })}
                            />
                        </Field>
                        <ContextPreview triggerType={trigger.type} />
                    </>
                )}
                {trigger.type === "recall_scan" && (
                    <Field label="Recall interval (months)">
                        <Input
                            type="number"
                            min={1}
                            value={trigger.recall_interval_months}
                            disabled={readOnly}
                            onChange={(e) => onChange({ ...trigger, recall_interval_months: toInt(e.target.value, 1) })}
                        />
                    </Field>
                )}
                {(trigger.type === "manual" || trigger.type === "bulk_import") && (
                    <p className="text-sm text-muted-foreground">
                        No timing configuration — contacts are enrolled manually or by import.
                    </p>
                )}
                {trigger.type === "callback_requested" && (
                    <p className="text-sm text-muted-foreground">
                        Enrolls when an inbound interaction requests staff follow-up.
                    </p>
                )}
                {trigger.type === "patient_status_changed" && (
                    <>
                        <Field label="Statuses" hint="Comma or newline separated status labels.">
                            <Textarea
                                value={trigger.statuses.join(", ")}
                                disabled={readOnly}
                                placeholder="appointment_confirmed"
                                onChange={(e) => {
                                    const statuses = e.target.value
                                        .split(/[,\n]/)
                                        .map((v) => v.trim())
                                        .filter(Boolean)
                                    onChange({ ...trigger, statuses })
                                }}
                            />
                        </Field>
                        <Field label="Campaign goal">
                            <Input
                                value={trigger.campaign_goal ?? ""}
                                disabled={readOnly}
                                placeholder="post_op_followup"
                                onChange={(e) => onChange({
                                    ...trigger,
                                    campaign_goal: e.target.value.trim() || null,
                                })}
                            />
                        </Field>
                        <ContextPreview triggerType={trigger.type} />
                    </>
                )}
            </div>
        </>
    )
}

// ---------------------------------------------------------------------------
// Node form
// ---------------------------------------------------------------------------
function NodeForm({
    def,
    node,
    onNodeChange,
    onDefinitionChange,
    onDeleteNode,
    onSetEntry,
    voiceProfiles,
    readOnly,
}: StepConfigPanelProps & { node: WorkflowNode }) {
    const meta = NODE_META[node.type]
    const isEntry = def.entry_node_id === node.id

    return (
        <>
            <SheetHeader>
                <SheetTitle className="flex items-center gap-2">
                    <meta.icon className="h-4 w-4" /> {meta.label}
                </SheetTitle>
                <SheetDescription>{meta.description}</SheetDescription>
            </SheetHeader>

            <div className="space-y-4 py-4">
                {node.type === "send_sms" && <SmsFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "send_email" && <EmailFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "send_voice" && (
                    <VoiceFields
                        node={node}
                        def={def}
                        onChange={onNodeChange}
                        onDefinitionChange={onDefinitionChange}
                        voiceProfiles={voiceProfiles ?? []}
                        readOnly={readOnly}
                    />
                )}
                {node.type === "wait" && <WaitFields node={node} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "drip" && <DripFields node={node} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "update_patient_status" && (
                    <UpdatePatientStatusFields node={node} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "update_gotracker_appointment" && (
                    <UpdateGoTrackerAppointmentFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "json_mapper" && (
                    <JsonMapperFields node={node} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "llm" && (
                    <LlmFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "condition" && (
                    <ConditionFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "exit" && (
                    <Field label="Outcome" hint="Label recorded when a contact ends here.">
                        <Input
                            value={node.outcome ?? ""}
                            disabled={readOnly}
                            placeholder="e.g. confirmed"
                            onChange={(e) => onNodeChange({ ...node, outcome: e.target.value || null })}
                        />
                    </Field>
                )}

                {/* Next-step selector(s) — how edges are authored. */}
                {node.type !== "exit" && node.type !== "condition" && (
                    <NextStepField
                        label="Next step"
                        def={def}
                        currentId={node.id}
                        value={(node as { next_node_id: string }).next_node_id}
                        onChange={(v) => onNodeChange({ ...node, next_node_id: v } as WorkflowNode)}
                        readOnly={readOnly}
                    />
                )}

                {(node.type === "wait" ||
                    node.type === "send_sms" ||
                    node.type === "send_voice" ||
                    node.type === "send_email") && (
                    <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                        <div>
                            <Label className="text-sm">Respect quiet hours</Label>
                            <p className="text-xs text-muted-foreground">Hold sends outside the location's window.</p>
                        </div>
                        <Switch
                            checked={(node as { respect_quiet_hours?: boolean }).respect_quiet_hours ?? true}
                            disabled={readOnly}
                            onCheckedChange={(c) =>
                                onNodeChange({ ...node, respect_quiet_hours: c } as WorkflowNode)
                            }
                        />
                    </div>
                )}

                {!readOnly && (
                    <div className="flex items-center gap-2 border-t border-border pt-4">
                        {!isEntry && (
                            <Button variant="outline" size="sm" className="gap-1.5" onClick={() => onSetEntry(node.id)}>
                                <Flag className="h-3.5 w-3.5" /> Set as start
                            </Button>
                        )}
                        <Button
                            variant="ghost"
                            size="sm"
                            className="ml-auto gap-1.5 text-destructive hover:text-destructive"
                            onClick={() => onDeleteNode(node.id)}
                        >
                            <Trash2 className="h-3.5 w-3.5" /> Delete step
                        </Button>
                    </div>
                )}
            </div>
        </>
    )
}

// ---------------------------------------------------------------------------
// Per-type field groups
// ---------------------------------------------------------------------------
function SmsFields({
    node,
    def,
    onChange,
    readOnly,
}: {
    node: SendSmsNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    return (
        <>
            <MessageField
                label="Message"
                value={node.body_template}
                onChange={(v) => onChange({ ...node, body_template: v })}
                triggerType={def.trigger.type}
                channel="sms"
                readOnly={readOnly}
            />
            <div className="space-y-1.5">
                <Label className="text-sm">Preview</Label>
                <SmsPreview node={node} />
            </div>
            <AttemptsField value={node.max_attempts ?? 1} onChange={(v) => onChange({ ...node, max_attempts: v })} readOnly={readOnly} />
        </>
    )
}

function EmailFields({
    node,
    def,
    onChange,
    readOnly,
}: {
    node: SendEmailNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    return (
        <>
            <Field label="Subject">
                <Input
                    value={node.subject_template}
                    disabled={readOnly}
                    onChange={(e) => onChange({ ...node, subject_template: e.target.value })}
                />
            </Field>
            <MessageField
                label="Body"
                value={node.body_template}
                onChange={(v) => onChange({ ...node, body_template: v })}
                triggerType={def.trigger.type}
                channel="email"
                readOnly={readOnly}
            />
            <div className="space-y-1.5">
                <Label className="text-sm">Preview</Label>
                <EmailPreview node={node} />
            </div>
            <AttemptsField value={node.max_attempts ?? 1} onChange={(v) => onChange({ ...node, max_attempts: v })} readOnly={readOnly} />
        </>
    )
}

function VoiceFields({
    node,
    def,
    onChange,
    onDefinitionChange,
    voiceProfiles,
    readOnly,
}: {
    node: SendVoiceNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    onDefinitionChange: (def: WorkflowDefinition) => void
    voiceProfiles: OutboundVoiceProfile[]
    readOnly?: boolean
}) {
    const hasLegacyAgent = Boolean(node.retell_agent_id?.trim() && !node.voice_profile_id)
    const hasProfiles = voiceProfiles.length > 0
    const [phoneCountryOpen, setPhoneCountryOpen] = useState(false)
    const [phoneCountrySearch, setPhoneCountrySearch] = useState("")
    const [phoneCountries, setPhoneCountries] = useState<PhoneCountryRegion[]>(FALLBACK_PHONE_COUNTRIES)
    const [phoneCountriesLoaded, setPhoneCountriesLoaded] = useState(false)
    const selectedPhoneCountry = useMemo(
        () => phoneCountries.find((country) => country.region === (node.phone_country_region || "US")),
        [node.phone_country_region, phoneCountries],
    )
    const filteredPhoneCountries = useMemo(() => {
        const query = phoneCountrySearch.trim().toLowerCase()
        const sorted = [...phoneCountries].sort((a, b) =>
            phoneCountryLabel(a).localeCompare(phoneCountryLabel(b)),
        )
        if (!query) return sorted
        return sorted.filter((country) =>
            [phoneCountryLabel(country), country.region, country.calling_code]
                .join(" ")
                .toLowerCase()
                .includes(query),
        )
    }, [phoneCountries, phoneCountrySearch])

    useEffect(() => {
        if (!node.phone_country_code_enabled || phoneCountriesLoaded) return
        let cancelled = false
        listPhoneCountryRegions()
            .then((countries) => {
                if (!cancelled) setPhoneCountries(countries)
            })
            .catch(() => {
                if (!cancelled) setPhoneCountries(FALLBACK_PHONE_COUNTRIES)
            })
            .finally(() => {
                if (!cancelled) setPhoneCountriesLoaded(true)
            })
        return () => {
            cancelled = true
        }
    }, [node.phone_country_code_enabled, phoneCountriesLoaded])

    return (
        <>
            <Field label="Outbound voice profile" hint="Named location profile used for this outbound call.">
                <Select
                    value={node.voice_profile_id || NONE}
                    disabled={readOnly}
                    onValueChange={(value) => {
                        onChange({
                            ...node,
                            voice_profile_id: value === NONE ? null : value,
                        })
                    }}
                >
                    <SelectTrigger>
                        <SelectValue placeholder="Choose outbound profile" />
                    </SelectTrigger>
                    <SelectContent>
                        {hasLegacyAgent ? (
                            <SelectItem value={NONE}>Legacy voice agent</SelectItem>
                        ) : (
                            <SelectItem value={NONE} disabled={hasProfiles}>
                                No profile selected
                            </SelectItem>
                        )}
                        {voiceProfiles.map((profile) => (
                            <SelectItem key={profile.id} value={profile.id}>
                                {profile.display_name || profile.purpose || "Unnamed voice profile"}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </Field>
            {!hasProfiles && !hasLegacyAgent && (
                <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                    No outbound voice profiles are configured for this location. Ask a platform admin to add one.
                </p>
            )}
            {hasLegacyAgent && (
                <p className="rounded-md border border-border px-3 py-2 text-xs text-muted-foreground">
                    This workflow has an older voice agent configured. Switch it to a named profile when one is available.
                </p>
            )}
            <div className="space-y-3 rounded-md border border-border px-3 py-2">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <Label className="text-sm">Phone country override</Label>
                        <p className="text-xs text-muted-foreground">
                            Use this when patient numbers arrive without a country code.
                        </p>
                    </div>
                    <Switch
                        checked={node.phone_country_code_enabled ?? false}
                        disabled={readOnly}
                        onCheckedChange={(checked) =>
                            onChange({
                                ...node,
                                phone_country_code_enabled: checked,
                                phone_country_region: node.phone_country_region || "US",
                            })
                        }
                    />
                </div>
                {(node.phone_country_code_enabled ?? false) && (
                    <Popover open={phoneCountryOpen} onOpenChange={setPhoneCountryOpen}>
                        <PopoverTrigger asChild>
                            <Button
                                type="button"
                                variant="outline"
                                disabled={readOnly}
                                className="h-10 w-full justify-between px-3 text-left font-normal"
                            >
                                <span className="min-w-0 truncate">
                                    {selectedPhoneCountry
                                        ? phoneCountryLabel(selectedPhoneCountry)
                                        : node.phone_country_region || "Select phone country"}
                                </span>
                                <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                            </Button>
                        </PopoverTrigger>
                        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-0">
                            <div className="border-b border-border p-2">
                                <div className="relative">
                                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                    <Input
                                        value={phoneCountrySearch}
                                        onChange={(event) => setPhoneCountrySearch(event.target.value)}
                                        placeholder="Search countries"
                                        className="h-9 pl-8"
                                    />
                                </div>
                            </div>
                            <div className="max-h-64 overflow-y-auto p-1">
                                {!phoneCountriesLoaded ? (
                                    <div className="px-2 py-6 text-center text-sm text-muted-foreground">
                                        Loading countries...
                                    </div>
                                ) : filteredPhoneCountries.length === 0 ? (
                                    <div className="px-2 py-6 text-center text-sm text-muted-foreground">
                                        No countries match your search.
                                    </div>
                                ) : (
                                    filteredPhoneCountries.map((country) => (
                                        <button
                                            type="button"
                                            key={country.region}
                                            className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                            onClick={() => {
                                                onChange({
                                                    ...node,
                                                    phone_country_code_enabled: true,
                                                    phone_country_region: country.region,
                                                })
                                                setPhoneCountrySearch("")
                                                setPhoneCountryOpen(false)
                                            }}
                                        >
                                            <span className="min-w-0">
                                                <span className="block truncate">{phoneCountryLabel(country)}</span>
                                                <span className="block truncate font-mono text-xs text-muted-foreground">
                                                    {country.region}
                                                </span>
                                            </span>
                                            {(node.phone_country_region || "US") === country.region && (
                                                <Check className="h-4 w-4 shrink-0" />
                                            )}
                                        </button>
                                    ))
                                )}
                            </div>
                        </PopoverContent>
                    </Popover>
                )}
                {!(node.phone_country_code_enabled ?? false) && (
                    <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-200">
                        Local-format patient numbers will not be called unless this is enabled. Numbers already saved with +country code can still be called.
                    </p>
                )}
            </div>
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div>
                    <Label className="text-sm">Wait for voice outcome</Label>
                    <p className="text-xs text-muted-foreground">
                        Pause this run until the post-call result writes call_outcome.
                    </p>
                </div>
                <Switch
                    checked={node.wait_for_outcome ?? false}
                    disabled={readOnly}
                    onCheckedChange={(checked) => onChange({ ...node, wait_for_outcome: checked })}
                />
            </div>
            <Field
                label="Patient voice cooldown"
                hint="Minimum hours before another workflow run may call the same patient. Retries inside this same run are still allowed. Use 0 to disable."
            >
                <Input
                    aria-label="Patient voice cooldown"
                    type="number"
                    min={0}
                    max={168}
                    step={1}
                    value={node.patient_voice_cooldown_hours ?? 24}
                    disabled={readOnly}
                    onChange={(event) =>
                        onChange({
                            ...node,
                            patient_voice_cooldown_hours: toInt(event.target.value, 24),
                        })
                    }
                />
            </Field>
            {!readOnly && (
                <div className="space-y-2 rounded-md border border-border p-3">
                    <div className="space-y-1">
                        <Label className="text-sm">Voice outcome branch</Label>
                        <p className="text-xs text-muted-foreground">
                            Adds a call_outcome condition with booked and staff handoff exits.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-1">
                        {VOICE_OUTCOME_BRANCH_VALUES.map((value) => (
                            <span key={value} className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                                {value}
                            </span>
                        ))}
                    </div>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                        onClick={() => onDefinitionChange(addVoiceOutcomeBranch(def, node.id))}
                    >
                        <GitBranch className="h-3.5 w-3.5" /> Add outcome branch
                    </Button>
                </div>
            )}
            <AttemptsField value={node.max_attempts ?? 1} onChange={(v) => onChange({ ...node, max_attempts: v })} readOnly={readOnly} />
        </>
    )
}

function WaitFields({ node, onChange, readOnly }: { node: WaitNode; onChange: (n: WorkflowNode) => void; readOnly?: boolean }) {
    const delay = node.delay
    return (
        <>
            <Field label="Wait type">
                <Select
                    value={delay.delay_type}
                    disabled={readOnly}
                    onValueChange={(v) =>
                        onChange({
                            ...node,
                            delay:
                                v === "duration"
                                    ? { delay_type: "duration", duration_seconds: 3600 }
                                    : v === "appointment_relative"
                                      ? { delay_type: "appointment_relative", offset_seconds: -3600, anchor_field: "appointment_at" }
                                      : { delay_type: "calendar", offset_days: 0, time_of_day: "09:00" },
                        })
                    }
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="duration">Fixed duration</SelectItem>
                        <SelectItem value="calendar">Calendar day + time</SelectItem>
                        <SelectItem value="appointment_relative">Relative to appointment</SelectItem>
                    </SelectContent>
                </Select>
            </Field>
            {delay.delay_type === "duration" ? (
                <Field label="Duration (hours)">
                    <Input
                        type="number"
                        min={0}
                        step="0.25"
                        value={round2(delay.duration_seconds / 3600)}
                        disabled={readOnly}
                        onChange={(e) =>
                            onChange({
                                ...node,
                                delay: { delay_type: "duration", duration_seconds: Math.round(toFloat(e.target.value, 0) * 3600) },
                            })
                        }
                    />
                </Field>
            ) : delay.delay_type === "calendar" ? (
                <>
                    <Field label="Day offset" hint="Days relative to the anchor (0 = same day).">
                        <Input
                            type="number"
                            value={delay.offset_days}
                            disabled={readOnly}
                            onChange={(e) =>
                                onChange({ ...node, delay: { ...delay, offset_days: toInt(e.target.value, 0) } })
                            }
                        />
                    </Field>
                    <Field label="Send time (HH:MM, local)">
                        <Input
                            type="time"
                            value={delay.time_of_day}
                            disabled={readOnly}
                            onChange={(e) => onChange({ ...node, delay: { ...delay, time_of_day: e.target.value } })}
                        />
                    </Field>
                </>
            ) : (
                <>
                    <Field label="Timing" hint="Negative = before appointment. Positive = after appointment.">
                        <Select
                            value={relativeWaitPresetValue(delay.offset_seconds)}
                            disabled={readOnly}
                            onValueChange={(v) => {
                                const offsetSeconds = v === CUSTOM_RELATIVE_WAIT
                                    ? (isRelativeWaitPreset(delay.offset_seconds) ? 0 : delay.offset_seconds)
                                    : Number(v)
                                onChange({
                                    ...node,
                                    delay: {
                                        delay_type: "appointment_relative",
                                        offset_seconds: offsetSeconds,
                                        anchor_field: delay.anchor_field ?? "appointment_at",
                                    },
                                })
                            }}
                        >
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="-86400">1 day before appointment</SelectItem>
                                <SelectItem value="-7200">2 hours before appointment</SelectItem>
                                <SelectItem value="-3600">1 hour before appointment</SelectItem>
                                <SelectItem value="3600">1 hour after appointment</SelectItem>
                                <SelectItem value="86400">1 day after appointment</SelectItem>
                                <SelectItem value={CUSTOM_RELATIVE_WAIT}>Custom offset</SelectItem>
                            </SelectContent>
                        </Select>
                    </Field>
                    <Field label="Custom offset (hours)">
                        <Input
                            type="number"
                            step="0.25"
                            value={round2(delay.offset_seconds / 3600)}
                            disabled={readOnly}
                            onChange={(e) =>
                                onChange({
                                    ...node,
                                    delay: {
                                        delay_type: "appointment_relative",
                                        offset_seconds: Math.round(toFloat(e.target.value, 0) * 3600),
                                        anchor_field: delay.anchor_field ?? "appointment_at",
                                    },
                                })
                            }
                        />
                    </Field>
                </>
            )}
        </>
    )
}

function relativeWaitPresetValue(seconds: number): string {
    return isRelativeWaitPreset(seconds) ? String(seconds) : CUSTOM_RELATIVE_WAIT
}

function isRelativeWaitPreset(seconds: number): boolean {
    const presets = new Set(["-86400", "-7200", "-3600", "3600", "86400"])
    return presets.has(String(seconds))
}

function DripFields({ node, onChange, readOnly }: { node: DripNode; onChange: (n: WorkflowNode) => void; readOnly?: boolean }) {
    return (
        <>
            <Field label="Batch size" hint="How many contacts are released immediately in each batch.">
                <Input
                    type="number"
                    min={1}
                    max={10000}
                    value={node.batch_size}
                    disabled={readOnly}
                    onChange={(e) =>
                        onChange({
                            ...node,
                            batch_size: clamp(toInt(e.target.value, node.batch_size), 1, 10000),
                        })
                    }
                />
            </Field>
            <Field label="Interval (minutes)" hint="How long to wait before opening the next batch.">
                <Input
                    type="number"
                    min={1}
                    step={1}
                    value={Math.max(1, Math.round(node.interval_seconds / 60))}
                    disabled={readOnly}
                    onChange={(e) =>
                        onChange({
                            ...node,
                            interval_seconds: Math.max(1, toInt(e.target.value, Math.round(node.interval_seconds / 60))) * 60,
                        })
                    }
                />
            </Field>
        </>
    )
}

function UpdatePatientStatusFields({
    node,
    onChange,
    readOnly,
}: {
    node: UpdatePatientStatusNode
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    return (
        <>
            <Field label="Internal status">
                <Select
                    value={node.status}
                    disabled={readOnly}
                    onValueChange={(value) => onChange({ ...node, status: value })}
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectGroup>
                            <SelectLabel>Internal outcomes</SelectLabel>
                            <SelectItem value="needs_staff_followup">Needs staff follow-up</SelectItem>
                            <SelectItem value="patient_asked_question">Patient asked question</SelectItem>
                            <SelectItem value="callback_requested">Callback requested</SelectItem>
                            <SelectItem value="reschedule_or_followup_needed">Reschedule/follow-up needed</SelectItem>
                            <SelectItem value="do_not_call_requested">Do not call requested</SelectItem>
                            <SelectItem value="no_answer">No answer</SelectItem>
                            <SelectItem value="ai_call_failed">AI call failed</SelectItem>
                            <SelectItem value="post_op_followup_needed">Post-op follow-up needed</SelectItem>
                            <SelectItem value="post_op_complete">Post-op complete</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Legacy appointment labels</SelectLabel>
                            <SelectItem value="appointment_confirmed">Appointment confirmed</SelectItem>
                            <SelectItem value="appointment_cancelled">Appointment cancelled</SelectItem>
                            <SelectItem value="reschedule_requested">Reschedule requested</SelectItem>
                        </SelectGroup>
                    </SelectContent>
                </Select>
            </Field>
            <Field label="Note" hint="Optional internal note stored with the status event.">
                <Textarea
                    value={node.note_template ?? ""}
                    disabled={readOnly}
                    placeholder="e.g. Confirmation call outcome: {{call_outcome}}"
                    onChange={(e) => onChange({ ...node, note_template: e.target.value })}
                />
            </Field>
        </>
    )
}

function UpdateGoTrackerAppointmentFields({
    node,
    def,
    onChange,
    readOnly,
}: {
    node: UpdateGoTrackerAppointmentNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const update = (patch: Partial<UpdateGoTrackerAppointmentNode>) => onChange({ ...node, ...patch })
    return (
        <>
            <Field label="Status">
                <Select
                    value={node.status_id ? String(node.status_id) : NONE}
                    disabled={readOnly}
                    onValueChange={(value) => update({ status_id: value === NONE ? null : toInt(value, 1) })}
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value={NONE}>Do not change</SelectItem>
                        {GOTRACKER_STATUS_OPTIONS.map((status) => (
                            <SelectItem key={status.id} value={String(status.id)}>
                                {status.id} · {status.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </Field>

            <div className="grid grid-cols-2 gap-2">
                <Field label="Confirmed">
                    <TriStateBooleanSelect
                        value={node.confirmed}
                        disabled={readOnly}
                        trueLabel="Set true"
                        falseLabel="Set false"
                        onChange={(value) => update({ confirmed: value })}
                    />
                </Field>
                <Field label="Preconfirmed">
                    <TriStateBooleanSelect
                        value={node.preconfirmed}
                        disabled={readOnly}
                        trueLabel="Set true"
                        falseLabel="Set false"
                        onChange={(value) => update({ preconfirmed: value })}
                    />
                </Field>
            </div>

            <div className="space-y-3 rounded-md border border-border p-3">
                <Label className="text-sm">Appointment update</Label>
                <Field label="Start time">
                    <Input
                        value={node.start_time ?? ""}
                        disabled={readOnly}
                        placeholder="2026-08-12T14:30 or {{new_start_time}}"
                        onChange={(e) => update({ start_time: e.target.value.trim() || null })}
                    />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                    <Field label="End time">
                        <Input
                            value={node.end_time ?? ""}
                            disabled={readOnly}
                            placeholder="optional"
                            onChange={(e) => update({ end_time: e.target.value.trim() || null })}
                        />
                    </Field>
                    <Field label="Duration minutes">
                        <Input
                            type="number"
                            min={1}
                            value={node.duration_min ?? ""}
                            disabled={readOnly}
                            placeholder="45"
                            onChange={(e) => update({ duration_min: e.target.value ? Math.max(1, toInt(e.target.value, 1)) : null })}
                        />
                    </Field>
                </div>
                <div className="grid grid-cols-2 gap-2">
                    <Field label="Provider ID">
                        <Input
                            value={node.provider_id ?? ""}
                            disabled={readOnly}
                            placeholder="{{ProviderId}}"
                            onChange={(e) => update({ provider_id: e.target.value.trim() || null })}
                        />
                    </Field>
                    <Field label="Operatory ID">
                        <Input
                            value={node.operatory_id ?? ""}
                            disabled={readOnly}
                            placeholder="optional"
                            onChange={(e) => update({ operatory_id: e.target.value.trim() || null })}
                        />
                    </Field>
                </div>
                <Field label="Patient ID">
                    <Input
                        value={node.patient_id ?? ""}
                        disabled={readOnly}
                        placeholder="{{ContactId}}"
                        onChange={(e) => update({ patient_id: e.target.value.trim() || null })}
                    />
                </Field>
                <Field label="Reason">
                    <Input
                        value={node.reason ?? ""}
                        disabled={readOnly}
                        placeholder="{{Reason}}"
                        onChange={(e) => update({ reason: e.target.value.trim() || null })}
                    />
                </Field>
            </div>

            <ContextPreview triggerType={def.trigger.type} />
        </>
    )
}

function JsonMapperFields({
    node,
    onChange,
    readOnly,
}: {
    node: JsonMapperNode
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const updateMapping = (i: number, patch: Partial<JsonMapperNode["mappings"][number]>) => {
        const mappings = node.mappings.map((mapping, idx) => (idx === i ? { ...mapping, ...patch } : mapping))
        onChange({ ...node, mappings })
    }
    const addMapping = () => onChange({
        ...node,
        mappings: [...node.mappings, { source_path: "gotracker_payload.appointment.reasons", target_field: "appointment_reasons", default_value: null }],
    })
    const removeMapping = (i: number) => onChange({ ...node, mappings: node.mappings.filter((_, idx) => idx !== i) })

    return (
        <div className="space-y-2">
            <Label className="text-sm">Mappings</Label>
            {node.mappings.map((mapping, i) => (
                <div key={i} className="space-y-2 rounded-md border border-border p-2">
                    <div className="grid gap-2 sm:grid-cols-2">
                        <Field label="Source path">
                            <Input
                                value={mapping.source_path}
                                disabled={readOnly}
                                placeholder="gotracker_payload.appointment.reasons"
                                onChange={(e) => updateMapping(i, { source_path: e.target.value })}
                            />
                        </Field>
                        <Field label="Target field">
                            <Input
                                value={mapping.target_field}
                                disabled={readOnly}
                                placeholder="appointment_reasons"
                                onChange={(e) => updateMapping(i, { target_field: e.target.value })}
                            />
                        </Field>
                    </div>
                    {contextValueAtPath(SAMPLE_WORKFLOW_CONTEXT, mapping.source_path) === undefined && (
                        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
                            Path not found in sample context.
                        </p>
                    )}
                    {contextValueAtPath(SAMPLE_WORKFLOW_CONTEXT, mapping.source_path) !== undefined && (
                        <p className="rounded-md bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
                            {formatContextValue(contextValueAtPath(SAMPLE_WORKFLOW_CONTEXT, mapping.source_path))}
                        </p>
                    )}
                    <Field label="Default value">
                        <Input
                            value={ruleValueToText(mapping.default_value)}
                            disabled={readOnly}
                            placeholder="unknown"
                            onChange={(e) => updateMapping(i, { default_value: e.target.value || null })}
                        />
                    </Field>
                    {!readOnly && node.mappings.length > 1 && (
                        <Button variant="ghost" size="sm" className="gap-1.5 text-destructive hover:text-destructive" onClick={() => removeMapping(i)}>
                            <Trash2 className="h-3.5 w-3.5" /> Remove mapping
                        </Button>
                    )}
                </div>
            ))}
            {!readOnly && (
                <Button variant="outline" size="sm" className="gap-1.5" onClick={addMapping}>
                    <Plus className="h-3.5 w-3.5" /> Add mapping
                </Button>
            )}
        </div>
    )
}

function ContextPreview({ triggerType }: { triggerType: TriggerType }) {
    const [open, setOpen] = useState(false)
    const fields = contextFieldsForTrigger(triggerType)
    if (fields.length === 0) return null
    const entries = Object.entries(GOTRACKER_APPOINTMENT_WEBHOOK_SAMPLE.data)
    return (
        <div className="rounded-md border border-border">
            <button
                type="button"
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
                onClick={() => setOpen((value) => !value)}
            >
                <div>
                    <Label className="text-sm">Context preview</Label>
                    <p className="text-xs text-muted-foreground">Incoming GoTracker appointment webhook.</p>
                </div>
                <ChevronDown className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
            </button>
            {open && (
                <div className="max-h-80 space-y-1 overflow-y-auto border-t border-border p-3">
                    {entries.map(([key, value]) => (
                        <div key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 text-xs">
                            <span className="truncate text-muted-foreground">{key}</span>
                            <span className="truncate font-mono">{formatContextValue(value)}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

function LlmFields({
    node,
    def,
    onChange,
    readOnly,
}: {
    node: LlmNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const [models, setModels] = useState<WorkflowLlmModel[]>([])
    const [defaultModel, setDefaultModel] = useState(node.model ?? "")
    const [modelLoadFailed, setModelLoadFailed] = useState(false)
    const [variableOpen, setVariableOpen] = useState(false)
    const variables = contextFieldsForTrigger(def.trigger.type)
    useEffect(() => {
        let active = true
        listWorkflowLlmModels()
            .then((result) => {
                if (!active) return
                setModels(result.models)
                setDefaultModel(result.default_model)
                setModelLoadFailed(false)
            })
            .catch(() => {
                if (!active) return
                setModelLoadFailed(true)
            })
        return () => {
            active = false
        }
    }, [])

    const selectedModel = node.model ?? defaultModel
    const modelChoices = ensureModelChoice(models, selectedModel)
    const update = (patch: Partial<LlmNode>) => {
        onChange({
            ...node,
            source_field: node.source_field || "appointment_reason",
            output_field: node.output_field || "llm_result",
            output_mode: "text",
            max_output_tokens: node.max_output_tokens ?? 512,
            include_context: true,
            require_model: true,
            allow_keyword_fallback: false,
            labels: node.labels ?? [],
            label_rules: node.label_rules ?? [],
            fallback_label: node.fallback_label ?? null,
            json_schema: node.json_schema ?? null,
            ...patch,
        })
    }
    const insertVariable = (name: string) => {
        const token = `{{${name}}}`
        const base = node.prompt_template.trimEnd()
        update({ prompt_template: base ? `${base} ${token}` : token })
        setVariableOpen(false)
    }

    return (
        <>
            <Field label="Model">
                <Select
                    value={selectedModel || undefined}
                    disabled={readOnly || modelChoices.length === 0}
                    onValueChange={(value) => update({ model: value })}
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        {modelChoices.map((model) => (
                            <SelectItem key={model.id} value={model.id}>{model.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                {modelLoadFailed && (
                    <p className="text-xs text-muted-foreground">Model list unavailable.</p>
                )}
            </Field>
            <Field label="Prompt">
                <div className="mb-2 flex justify-end">
                    <Popover open={variableOpen} onOpenChange={setVariableOpen}>
                        <PopoverTrigger asChild>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                disabled={readOnly || variables.length === 0}
                                className="h-8 gap-2"
                            >
                                <Plus className="h-3.5 w-3.5" />
                                Insert variable
                            </Button>
                        </PopoverTrigger>
                        <PopoverContent align="end" className="w-80 p-0">
                            <div className="border-b border-border px-3 py-2 text-sm font-medium">
                                GoTracker appointment payload
                            </div>
                            <div className="max-h-72 overflow-y-auto p-1">
                                {variables.map((field) => (
                                    <button
                                        key={field.name}
                                        type="button"
                                        className="flex w-full items-center justify-between gap-3 rounded-sm px-2 py-2 text-left text-sm hover:bg-accent"
                                        onClick={() => insertVariable(field.name)}
                                    >
                                        <span>{field.label}</span>
                                        <span className="truncate font-mono text-xs text-muted-foreground">
                                            {formatContextValue(field.sample)}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        </PopoverContent>
                    </Popover>
                </div>
                <Textarea
                    rows={8}
                    value={node.prompt_template}
                    disabled={readOnly}
                    placeholder="Write the instruction for the AI action."
                    onChange={(e) => update({ prompt_template: e.target.value })}
                />
            </Field>
        </>
    )
}

function ConditionFields({
    node,
    def,
    onChange,
    readOnly,
}: {
    node: ConditionNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const contextFields = contextFieldsForTrigger(def.trigger.type)
    const contextFieldNames = new Set(contextFields.map((field) => field.name))
    const updateRule = (i: number, patch: Partial<ConditionRule>) => {
        const rules = node.rules.map((r, idx) => (idx === i ? { ...r, ...patch } : r))
        onChange({ ...node, rules })
    }
    const addRule = () => onChange({ ...node, rules: [...node.rules, { field: "", op: "eq", value: "" }] })
    const removeRule = (i: number) => onChange({ ...node, rules: node.rules.filter((_, idx) => idx !== i) })

    return (
        <>
            <Field label="Match logic">
                <Select
                    value={node.logic ?? "AND"}
                    disabled={readOnly}
                    onValueChange={(v) => onChange({ ...node, logic: v as "AND" | "OR" })}
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="AND">All rules (AND)</SelectItem>
                        <SelectItem value="OR">Any rule (OR)</SelectItem>
                    </SelectContent>
                </Select>
            </Field>

            <div className="space-y-2">
                <Label className="text-sm">Rules</Label>
                {node.rules.map((rule, i) => {
                    const needsValue = rule.op !== "is_null" && rule.op !== "is_not_null"
                    const selectedKnownField = contextFieldNames.has(rule.field) ? rule.field : CUSTOM_CONDITION_FIELD
                    return (
                        <div key={i} className="space-y-2 rounded-md border border-border p-2">
                            <div className="flex gap-2">
                                <Select
                                    value={selectedKnownField}
                                    disabled={readOnly}
                                    onValueChange={(value) => {
                                        if (value === CUSTOM_CONDITION_FIELD) {
                                            updateRule(i, { field: "" })
                                        } else {
                                            updateRule(i, { field: value })
                                        }
                                    }}
                                >
                                    <SelectTrigger className="flex-1">
                                        <SelectValue placeholder="Select field" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectGroup>
                                            <SelectLabel>GoTracker appointment payload</SelectLabel>
                                            {contextFields.map((field) => (
                                                <SelectItem key={field.name} value={field.name}>{field.label}</SelectItem>
                                            ))}
                                        </SelectGroup>
                                        <SelectGroup>
                                            <SelectLabel>Other</SelectLabel>
                                            <SelectItem value={CUSTOM_CONDITION_FIELD}>Custom field/path</SelectItem>
                                        </SelectGroup>
                                    </SelectContent>
                                </Select>
                                {!readOnly && node.rules.length > 1 && (
                                    <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" onClick={() => removeRule(i)}>
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </Button>
                                )}
                            </div>
                            {selectedKnownField === CUSTOM_CONDITION_FIELD && (
                                <Input
                                    placeholder="custom field or JSON path"
                                    value={rule.field}
                                    disabled={readOnly}
                                    onChange={(e) => updateRule(i, { field: e.target.value })}
                                />
                            )}
                            {selectedKnownField !== CUSTOM_CONDITION_FIELD && (
                                <p className="rounded-md bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
                                    {formatContextValue(contextFields.find((field) => field.name === rule.field)?.sample)}
                                </p>
                            )}
                            <div className="flex gap-2">
                                <Select
                                    value={rule.op}
                                    disabled={readOnly}
                                    onValueChange={(v) => updateRule(i, { op: v as ConditionOp })}
                                >
                                    <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {CONDITION_OPS.map((op) => (
                                            <SelectItem key={op} value={op}>{CONDITION_OP_LABELS[op]}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                {needsValue && (
                                    <Input
                                        className="flex-1"
                                        placeholder={rule.op === "in" || rule.op === "in_case_insensitive" || rule.op === "not_in" ? "a, b, c" : "value"}
                                        value={ruleValueToText(rule.value)}
                                        disabled={readOnly}
                                        onChange={(e) => updateRule(i, { value: textToRuleValue(e.target.value, rule.op) })}
                                    />
                                )}
                            </div>
                        </div>
                    )
                })}
                {!readOnly && (
                    <Button variant="outline" size="sm" className="gap-1.5" onClick={addRule}>
                        <Plus className="h-3.5 w-3.5" /> Add rule
                    </Button>
                )}
            </div>

            <NextStepField
                label="If true → go to"
                def={def}
                currentId={node.id}
                value={node.true_next_node_id}
                onChange={(v) => onChange({ ...node, true_next_node_id: v })}
                readOnly={readOnly}
            />
            <NextStepField
                label="If false → go to"
                def={def}
                currentId={node.id}
                value={node.false_next_node_id}
                onChange={(v) => onChange({ ...node, false_next_node_id: v })}
                readOnly={readOnly}
            />
        </>
    )
}

// ---------------------------------------------------------------------------
// Shared field helpers
// ---------------------------------------------------------------------------
function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
    return (
        <div className="space-y-1.5">
            <Label className="text-sm">{label}</Label>
            {children}
            {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        </div>
    )
}

function MessageField({
    label,
    value,
    onChange,
    triggerType,
    channel,
    readOnly,
}: {
    label: string
    value: string
    onChange: (v: string) => void
    triggerType: TriggerType
    channel: "sms" | "email" | "voice"
    readOnly?: boolean
}) {
    const mergeFields = useMergeFields({ triggerType, channel })
    const grouped = mergeFields.reduce<Record<string, typeof mergeFields>>((acc, field) => {
        const group = field.group ?? "other"
        acc[group] = acc[group] ?? []
        acc[group].push(field)
        return acc
    }, {})
    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between">
                <Label className="text-sm">{label}</Label>
                {!readOnly && (
                    <Select value="" onValueChange={(token) => onChange(`${value}${token}`)}>
                        <SelectTrigger className="h-7 w-[150px] text-xs">
                            <SelectValue placeholder="Insert field" />
                        </SelectTrigger>
                        <SelectContent>
                            {Object.entries(grouped).map(([group, fields]) => (
                                <SelectGroup key={group}>
                                    <SelectLabel className="text-[11px] uppercase text-muted-foreground">
                                        {group}
                                    </SelectLabel>
                                    {fields.map((f) => (
                                        <SelectItem key={f.token} value={f.token} className="text-xs">
                                            {f.label}
                                        </SelectItem>
                                    ))}
                                </SelectGroup>
                            ))}
                        </SelectContent>
                    </Select>
                )}
            </div>
            <Textarea
                rows={5}
                value={value}
                disabled={readOnly}
                placeholder="Type the message. Use merge fields like {{patient_first_name}}."
                onChange={(e) => onChange(e.target.value)}
            />
        </div>
    )
}

function AttemptsField({ value, onChange, readOnly }: { value: number; onChange: (v: number) => void; readOnly?: boolean }) {
    return (
        <Field label="Max attempts" hint="1–3 delivery attempts per contact.">
            <Select value={String(value)} disabled={readOnly} onValueChange={(v) => onChange(toInt(v, 1))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                    {[1, 2, 3].map((n) => (
                        <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </Field>
    )
}

function NextStepField({
    label,
    def,
    currentId,
    value,
    onChange,
    readOnly,
}: {
    label: string
    def: WorkflowDefinition
    currentId: string
    value: string
    onChange: (v: string) => void
    readOnly?: boolean
}) {
    const options = def.nodes.filter((n) => n.id !== currentId)
    return (
        <Field label={label}>
            <Select
                value={value || NONE}
                disabled={readOnly}
                onValueChange={(v) => onChange(v === NONE ? "" : v)}
            >
                <SelectTrigger><SelectValue placeholder="Not connected" /></SelectTrigger>
                <SelectContent>
                    <SelectItem value={NONE}>— Not connected —</SelectItem>
                    {options.map((n) => (
                        <SelectItem key={n.id} value={n.id}>
                            {NODE_META[n.type].label} · {n.id}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </Field>
    )
}

function TriStateBooleanSelect({
    value,
    disabled,
    trueLabel,
    falseLabel,
    onChange,
}: {
    value?: boolean | null
    disabled?: boolean
    trueLabel: string
    falseLabel: string
    onChange: (value: boolean | null) => void
}) {
    return (
        <Select
            value={value === null || value === undefined ? NONE : value ? "true" : "false"}
            disabled={disabled}
            onValueChange={(next) => onChange(next === NONE ? null : next === "true")}
        >
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
                <SelectItem value={NONE}>Do not change</SelectItem>
                <SelectItem value="true">{trueLabel}</SelectItem>
                <SelectItem value="false">{falseLabel}</SelectItem>
            </SelectContent>
        </Select>
    )
}

// ---------------------------------------------------------------------------
// value helpers
// ---------------------------------------------------------------------------
function defaultTrigger(type: TriggerType): WorkflowTrigger {
    switch (type) {
        case "appointment_offset":
            return { type, offset_hours: -24 }
        case "appointment_state_changed":
            return { type, status_ids: [], confirmed: true, preconfirmed: null, campaign_goal: "post_op_followup" }
        case "recall_scan":
            return { type, recall_interval_months: 6 }
        case "manual":
            return { type }
        case "bulk_import":
            return { type }
        case "callback_requested":
            return { type }
        case "patient_status_changed":
            return {
                type,
                statuses: ["appointment_confirmed"],
                campaign_goal: "post_op_followup",
            }
    }
}

function toInt(v: string, fallback: number): number {
    const n = parseInt(v, 10)
    return Number.isFinite(n) ? n : fallback
}
function toFloat(v: string, fallback: number): number {
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : fallback
}
function clamp(value: number, min: number, max: number): number {
    return Math.min(max, Math.max(min, value))
}
function round2(n: number): number {
    return Math.round(n * 100) / 100
}
function ruleValueToText(value: ConditionRule["value"]): string {
    if (value === null || value === undefined) return ""
    if (Array.isArray(value)) return value.join(", ")
    return String(value)
}
function textToRuleValue(text: string, op: ConditionOp): ConditionRule["value"] {
    if (op === "in" || op === "in_case_insensitive" || op === "not_in") {
        return text
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
    }
    return text
}

function ensureModelChoice(models: WorkflowLlmModel[], selectedModel: string): WorkflowLlmModel[] {
    if (!selectedModel || models.some((model) => model.id === selectedModel)) return models
    return [{ id: selectedModel, label: selectedModel, owned_by: null }, ...models]
}
