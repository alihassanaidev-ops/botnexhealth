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
import { useEffect, useState } from "react"
import { Trash2, Flag, Plus, GitBranch } from "lucide-react"
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
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
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
import { listAppointmentTypes } from "@/lib/tenant-api"
import { SmsPreview, EmailPreview } from "./MessagePreview"
import { useMergeFields } from "@/lib/workflow/merge-fields"
import { addVoiceOutcomeBranch, TRIGGER_NODE_ID, VOICE_OUTCOME_BRANCH_VALUES } from "@/lib/workflow/graph"
import type { CachedAppointmentType } from "@/types"
import type {
    ConditionNode,
    ConditionOp,
    ConditionRule,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    TriggerType,
    UpdatePatientStatusNode,
    WaitNode,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowTrigger,
} from "@/types/workflow"

const NONE = "__none__"
const CONDITION_OPS: ConditionOp[] = ["eq", "neq", "in", "not_in", "is_null", "is_not_null", "contains", "not_contains"]

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
                        locationId={props.locationId}
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
    locationId,
    onChange,
    readOnly,
}: {
    trigger: WorkflowTrigger
    locationId?: string | null
    onChange: (t: WorkflowTrigger) => void
    readOnly?: boolean
}) {
    const meta = TRIGGER_META[trigger.type]
    const [appointmentTypes, setAppointmentTypes] = useState<CachedAppointmentType[]>([])
    const [typesLoading, setTypesLoading] = useState(false)

    useEffect(() => {
        if (trigger.type !== "appointment_offset" || !locationId) {
            setAppointmentTypes([])
            return
        }
        let cancelled = false
        setTypesLoading(true)
        listAppointmentTypes(locationId)
            .then((types) => {
                if (!cancelled) setAppointmentTypes(types.filter((type) => type.is_active))
            })
            .catch(() => {
                if (!cancelled) setAppointmentTypes([])
            })
            .finally(() => {
                if (!cancelled) setTypesLoading(false)
            })
        return () => {
            cancelled = true
        }
    }, [trigger.type, locationId])

    const selectedTypeIds = trigger.type === "appointment_offset"
        ? trigger.appointment_type_ids ?? []
        : []
    const toggleAppointmentType = (sourceId: string, checked: boolean) => {
        if (trigger.type !== "appointment_offset") return
        const next = checked
            ? [...selectedTypeIds, sourceId]
            : selectedTypeIds.filter((id) => id !== sourceId)
        onChange({ ...trigger, appointment_type_ids: next.length ? next : null })
    }
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
                        <Field label="Appointment types" hint="Leave empty to match every appointment type.">
                            <div className="max-h-48 space-y-2 overflow-y-auto rounded-md border border-border p-2">
                                {!locationId ? (
                                    <p className="text-xs text-muted-foreground">Save this campaign to a location before selecting appointment types.</p>
                                ) : typesLoading ? (
                                    <p className="text-xs text-muted-foreground">Loading appointment types...</p>
                                ) : appointmentTypes.length === 0 ? (
                                    <p className="text-xs text-muted-foreground">No appointment types are available for this location yet.</p>
                                ) : (
                                    appointmentTypes.map((type) => (
                                        <label key={type.source_id} className="flex items-center gap-2 rounded px-1.5 py-1 text-sm">
                                            <Checkbox
                                                checked={selectedTypeIds.includes(type.source_id)}
                                                disabled={readOnly}
                                                onCheckedChange={(checked) => toggleAppointmentType(type.source_id, checked === true)}
                                            />
                                            <span className="min-w-0 flex-1 truncate">{type.name}</span>
                                            {type.duration_minutes && (
                                                <span className="shrink-0 text-xs text-muted-foreground">{type.duration_minutes}m</span>
                                            )}
                                        </label>
                                    ))
                                )}
                            </div>
                        </Field>
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
                        readOnly={readOnly}
                    />
                )}
                {node.type === "wait" && <WaitFields node={node} onChange={onNodeChange} readOnly={readOnly} />}
                {node.type === "update_patient_status" && (
                    <UpdatePatientStatusFields node={node} onChange={onNodeChange} readOnly={readOnly} />
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
    readOnly,
}: {
    node: SendVoiceNode
    def: WorkflowDefinition
    onChange: (n: WorkflowNode) => void
    onDefinitionChange: (def: WorkflowDefinition) => void
    readOnly?: boolean
}) {
    return (
        <>
            <Field label="Retell agent ID" hint="The location's outbound voice agent.">
                <Input
                    value={node.retell_agent_id}
                    disabled={readOnly}
                    placeholder="agent_..."
                    onChange={(e) => onChange({ ...node, retell_agent_id: e.target.value })}
                />
            </Field>
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div>
                    <Label className="text-sm">Wait for voice outcome</Label>
                    <p className="text-xs text-muted-foreground">
                        Pause this run until the Retell post-call webhook writes call_outcome.
                    </p>
                </div>
                <Switch
                    checked={node.wait_for_outcome ?? false}
                    disabled={readOnly}
                    onCheckedChange={(checked) => onChange({ ...node, wait_for_outcome: checked })}
                />
            </div>
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
                            value={relativeWaitPreset(delay.offset_seconds)}
                            disabled={readOnly}
                            onValueChange={(v) =>
                                onChange({
                                    ...node,
                                    delay: {
                                        delay_type: "appointment_relative",
                                        offset_seconds: Number(v),
                                        anchor_field: delay.anchor_field ?? "appointment_at",
                                    },
                                })
                            }
                        >
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="-86400">1 day before appointment</SelectItem>
                                <SelectItem value="-7200">2 hours before appointment</SelectItem>
                                <SelectItem value="-3600">1 hour before appointment</SelectItem>
                                <SelectItem value="3600">1 hour after appointment</SelectItem>
                                <SelectItem value="86400">1 day after appointment</SelectItem>
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

function relativeWaitPreset(seconds: number): string {
    const presets = new Set(["-86400", "-7200", "-3600", "3600", "86400"])
    const text = String(seconds)
    return presets.has(text) ? text : text
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
            <Field label="Status">
                <Select
                    value={node.status}
                    disabled={readOnly}
                    onValueChange={(value) => onChange({ ...node, status: value })}
                >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="appointment_confirmed">Appointment confirmed</SelectItem>
                        <SelectItem value="appointment_cancelled">Appointment cancelled</SelectItem>
                        <SelectItem value="reschedule_requested">Reschedule requested</SelectItem>
                        <SelectItem value="reschedule_or_followup_needed">Reschedule/follow-up needed</SelectItem>
                        <SelectItem value="do_not_call_requested">Do not call requested</SelectItem>
                        <SelectItem value="no_answer">No answer</SelectItem>
                        <SelectItem value="post_op_followup_needed">Post-op follow-up needed</SelectItem>
                        <SelectItem value="post_op_complete">Post-op complete</SelectItem>
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
                    return (
                        <div key={i} className="space-y-2 rounded-md border border-border p-2">
                            <div className="flex gap-2">
                                <Input
                                    className="flex-1"
                                    placeholder="field (e.g. appointment_status)"
                                    value={rule.field}
                                    disabled={readOnly}
                                    onChange={(e) => updateRule(i, { field: e.target.value })}
                                />
                                {!readOnly && node.rules.length > 1 && (
                                    <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" onClick={() => removeRule(i)}>
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </Button>
                                )}
                            </div>
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
                                        placeholder={rule.op === "in" || rule.op === "not_in" ? "a, b, c" : "value"}
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

// ---------------------------------------------------------------------------
// value helpers
// ---------------------------------------------------------------------------
function defaultTrigger(type: TriggerType): WorkflowTrigger {
    switch (type) {
        case "appointment_offset":
            return { type, offset_hours: -24, appointment_type_ids: null }
        case "recall_scan":
            return { type, recall_interval_months: 6 }
        case "manual":
            return { type }
        case "bulk_import":
            return { type }
        case "callback_requested":
            return { type }
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
function round2(n: number): number {
    return Math.round(n * 100) / 100
}
function ruleValueToText(value: ConditionRule["value"]): string {
    if (value === null || value === undefined) return ""
    if (Array.isArray(value)) return value.join(", ")
    return String(value)
}
function textToRuleValue(text: string, op: ConditionOp): ConditionRule["value"] {
    if (op === "in" || op === "not_in") {
        return text
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
    }
    return text
}
