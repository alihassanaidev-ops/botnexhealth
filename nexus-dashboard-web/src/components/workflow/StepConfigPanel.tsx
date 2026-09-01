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
import {
    Check,
    ChevronDown,
    ChevronsUpDown,
    ChevronUp,
    GitBranch,
    Flag,
    Plus,
    Search,
    Trash2,
} from "lucide-react"
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
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    NODE_META,
    CONDITION_OP_LABELS,
    TRIGGER_META,
    selectableTriggerTypes,
} from "@/lib/workflow/catalog"
import {
    listCampaignEmailTemplates,
    type CampaignEmailTemplate,
} from "@/lib/campaign-email-templates-api"
import {
    listPhoneCountryRegions,
    listPmsAppointmentStatuses,
    listWorkflowLlmModels,
    type PhoneCountryRegion,
    type PmsAppointmentStatus,
} from "@/lib/workflow-api"
import { SmsPreview, EmailPreview } from "./MessagePreview"
import FilterEditor from "./FilterEditor"
import { useMergeFields } from "@/lib/workflow/merge-fields"
import {
    addVoiceOutcomeBranch,
    createTrigger,
    TRIGGER_NODE_ID,
    VOICE_OUTCOME_BRANCH_VALUES,
} from "@/lib/workflow/graph"
import {
    contextFieldsForTrigger,
    contextValueAtPath,
    formatContextValue,
    GOTRACKER_APPOINTMENT_WEBHOOK_SAMPLE,
    SAMPLE_WORKFLOW_CONTEXT,
} from "@/lib/workflow/context-fields"
import type {
    CachedAppointmentType,
    CachedProvider,
    OutboundVoiceProfile,
    RetellSmsChatProfile,
} from "@/types"
import type {
    BookingLinkNode,
    ConditionNode,
    ConditionOp,
    ConditionRule,
    FilterExpression,
    FilterOp,
    FilterRule,
    SwitchCase,
    SwitchNode,
    DripNode,
    EmailRecipient,
    JsonMapperNode,
    LlmNode,
    PatientRegistrationNode,
    RetellSmsConversationNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    SmsResponseMapping,
    SmsReplyWaitConfig,
    EmailReplyWaitConfig,
    WaitForConfig,
    TimeWaitConfig,
    TriggerType,
    UpdateAppointmentNode,
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
/**
 * Last-resort copy of the GoTracker disposition catalog, used only until the
 * served catalog loads (and offline). The backend owns the real list in
 * `src/app/pms/gotracker/statuses.py`.
 */
const FALLBACK_GOTRACKER_STATUSES: PmsAppointmentStatus[] = [
    { id: 1, key: "booked", label: "Booked", semantics: "booked", readable: true, writable: true, description: "" },
    { id: 2, key: "booked_waiting", label: "Booked + Waiting", semantics: "waiting", readable: true, writable: true, description: "" },
    { id: 3, key: "cancelled", label: "Cancelled", semantics: "cancelled", readable: true, writable: true, description: "" },
    { id: 4, key: "late", label: "Late", semantics: "late", readable: true, writable: true, description: "" },
    { id: 5, key: "no_show", label: "No Show", semantics: "no_show", readable: true, writable: true, description: "" },
    { id: 6, key: "office_cancel", label: "Office Cancel", semantics: "cancelled", readable: true, writable: true, description: "" },
    { id: 7, key: "pending", label: "Pending", semantics: "pending", readable: true, writable: true, description: "" },
    { id: 8, key: "short_cancel", label: "Short Cancel", semantics: "cancelled", readable: true, writable: true, description: "" },
    { id: 9, key: "waiting", label: "Waiting", semantics: "waiting", readable: true, writable: true, description: "" },
]

/** Module-level cache so every panel instance shares one fetch. */
let pmsStatusCache: PmsAppointmentStatus[] | null = null

/** The served PMS status catalog, falling back until it loads. */
function usePmsAppointmentStatuses(): PmsAppointmentStatus[] {
    const [statuses, setStatuses] = useState<PmsAppointmentStatus[]>(
        pmsStatusCache ?? FALLBACK_GOTRACKER_STATUSES,
    )
    useEffect(() => {
        if (pmsStatusCache) return
        let active = true
        listPmsAppointmentStatuses()
            .then((loaded) => {
                if (!loaded.length) return
                pmsStatusCache = loaded
                if (active) setStatuses(loaded)
            })
            .catch(() => {
                /* keep the fallback so the picker stays usable */
            })
        return () => {
            active = false
        }
    }, [])
    return statuses
}
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
    appointmentTypes?: CachedAppointmentType[]
    providers?: CachedProvider[]
    retellSmsProfiles?: RetellSmsChatProfile[]
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
                        onValueChange={(v) => onChange(createTrigger(v as TriggerType))}
                        disabled={readOnly}
                    >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                            {selectableTriggerTypes(trigger.type).map((t) => (
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
                        <Field
                            label="Chair Flow states"
                            htmlFor="trigger-flow-states"
                            hint="Comma-separated exact Tracker wording, for example Completed. Status and confirmation fields below are optional AND filters."
                        >
                            <Input
                                id="trigger-flow-states"
                                defaultValue={(trigger.flow_states ?? []).join(", ")}
                                disabled={readOnly}
                                placeholder="Completed"
                                onChange={(e) => {
                                    onChange({
                                        ...trigger,
                                        flow_states: textToStringList(e.target.value),
                                    })
                                }}
                            />
                        </Field>
                        <Field
                            label="GoTracker statuses (optional filter)"
                            hint="Select none to match any status. Selecting several matches any one of them."
                        >
                            <StatusIdMultiSelect
                                selected={trigger.status_ids}
                                disabled={readOnly}
                                ariaLabel="GoTracker statuses (optional filter)"
                                onChange={(status_ids) => onChange({ ...trigger, status_ids })}
                            />
                        </Field>
                        <div className="grid grid-cols-2 gap-2">
                            <Field label="Confirmed (optional AND filter)">
                                <TriStateBooleanSelect
                                    value={trigger.confirmed}
                                    disabled={readOnly}
                                    ariaLabel="Confirmed (optional AND filter)"
                                    nullLabel="Do not restrict"
                                    trueLabel="Is true"
                                    falseLabel="Is false"
                                    onChange={(value) => onChange({ ...trigger, confirmed: value })}
                                />
                            </Field>
                            <Field label="Preconfirmed (optional AND filter)">
                                <TriStateBooleanSelect
                                    value={trigger.preconfirmed}
                                    disabled={readOnly}
                                    ariaLabel="Preconfirmed (optional AND filter)"
                                    nullLabel="Do not restrict"
                                    trueLabel="Is true"
                                    falseLabel="Is false"
                                    onChange={(value) => onChange({ ...trigger, preconfirmed: value })}
                                />
                            </Field>
                        </div>
                        <Field
                            label="Latest follow-up window (hours after flow change)"
                            htmlFor="trigger-max-followup-hours"
                            hint="Optional. Post-op calls are blocked after this many hours from FlowChange."
                        >
                            <Input
                                id="trigger-max-followup-hours"
                                type="number"
                                min={0}
                                max={168}
                                step={1}
                                value={trigger.max_followup_delay_hours ?? ""}
                                disabled={readOnly}
                                placeholder="72"
                                onChange={(e) => onChange({
                                    ...trigger,
                                    max_followup_delay_hours: e.target.value === ""
                                        ? null
                                        : toInt(e.target.value, 0),
                                })}
                            />
                        </Field>
                        <p className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
                            Any listed Chair Flow state can match. Non-empty status and confirmation filters are
                            combined with it using AND. For Completed-only post-op, leave those filters unrestricted.
                        </p>
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
                {trigger.type === "sms_reply" && (
                    <>
                        <Field label="Reply tokens" hint="Optional comma-separated whole-token filters. Leave empty for any non-compliance inbound SMS.">
                            <Textarea
                                value={(trigger.tokens ?? []).join(", ")}
                                disabled={readOnly}
                                placeholder="pricing, reschedule, question"
                                onChange={(e) => onChange({
                                    ...trigger,
                                    tokens: textToStringList(e.target.value),
                                })}
                            />
                        </Field>
                        <Field label="Campaign goal">
                            <Input
                                value={trigger.campaign_goal ?? ""}
                                disabled={readOnly}
                                placeholder="inbound_sms_followup"
                                onChange={(e) => onChange({
                                    ...trigger,
                                    campaign_goal: e.target.value.trim() || null,
                                })}
                            />
                        </Field>
                        <ContextPreview triggerType={trigger.type} />
                    </>
                )}

                <TriggerEligibilityFilter
                    trigger={trigger}
                    onChange={onChange}
                    readOnly={readOnly}
                />
            </div>
        </>
    )
}

/**
 * Optional eligibility filter on the trigger.
 *
 * Evaluated before a run is created, so an ineligible subject costs one
 * in-memory check instead of a run row, a step row and analytics rows that an
 * opening condition node would then immediately exit.
 */
function TriggerEligibilityFilter({
    trigger,
    onChange,
    readOnly,
}: {
    trigger: WorkflowTrigger
    onChange: (t: WorkflowTrigger) => void
    readOnly?: boolean
}) {
    const filter = trigger.filter ?? null

    if (!filter) {
        return (
            <div className="rounded-md border border-dashed border-border p-3">
                <p className="text-sm font-medium">Enrollment filter</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                    Optional. Decide eligibility here rather than in a first step, so
                    contacts who do not qualify never enter the campaign at all.
                </p>
                {!readOnly && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="mt-2 h-7 gap-1.5 text-xs"
                        onClick={() =>
                            onChange({
                                ...trigger,
                                filter: { kind: "rule", field: "", op: "eq", value: "" },
                            })
                        }
                    >
                        <Plus className="h-3.5 w-3.5" /> Add filter
                    </Button>
                )}
            </div>
        )
    }

    return (
        <div className="space-y-2 rounded-md border border-border p-3">
            <div className="flex items-start justify-between gap-2">
                <div>
                    <p className="text-sm font-medium">Enrollment filter</p>
                    <p className="text-xs text-muted-foreground">Only enroll when this matches.</p>
                </div>
                {!readOnly && (
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => onChange({ ...trigger, filter: null })}
                    >
                        Remove
                    </Button>
                )}
            </div>
            <FilterEditor
                value={filter}
                triggerType={trigger.type}
                readOnly={readOnly}
                onChange={(next) => onChange({ ...trigger, filter: next })}
            />
        </div>
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
    appointmentTypes,
    providers,
    retellSmsProfiles,
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
                {node.type === "retell_sms_conversation" && (
                    <RetellSmsFields
                        node={node}
                        onChange={onNodeChange}
                        profiles={retellSmsProfiles ?? []}
                        readOnly={readOnly}
                    />
                )}
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
                {node.type === "wait" && <WaitFields key={`${node.id}-${node.wait_for.type}`} node={node} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "drip" && <DripFields node={node} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "update_patient_status" && (
                    <UpdatePatientStatusFields node={node} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "update_appointment" && (
                    <UpdateAppointmentFields node={node} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "update_gotracker_appointment" && (
                    <UpdateGoTrackerAppointmentFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />
                )}
                {node.type === "booking_link" && (
                    <BookingLinkFields
                        node={node}
                        appointmentTypes={appointmentTypes ?? []}
                        providers={providers ?? []}
                        onChange={onNodeChange}
                        readOnly={readOnly}
                    />
                )}
                {node.type === "patient_registration" && (
                    <PatientRegistrationFields
                        node={node}
                        providers={providers ?? []}
                        onChange={onNodeChange}
                        readOnly={readOnly}
                    />
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
                {node.type === "switch" && (
                    <SwitchFields node={node} def={def} onChange={onNodeChange} readOnly={readOnly} />
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

                {(node.type === "send_sms" ||
                    node.type === "retell_sms_conversation" ||
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
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div>
                    <Label className="text-sm">Include STOP opt-out footer</Label>
                    <p className="text-xs text-muted-foreground">
                        Append “Reply STOP to opt out.” unless the message already contains it.
                    </p>
                </div>
                <Switch
                    aria-label="Include STOP opt-out footer"
                    checked={node.include_opt_out_footer ?? true}
                    disabled={readOnly}
                    onCheckedChange={(checked) =>
                        onChange({ ...node, include_opt_out_footer: checked })
                    }
                />
            </div>
            <div className="space-y-1.5">
                <Label className="text-sm">Preview</Label>
                <SmsPreview node={node} />
            </div>
            <AttemptsField value={node.max_attempts ?? 1} onChange={(v) => onChange({ ...node, max_attempts: v })} readOnly={readOnly} />
        </>
    )
}

/** Default config for each wait mode, used when the author switches modes. */
function defaultWaitFor(type: WaitForConfig["type"]): WaitForConfig {
    if (type === "sms_reply") {
        return {
            type: "sms_reply",
            response_window_seconds: 259200,
            response_mappings: [
                { tokens: ["YES", "Y"], context_updates: { sms_reply: "yes" } },
                { tokens: ["NO", "N"], context_updates: { sms_reply: "no" } },
            ],
        }
    }
    if (type === "email_reply") {
        // A week, not three days: email is answered on a slower rhythm and a
        // weekend must not be read as a non-response.
        return {
            type: "email_reply",
            response_window_seconds: 604800,
            response_mappings: [
                { tokens: ["YES", "CONFIRM"], context_updates: { email_reply: "yes" } },
                { tokens: ["NO", "CANCEL"], context_updates: { email_reply: "no" } },
            ],
        }
    }
    return {
        type: "time",
        delay: { delay_type: "duration", duration_seconds: 3600 },
        respect_quiet_hours: true,
    }
}


/**
 * Reply-wait editor shared by the SMS and email channels. `EmailReplyWaitConfig`
 * carries the same fields as `SmsReplyWaitConfig` — the reply mapping is a
 * token-to-context map, not anything SMS-specific — so an author configures both
 * the same way and the two cannot drift apart.
 */
function ReplyWaitFields<T extends SmsReplyWaitConfig | EmailReplyWaitConfig>({
    config,
    onChange,
    readOnly,
}: {
    config: T
    onChange: (config: T) => void
    readOnly?: boolean
}) {
    const isEmail = config.type === "email_reply"
    const defaultWindowSeconds = isEmail ? 604800 : 259200
    const responseWindowHours = Math.round(
        (config.response_window_seconds ?? defaultWindowSeconds) / 3600,
    )
    const mappings = config.response_mappings ?? []
    const updateMapping = (index: number, mapping: SmsResponseMapping) => {
        onChange({
            ...config,
            response_mappings: mappings.map((current, currentIndex) => (
                currentIndex === index ? mapping : current
            )),
        })
    }
    const removeMapping = (index: number) => {
        onChange({
            ...config,
            response_mappings: mappings.filter((_, currentIndex) => currentIndex !== index),
        })
    }
    const addMapping = () => {
        onChange({
            ...config,
            response_mappings: [
                ...mappings,
                { tokens: [], context_updates: { sms_reply: "" } },
            ],
        })
    }

    return (
        <div className="space-y-3">
            <Field label="Response window hours">
                <Input
                    type="number"
                    min={1}
                    max={720}
                    value={responseWindowHours}
                    disabled={readOnly}
                    onChange={(e) => {
                        const hours = Math.max(1, Number(e.target.value || 1))
                        onChange({ ...config, response_window_seconds: hours * 3600 })
                    }}
                />
            </Field>
            <div className="space-y-2">
                <div>
                    <Label className="text-sm">Reply rules</Label>
                    <p className="text-xs text-muted-foreground">
                        Match whole words without regard to capitalization, then continue the workflow or create a staff handoff.
                    </p>
                </div>
                {mappings.map((mapping, index) => {
                    const contextEntry = Object.entries(mapping.context_updates ?? {})[0]
                    const contextField = contextEntry?.[0] ?? "sms_reply"
                    const contextValue = contextEntry?.[1]
                    const action = mapping.handoff_reason ? "handoff" : "continue"
                    const tokensId = `sms-reply-rule-${index}-tokens`
                    const fieldId = `sms-reply-rule-${index}-field`
                    const valueId = `sms-reply-rule-${index}-value`

                    return (
                        <div key={index} className="space-y-3 rounded-md border border-border p-3">
                            <Field
                                label="Accepted replies"
                                hint="Separate alternatives with commas, for example YES, Y."
                                htmlFor={tokensId}
                            >
                                <Input
                                    key={mapping.tokens.join("\u001f")}
                                    id={tokensId}
                                    defaultValue={mapping.tokens.join(", ")}
                                    disabled={readOnly}
                                    placeholder="YES, Y"
                                    onBlur={(event) => updateMapping(index, {
                                        ...mapping,
                                        tokens: textToStringList(event.currentTarget.value),
                                    })}
                                />
                            </Field>
                            <Field label="When matched">
                                <Select
                                    value={action}
                                    disabled={readOnly}
                                    onValueChange={(value) => updateMapping(index, value === "handoff"
                                        ? {
                                            tokens: mapping.tokens,
                                            handoff_reason: mapping.handoff_reason || "sms_reply_requires_staff",
                                        }
                                        : {
                                            tokens: mapping.tokens,
                                            context_updates: mapping.context_updates ?? { sms_reply: "" },
                                        })}
                                >
                                    <SelectTrigger aria-label={`Action for reply rule ${index + 1}`}>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="continue">Continue workflow</SelectItem>
                                        <SelectItem value="handoff">Create staff handoff</SelectItem>
                                    </SelectContent>
                                </Select>
                            </Field>
                            {action === "continue" ? (
                                <div className="grid gap-2 sm:grid-cols-2">
                                    <Field label="Save to context field" htmlFor={fieldId}>
                                        <Input
                                            id={fieldId}
                                            value={contextField}
                                            disabled={readOnly}
                                            placeholder="sms_reply"
                                            onChange={(event) => updateMapping(index, {
                                                tokens: mapping.tokens,
                                                context_updates: {
                                                    [event.target.value.trim() || "sms_reply"]: contextValue ?? "",
                                                },
                                            })}
                                        />
                                    </Field>
                                    <Field label="Save value" htmlFor={valueId}>
                                        <Input
                                            id={valueId}
                                            value={ruleValueToText(contextValue)}
                                            disabled={readOnly}
                                            placeholder="yes"
                                            onChange={(event) => updateMapping(index, {
                                                tokens: mapping.tokens,
                                                context_updates: { [contextField]: event.target.value },
                                            })}
                                        />
                                    </Field>
                                </div>
                            ) : (
                                <Field label="Handoff reason" htmlFor={valueId}>
                                    <Input
                                        id={valueId}
                                        value={mapping.handoff_reason ?? ""}
                                        disabled={readOnly}
                                        placeholder="sms_reply_requires_staff"
                                        onChange={(event) => updateMapping(index, {
                                            tokens: mapping.tokens,
                                            handoff_reason: event.target.value,
                                        })}
                                    />
                                </Field>
                            )}
                            {!readOnly && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="gap-1.5 text-destructive hover:text-destructive"
                                    onClick={() => removeMapping(index)}
                                >
                                    <Trash2 className="h-3.5 w-3.5" /> Remove rule
                                </Button>
                            )}
                        </div>
                    )
                })}
                {!readOnly && (
                    <Button type="button" variant="outline" size="sm" className="gap-1.5" onClick={addMapping}>
                        <Plus className="h-3.5 w-3.5" /> Add reply rule
                    </Button>
                )}
            </div>
        </div>
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
    const [savedTemplates, setSavedTemplates] = useState<CampaignEmailTemplate[]>([])
    const usingSavedTemplate = Boolean(node.template_key)

    useEffect(() => {
        let cancelled = false
        listCampaignEmailTemplates(true)
            .then((list) => {
                if (!cancelled) setSavedTemplates(list)
            })
            // A failed lookup must not break the config panel; the picker simply
            // shows as empty and inline content still works.
            .catch(() => undefined)
        return () => {
            cancelled = true
        }
    }, [])

    // The backend rejects a node carrying both a template key and inline
    // content, so switching clears the other side rather than leaving a
    // leftover that fails validation on publish.
    const onContentModeChange = (mode: string) => {
        if (mode === "template") {
            onChange({
                ...node,
                template_key: savedTemplates[0]?.key ?? "",
                subject_template: "",
                body_template: "",
                html_template: null,
            })
        } else {
            onChange({ ...node, template_key: null })
        }
    }

    // Definitions published before `recipient` existed have no value; the
    // backend reads that as the patient, so mirror it here.
    const recipient = node.recipient ?? { kind: "contact" as const }
    const patientDirected = recipient.kind === "contact" || recipient.kind === "merge_field"

    const setRecipient = (next: EmailRecipient) => onChange({ ...node, recipient: next })

    const onKindChange = (kind: EmailRecipient["kind"]) => {
        if (kind === recipient.kind) return
        // Each variant carries different fields; rebuild rather than merge so a
        // stale `addresses` or `field` can't ride along and fail backend validation.
        if (kind === "contact") setRecipient({ kind: "contact" })
        else if (kind === "staff") setRecipient({ kind: "staff", include_external: true })
        else if (kind === "static") setRecipient({ kind: "static", addresses: [] })
        else setRecipient({ kind: "merge_field", field: "" })
    }

    return (
        <>
            <Field
                label="Send to"
                hint={
                    patientDirected
                        ? "Patient emails respect consent, quiet hours, and carry an unsubscribe link."
                        : "Internal emails skip consent checks, quiet hours, and the unsubscribe footer."
                }
            >
                <Select value={recipient.kind} disabled={readOnly} onValueChange={onKindChange}>
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="contact">The patient</SelectItem>
                        <SelectItem value="staff">Clinic staff</SelectItem>
                        <SelectItem value="static">Specific address</SelectItem>
                        <SelectItem value="merge_field">Address from a merge field</SelectItem>
                    </SelectContent>
                </Select>
            </Field>

            {recipient.kind === "staff" && (
                <Field
                    label="Notification type"
                    hint="Optional. Respects each staff member's notification preferences and includes any external recipients configured for this type."
                >
                    <Input
                        value={recipient.notification_type ?? ""}
                        placeholder="e.g. urgent_alert"
                        disabled={readOnly}
                        onChange={(e) =>
                            setRecipient({
                                ...recipient,
                                notification_type: e.target.value.trim() || null,
                            })
                        }
                    />
                </Field>
            )}

            {recipient.kind === "static" && (
                <Field label="Addresses" hint="Comma-separated. Up to 10.">
                    <Input
                        value={recipient.addresses.join(", ")}
                        placeholder="ops@clinic.com, alerts@clinic.com"
                        disabled={readOnly}
                        onChange={(e) =>
                            setRecipient({
                                ...recipient,
                                addresses: e.target.value
                                    .split(",")
                                    .map((a) => a.trim())
                                    .filter(Boolean),
                            })
                        }
                    />
                </Field>
            )}

            {recipient.kind === "merge_field" && (
                <Field
                    label="Merge field"
                    hint="Treated as a patient email — consent and quiet hours still apply."
                >
                    <Input
                        value={recipient.field}
                        placeholder="e.g. patient_email"
                        disabled={readOnly}
                        onChange={(e) => setRecipient({ ...recipient, field: e.target.value.trim() })}
                    />
                </Field>
            )}

            <Field
                label="Content"
                hint={
                    usingSavedTemplate
                        ? "Edited in Campaign Emails. Changing it there updates every campaign using it."
                        : "Written here and used only by this step."
                }
            >
                <Select
                    value={usingSavedTemplate ? "template" : "inline"}
                    disabled={readOnly}
                    onValueChange={onContentModeChange}
                >
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="inline">Write it here</SelectItem>
                        <SelectItem value="template">Use a saved template</SelectItem>
                    </SelectContent>
                </Select>
            </Field>

            {usingSavedTemplate ? (
                <Field
                    label="Template"
                    hint={
                        savedTemplates.length === 0
                            ? "No templates yet — create one under Campaign Emails."
                            : undefined
                    }
                >
                    <Select
                        value={node.template_key ?? ""}
                        disabled={readOnly || savedTemplates.length === 0}
                        onValueChange={(v) => onChange({ ...node, template_key: v })}
                    >
                        <SelectTrigger>
                            <SelectValue placeholder="Select a template" />
                        </SelectTrigger>
                        <SelectContent>
                            {savedTemplates.map((t) => (
                                <SelectItem key={t.key} value={t.key}>
                                    {t.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </Field>
            ) : (
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
                </>
            )}
            <AttemptsField value={node.max_attempts ?? 1} onChange={(v) => onChange({ ...node, max_attempts: v })} readOnly={readOnly} />
            <Field
                label="If sending fails"
                hint="Continue is for optional emails that should not abandon a run that has already done its real work."
            >
                <Select
                    value={node.on_failure ?? "fail_run"}
                    disabled={readOnly}
                    onValueChange={(v) =>
                        onChange({ ...node, on_failure: v as "fail_run" | "continue" })
                    }
                >
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="fail_run">Stop the workflow</SelectItem>
                        <SelectItem value="continue">Carry on to the next step</SelectItem>
                    </SelectContent>
                </Select>
            </Field>
        </>
    )
}

function RetellSmsFields({
    node,
    onChange,
    profiles,
    readOnly,
}: {
    node: RetellSmsConversationNode
    onChange: (n: WorkflowNode) => void
    profiles: RetellSmsChatProfile[]
    readOnly?: boolean
}) {
    return (
        <>
            <Field
                label="AI SMS agent profile"
                hint="Choose the response agent. Patient, clinic, and appointment context is supplied automatically; Twilio sends and receives every SMS."
            >
                <Select
                    value={node.chat_profile_id || NONE}
                    disabled={readOnly}
                    onValueChange={(value) =>
                        onChange({ ...node, chat_profile_id: value === NONE ? "" : value })
                    }
                >
                    <SelectTrigger><SelectValue placeholder="Choose chat profile" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value={NONE} disabled={profiles.length > 0}>No profile selected</SelectItem>
                        {profiles.map((profile) => (
                            <SelectItem key={profile.id} value={profile.id}>
                                {profile.display_name || profile.purpose || "Unnamed chat profile"}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </Field>
            {profiles.length === 0 && (
                <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-muted-foreground">
                    No Retell SMS chat profiles are configured for this location. Ask a platform admin to add one.
                </p>
            )}
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
            <div className="space-y-3 rounded-md border border-border p-3">
                <div className="space-y-1">
                    <Label className="text-sm">Voicemail</Label>
                    <p className="text-xs text-muted-foreground">
                        Reaching an answering machine is not the same as nobody answering.
                    </p>
                </div>
                <div className="flex items-center justify-between gap-3">
                    <Label htmlFor={`leave-voicemail-${node.id}`} className="text-sm font-normal">
                        Leave a message
                    </Label>
                    <Switch
                        id={`leave-voicemail-${node.id}`}
                        checked={node.leave_voicemail ?? false}
                        disabled={readOnly}
                        onCheckedChange={(checked) => onChange({ ...node, leave_voicemail: checked })}
                    />
                </div>
                <div className="flex items-center justify-between gap-3">
                    <Label
                        htmlFor={`voicemail-consumes-${node.id}`}
                        className="text-sm font-normal"
                    >
                        Voicemail uses up an attempt
                    </Label>
                    <Switch
                        id={`voicemail-consumes-${node.id}`}
                        checked={node.voicemail_consumes_attempt ?? true}
                        disabled={readOnly}
                        onCheckedChange={(checked) =>
                            onChange({ ...node, voicemail_consumes_attempt: checked })
                        }
                    />
                </div>
                <Field
                    label="Attempts to reach the patient"
                    hint="How many counted attempts this step makes. Separate from the retry limit below, which only covers vendor errors."
                >
                    <Input
                        aria-label="Attempts to reach the patient"
                        type="number"
                        min={1}
                        max={10}
                        step={1}
                        value={node.voice_attempt_allowance ?? 1}
                        disabled={readOnly}
                        onChange={(event) =>
                            onChange({
                                ...node,
                                voice_attempt_allowance: toInt(event.target.value, 1),
                            })
                        }
                    />
                </Field>
                <Field
                    label="Maximum dials"
                    hint="A hard stop whatever the outcome. With voicemail not using up an attempt, this is what prevents a number that always goes to voicemail being dialled indefinitely. Must be at least the number of attempts."
                >
                    <Input
                        aria-label="Maximum dials"
                        type="number"
                        min={1}
                        max={20}
                        step={1}
                        value={node.max_dials ?? 5}
                        disabled={readOnly}
                        onChange={(event) =>
                            onChange({ ...node, max_dials: toInt(event.target.value, 5) })
                        }
                    />
                </Field>
                {(node.max_dials ?? 5) < (node.voice_attempt_allowance ?? 1) && (
                    <p className="text-xs text-destructive">
                        Maximum dials is below the number of attempts, so the dial cap would
                        cut the ladder short. Raise it to at least{" "}
                        {node.voice_attempt_allowance ?? 1}.
                    </p>
                )}
            </div>
            <AttemptsField value={node.max_attempts ?? 1} onChange={(v) => onChange({ ...node, max_attempts: v })} readOnly={readOnly} />
        </>
    )
}

function WaitFields({ node, onChange, readOnly }: { node: WaitNode; onChange: (n: WorkflowNode) => void; readOnly?: boolean }) {
    const setWaitFor = (waitFor: WaitNode["wait_for"]) => onChange({ ...node, wait_for: waitFor })

    return (
        <>
            <Field label="Wait for">
                <Select
                    value={node.wait_for.type}
                    disabled={readOnly}
                    onValueChange={(v) => setWaitFor(defaultWaitFor(v as WaitForConfig["type"]))}
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="time">Time</SelectItem>
                        <SelectItem value="sms_reply">SMS reply</SelectItem>
                        <SelectItem value="email_reply">Email reply</SelectItem>
                    </SelectContent>
                </Select>
            </Field>
            {node.wait_for.type === "sms_reply" || node.wait_for.type === "email_reply" ? (
                <ReplyWaitFields
                    config={node.wait_for}
                    onChange={setWaitFor}
                    readOnly={readOnly}
                />
            ) : (
                <TimeWaitFields
                    config={node.wait_for}
                    onChange={setWaitFor}
                    readOnly={readOnly}
                />
            )}
        </>
    )
}

function TimeWaitFields({
    config,
    onChange,
    readOnly,
}: {
    config: TimeWaitConfig
    onChange: (config: TimeWaitConfig) => void
    readOnly?: boolean
}) {
    const delay = config.delay
    const setDelay = (nextDelay: TimeWaitConfig["delay"]) => onChange({ ...config, delay: nextDelay })

    return (
        <>
            <Field label="Timing">
                <Select
                    value={delay.delay_type}
                    disabled={readOnly}
                    onValueChange={(v) =>
                        setDelay(
                            v === "duration"
                                ? { delay_type: "duration", duration_seconds: 3600 }
                                : v === "appointment_relative"
                                  ? { delay_type: "appointment_relative", offset_seconds: -3600, anchor_field: "appointment_at" }
                                  : { delay_type: "calendar", offset_days: 0, time_of_day: "09:00" },
                        )
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
                            setDelay({ delay_type: "duration", duration_seconds: Math.round(toFloat(e.target.value, 0) * 3600) })
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
                                setDelay({ ...delay, offset_days: toInt(e.target.value, 0) })
                            }
                        />
                    </Field>
                    <Field label="Resume time (HH:MM, local)">
                        <Input
                            type="time"
                            value={delay.time_of_day}
                            disabled={readOnly}
                            onChange={(e) => setDelay({ ...delay, time_of_day: e.target.value })}
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
                                setDelay({
                                    delay_type: "appointment_relative",
                                    offset_seconds: offsetSeconds,
                                    anchor_field: delay.anchor_field ?? "appointment_at",
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
                                setDelay({
                                    delay_type: "appointment_relative",
                                    offset_seconds: Math.round(toFloat(e.target.value, 0) * 3600),
                                    anchor_field: delay.anchor_field ?? "appointment_at",
                                })
                            }
                        />
                    </Field>
                </>
            )}
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div>
                    <Label className="text-sm">Respect quiet hours</Label>
                    <p className="text-xs text-muted-foreground">Hold the workflow outside the location's window.</p>
                </div>
                <Switch
                    checked={config.respect_quiet_hours ?? true}
                    disabled={readOnly}
                    onCheckedChange={(checked) => onChange({ ...config, respect_quiet_hours: checked })}
                />
            </div>
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

const LINK_ACTIONS = ["book", "confirm", "reschedule", "cancel"] as const

function providerLabel(p: CachedProvider): string {
    return (
        p.name?.trim() ||
        [p.first_name, p.last_name].filter(Boolean).join(" ").trim() ||
        p.source_id
    )
}

function BookingLinkFields({
    node,
    appointmentTypes,
    providers,
    onChange,
    readOnly,
}: {
    node: BookingLinkNode
    appointmentTypes: CachedAppointmentType[]
    providers: CachedProvider[]
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const update = (patch: Partial<BookingLinkNode>) => onChange({ ...node, ...patch })
    const toggleAction = (action: (typeof LINK_ACTIONS)[number]) => {
        const next = node.actions.includes(action)
            ? node.actions.filter((a) => a !== action)
            : [...node.actions, action]
        // At least one, or the step issues a link that can do nothing.
        if (next.length > 0) update({ actions: next })
    }
    return (
        <>
            <Field label="What the link can do">
                <div className="flex flex-wrap gap-2">
                    {LINK_ACTIONS.map((action) => (
                        <Button
                            key={action}
                            type="button"
                            size="sm"
                            variant={node.actions.includes(action) ? "default" : "outline"}
                            disabled={readOnly}
                            aria-pressed={node.actions.includes(action)}
                            onClick={() => toggleAction(action)}
                        >
                            {action}
                        </Button>
                    ))}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                    A token for anything not selected is refused, even if the wording
                    somehow produced one.
                </p>
            </Field>

            <Field label="Appointment types the patient may choose">
                {appointmentTypes.length > 0 ? (
                    <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-2">
                        {appointmentTypes.map((t) => {
                            const id = t.source_id
                            const checked = node.appointment_type_ids.includes(id)
                            return (
                                <label
                                    key={id}
                                    className="flex items-center gap-2 text-sm cursor-pointer"
                                >
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        disabled={readOnly}
                                        onChange={() =>
                                            update({
                                                appointment_type_ids: checked
                                                    ? node.appointment_type_ids.filter(
                                                          (v) => v !== id,
                                                      )
                                                    : [...node.appointment_type_ids, id],
                                            })
                                        }
                                    />
                                    <span>{t.name}</span>
                                    {t.duration_minutes ? (
                                        <span className="text-xs text-muted-foreground">
                                            {t.duration_minutes} min
                                        </span>
                                    ) : null}
                                </label>
                            )
                        })}
                    </div>
                ) : (
                    // The cache is empty or could not be read. Falling back to
                    // ids keeps the step configurable rather than blocking on a
                    // list that may never arrive.
                    <Input
                        value={node.appointment_type_ids.join(", ")}
                        disabled={readOnly}
                        placeholder="Leave empty for any type"
                        onChange={(e) =>
                            update({
                                appointment_type_ids: e.target.value
                                    .split(",")
                                    .map((v) => v.trim())
                                    .filter(Boolean),
                            })
                        }
                    />
                )}
                <p className="text-xs text-muted-foreground mt-1">
                    Select none to offer every type. This is the link's version of the
                    restriction the voice agent follows for new patients — unlike the
                    agent's, it is enforced by the server, so a booking naming a type
                    outside the list is refused.
                </p>
            </Field>

            <Field label="Identity check">
                <Select
                    value={node.identity_check}
                    disabled={readOnly}
                    onValueChange={(value) =>
                        update({ identity_check: value as BookingLinkNode["identity_check"] })
                    }
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="sensitive">
                            Before rescheduling or cancelling
                        </SelectItem>
                        <SelectItem value="always">Before any action</SelectItem>
                        <SelectItem value="off">Never</SelectItem>
                    </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground mt-1">
                    A link reaches a phone, not a person — households share numbers and
                    old numbers get reassigned. When this is on, the patient confirms
                    their name, date of birth and a phone or email before the link shows
                    an appointment or changes one.
                </p>
            </Field>

            <Field label="How far ahead to offer">
                <Input
                    type="number"
                    min={1}
                    max={60}
                    value={node.window_days}
                    disabled={readOnly}
                    onChange={(e) =>
                        update({ window_days: Number(e.target.value) || 7 })
                    }
                />
            </Field>

            <Field label="Provider (optional)">
                {providers.length > 0 ? (
                    <Select
                        value={node.provider_id ?? "__any__"}
                        disabled={readOnly}
                        onValueChange={(value) =>
                            update({ provider_id: value === "__any__" ? null : value })
                        }
                    >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="__any__">Any provider</SelectItem>
                            {providers
                                // A provider hidden from the voice agent should
                                // not be offered by the link either.
                                .filter((p) => !p.is_hidden)
                                .map((p) => (
                                    <SelectItem key={p.source_id} value={p.source_id}>
                                        {providerLabel(p)}
                                    </SelectItem>
                                ))}
                        </SelectContent>
                    </Select>
                ) : (
                    <Input
                        value={node.provider_id ?? ""}
                        disabled={readOnly}
                        placeholder="Any provider"
                        onChange={(e) => update({ provider_id: e.target.value || null })}
                    />
                )}
            </Field>
        </>
    )
}

function PatientRegistrationFields({
    node,
    providers,
    onChange,
    readOnly,
}: {
    node: PatientRegistrationNode
    providers: CachedProvider[]
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const update = (patch: Partial<PatientRegistrationNode>) =>
        onChange({ ...node, ...patch })
    return (
        <>
            <Field label="Provider new patients are filed under">
                {providers.length > 0 ? (
                    <Select
                        value={node.provider_id || undefined}
                        disabled={readOnly}
                        onValueChange={(value) => update({ provider_id: value })}
                    >
                        <SelectTrigger>
                            <SelectValue placeholder="Choose a provider" />
                        </SelectTrigger>
                        <SelectContent>
                            {providers
                                .filter((p) => !p.is_hidden)
                                .map((p) => (
                                    <SelectItem key={p.source_id} value={p.source_id}>
                                        {providerLabel(p)}
                                    </SelectItem>
                                ))}
                        </SelectContent>
                    </Select>
                ) : (
                    <Input
                        value={node.provider_id}
                        disabled={readOnly}
                        placeholder="PMS provider id"
                        onChange={(e) => update({ provider_id: e.target.value })}
                    />
                )}
                <p className="text-xs text-muted-foreground mt-1">
                    Required. The practice software will not create a patient without
                    one, and it is a clinic decision rather than something the patient
                    can be asked for. Choosing from the list also supplies the id in the
                    form the PMS expects — NexHealth refuses a provider id that is not
                    numeric, and every registration would fail.
                </p>
            </Field>
            <p className="text-xs text-muted-foreground">
                The patient is asked only for date of birth and gender — everything else
                comes from the contact this campaign enrolled.
            </p>
        </>
    )
}

function UpdateAppointmentFields({
    node,
    onChange,
    readOnly,
}: {
    node: UpdateAppointmentNode
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const update = (patch: Partial<UpdateAppointmentNode>) => onChange({ ...node, ...patch })
    return (
        <>
            <Field label="Operation">
                <Select
                    value={node.operation}
                    disabled={readOnly}
                    onValueChange={(value) =>
                        update({ operation: value as UpdateAppointmentNode["operation"] })
                    }
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="confirm">Confirm</SelectItem>
                        <SelectItem value="cancel">Cancel</SelectItem>
                        <SelectItem value="reschedule">Reschedule</SelectItem>
                    </SelectContent>
                </Select>
            </Field>

            {node.operation === "reschedule" && (
                <Field label="New start time">
                    <Input
                        value={node.start_time ?? ""}
                        disabled={readOnly}
                        placeholder="e.g. {{reschedule_start_time}}"
                        onChange={(e) => update({ start_time: e.target.value || null })}
                    />
                </Field>
            )}

            <p className="text-xs text-muted-foreground">
                Writes back through the clinic's PMS, so one workflow works on both
                NexHealth and GoTracker. Rescheduling on NexHealth books the new slot
                and cancels the old one, which produces a new appointment id.
            </p>
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
    // Writability comes from the served catalog, so a disposition Tracker will
    // not accept is disabled here rather than failing mid-run.
    const gotrackerStatuses = usePmsAppointmentStatuses()
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
                        {gotrackerStatuses.map((status) => (
                            <SelectItem
                                key={status.id}
                                value={String(status.id)}
                                disabled={!status.writable}
                            >
                                {status.id} · {status.label}
                                {status.writable ? "" : " (read-only)"}
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
    const legacyRules = node.rules ?? []
    const updateRule = (i: number, patch: Partial<ConditionRule>) => {
        const rules = legacyRules.map((r, idx) => (idx === i ? { ...r, ...patch } : r))
        onChange({ ...node, rules })
    }
    const addRule = () => onChange({ ...node, rules: [...legacyRules, { field: "", op: "eq", value: "" }] })
    const removeRule = (i: number) => onChange({ ...node, rules: legacyRules.filter((_, idx) => idx !== i) })

    // A node authored with the filter DSL uses the shared editor. The legacy
    // rule list below is only rendered for definitions that already use it,
    // because the backend keeps executing those with exact-equality semantics
    // and silently rewriting them could change how a live campaign branches.
    if (node.filter) {
        return (
            <>
                <FilterEditor
                    value={node.filter}
                    triggerType={def.trigger.type}
                    readOnly={readOnly}
                    label="Continue on the Yes branch when"
                    onChange={(filter) => onChange({ ...node, filter })}
                />
                <ConditionBranchFields node={node} def={def} onChange={onChange} readOnly={readOnly} />
            </>
        )
    }

    return (
        <>
            <div className="rounded-md border border-amber-300/60 bg-amber-50 p-2.5 text-xs text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-200">
                <p className="font-medium">Using the original rule list</p>
                <p className="mt-0.5">
                    This step was authored before nested conditions and number/date
                    operators existed. Converting changes how values are compared, so
                    it is left as-is until you choose to switch.
                </p>
                {!readOnly && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="mt-2 h-7 text-xs"
                        onClick={() =>
                            onChange({
                                ...node,
                                rules: undefined,
                                logic: undefined,
                                filter: rulesToFilter(legacyRules, node.logic ?? "AND"),
                            })
                        }
                    >
                        Convert to the new editor
                    </Button>
                )}
            </div>
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
                {legacyRules.map((rule, i) => {
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
                                {!readOnly && legacyRules.length > 1 && (
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

            <ConditionBranchFields node={node} def={def} onChange={onChange} readOnly={readOnly} />
        </>
    )
}

/** The Yes/No targets, shared by both condition authoring shapes. */
function ConditionBranchFields({
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
    return (
        <>
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

/**
 * Lift a legacy rule list into the filter DSL. Only ever run on an explicit
 * author action, never automatically: the legacy evaluator compares exactly
 * where the DSL coerces, so a silent conversion could change branching.
 */
function rulesToFilter(rules: ConditionRule[], logic: "AND" | "OR"): FilterExpression {
    const children: FilterExpression[] = rules.map((rule) => ({
        kind: "rule" as const,
        field: rule.field,
        op: rule.op as FilterOp,
        value: (rule.value ?? null) as FilterRule["value"],
        // Preserve the old exactness so the converted step behaves the same.
        case_sensitive: rule.op === "in" || rule.op === "not_in" ? true : undefined,
    }))
    if (children.length === 1) return children[0]
    return { kind: "group", op: logic === "OR" ? "or" : "and", children }
}

// ---------------------------------------------------------------------------
// Switch
// ---------------------------------------------------------------------------
function SwitchFields({
    node,
    def,
    onChange,
    readOnly,
}: {
    node: SwitchNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    readOnly?: boolean
}) {
    const setCase = (index: number, patch: Partial<SwitchCase>) =>
        onChange({
            ...node,
            cases: node.cases.map((c, i) => (i === index ? { ...c, ...patch } : c)),
        })

    const addCase = () =>
        onChange({
            ...node,
            cases: [
                ...node.cases,
                {
                    label: `Case ${node.cases.length + 1}`,
                    filter: { kind: "rule", field: node.subject || "", op: "eq", value: "" },
                    next_node_id: "",
                },
            ],
        })

    const removeCase = (index: number) =>
        onChange({ ...node, cases: node.cases.filter((_, i) => i !== index) })

    const move = (index: number, delta: number) => {
        const target = index + delta
        if (target < 0 || target >= node.cases.length) return
        const cases = [...node.cases]
        const [moved] = cases.splice(index, 1)
        cases.splice(target, 0, moved)
        onChange({ ...node, cases })
    }

    return (
        <>
            <Field
                label="Routing on"
                hint="Shown on the canvas and in execution traces. Each case still names its own field."
            >
                <Input
                    value={node.subject ?? ""}
                    placeholder="call_outcome"
                    disabled={readOnly}
                    onChange={(e) => onChange({ ...node, subject: e.target.value || null })}
                />
            </Field>

            <div className="space-y-2">
                <div>
                    <Label className="text-sm">Cases</Label>
                    <p className="text-xs text-muted-foreground">
                        Checked top to bottom — the first match wins, so put the specific
                        case above the general one.
                    </p>
                </div>

                {node.cases.map((switchCase, index) => (
                    <div key={index} className="space-y-2 rounded-md border border-border p-2">
                        <div className="flex items-center gap-1.5">
                            <span className="w-5 shrink-0 text-center text-xs tabular-nums text-muted-foreground">
                                {index + 1}
                            </span>
                            <Input
                                className="h-8 flex-1"
                                aria-label={`Case ${index + 1} label`}
                                placeholder="Confirmed"
                                value={switchCase.label}
                                disabled={readOnly}
                                onChange={(e) => setCase(index, { label: e.target.value })}
                            />
                            {!readOnly && (
                                <>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 shrink-0"
                                        aria-label={`Move case ${index + 1} up`}
                                        disabled={index === 0}
                                        onClick={() => move(index, -1)}
                                    >
                                        <ChevronUp className="h-3.5 w-3.5" />
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 shrink-0"
                                        aria-label={`Move case ${index + 1} down`}
                                        disabled={index === node.cases.length - 1}
                                        onClick={() => move(index, 1)}
                                    >
                                        <ChevronDown className="h-3.5 w-3.5" />
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 shrink-0"
                                        aria-label={`Remove case ${index + 1}`}
                                        disabled={node.cases.length <= 1}
                                        onClick={() => removeCase(index)}
                                    >
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </Button>
                                </>
                            )}
                        </div>

                        <FilterEditor
                            value={switchCase.filter}
                            triggerType={def.trigger.type}
                            readOnly={readOnly}
                            onChange={(filter) => setCase(index, { filter })}
                        />

                        <NextStepField
                            label="Go to"
                            def={def}
                            currentId={node.id}
                            value={switchCase.next_node_id}
                            onChange={(v) => setCase(index, { next_node_id: v })}
                            readOnly={readOnly}
                        />
                    </div>
                ))}

                {!readOnly && (
                    <Button variant="outline" size="sm" className="gap-1.5" onClick={addCase}>
                        <Plus className="h-3.5 w-3.5" /> Add case
                    </Button>
                )}
            </div>

            <NextStepField
                label="Otherwise → go to"
                def={def}
                currentId={node.id}
                value={node.default_next_node_id}
                onChange={(v) => onChange({ ...node, default_next_node_id: v })}
                readOnly={readOnly}
            />
        </>
    )
}

// ---------------------------------------------------------------------------
// Shared field helpers
// ---------------------------------------------------------------------------
function Field({
    label,
    hint,
    htmlFor,
    children,
}: {
    label: string
    hint?: string
    htmlFor?: string
    children: React.ReactNode
}) {
    return (
        <div className="space-y-1.5">
            <Label className="text-sm" htmlFor={htmlFor}>{label}</Label>
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

/**
 * Multi-select over the PMS appointment dispositions.
 *
 * The definition schema has always accepted `status_ids: number[]`, but this was
 * rendered as a single-select writing a one-element array — so "booked OR
 * cancelled" was unreachable from the builder despite the backend supporting it.
 * An empty selection means "any status", matching the backend's treatment of an
 * empty list.
 */
function StatusIdMultiSelect({
    selected,
    disabled,
    ariaLabel,
    onChange,
}: {
    selected: number[]
    disabled?: boolean
    ariaLabel: string
    onChange: (statusIds: number[]) => void
}) {
    const statuses = usePmsAppointmentStatuses()
    const chosen = new Set(selected)

    const toggle = (id: number) => {
        // Preserve catalog order rather than click order, so two workflows with
        // the same selection serialize identically.
        const next = new Set(chosen)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        onChange(statuses.filter((s) => next.has(s.id)).map((s) => s.id))
    }

    return (
        <div role="group" aria-label={ariaLabel} className="space-y-1.5">
            <div className="grid grid-cols-1 gap-1.5 rounded-md border border-border p-2 sm:grid-cols-2">
                {statuses.map((status) => (
                    <label
                        key={status.id}
                        className={cn(
                            "flex items-center gap-2 text-sm",
                            disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                        )}
                        title={status.description || undefined}
                    >
                        <Checkbox
                            checked={chosen.has(status.id)}
                            disabled={disabled}
                            aria-label={status.label}
                            onCheckedChange={() => toggle(status.id)}
                        />
                        <span className="truncate">
                            <span className="text-muted-foreground">{status.id}</span> {status.label}
                        </span>
                    </label>
                ))}
            </div>
            <p className="text-xs text-muted-foreground">
                {chosen.size === 0
                    ? "Any status matches."
                    : `Matches ${chosen.size} of ${statuses.length} statuses.`}
            </p>
        </div>
    )
}


function TriStateBooleanSelect({
    value,
    disabled,
    ariaLabel,
    nullLabel = "Do not change",
    trueLabel,
    falseLabel,
    onChange,
}: {
    value?: boolean | null
    disabled?: boolean
    ariaLabel?: string
    nullLabel?: string
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
            <SelectTrigger aria-label={ariaLabel}><SelectValue /></SelectTrigger>
            <SelectContent>
                <SelectItem value={NONE}>{nullLabel}</SelectItem>
                <SelectItem value="true">{trueLabel}</SelectItem>
                <SelectItem value="false">{falseLabel}</SelectItem>
            </SelectContent>
        </Select>
    )
}

// ---------------------------------------------------------------------------
// value helpers
// ---------------------------------------------------------------------------
function toInt(v: string, fallback: number): number {
    const n = parseInt(v, 10)
    return Number.isFinite(n) ? n : fallback
}
function textToStringList(text: string): string[] {
    const values: string[] = []
    for (const raw of text.split(",")) {
        const value = raw.trim()
        if (value && !values.some((existing) => existing.toLowerCase() === value.toLowerCase())) {
            values.push(value)
        }
    }
    return values
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
