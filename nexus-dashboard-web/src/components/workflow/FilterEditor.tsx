/**
 * Recursive editor for a `FilterExpression`.
 *
 * One component serves every surface that used to hand-roll its own matcher UI:
 * the trigger's eligibility filter, the condition node, and each switch case.
 * Adding an operator therefore means editing `filter-ops.ts` once, not four
 * screens.
 *
 * Nesting is rendered as an indented rail rather than nested cards — a card per
 * level burns ~16px of horizontal space each time, and the panel is only ~380px
 * wide, so three levels of cards leaves no room for the inputs themselves.
 */
import { Fragment } from "react"
import { Plus, Trash2, CornerDownRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"
import {
    FILTER_OP_GROUPS,
    FILTER_OP_LABELS,
    newGroup,
    newRule,
    OPS_WITHOUT_VALUE,
    OPS_WITH_LIST_VALUE,
    valuePlaceholder,
} from "@/lib/workflow/filter-ops"
import type {
    FilterExpression,
    FilterGroup,
    FilterOp,
    FilterRule,
    TriggerType,
} from "@/types/workflow"
import { contextFieldsForTrigger } from "@/lib/workflow/context-fields"

const CUSTOM_FIELD = "__custom__"
const MAX_DEPTH = 3

export interface FilterEditorProps {
    value: FilterExpression
    onChange: (next: FilterExpression) => void
    /** Scopes the field suggestions to what this trigger's context carries. */
    triggerType?: TriggerType
    readOnly?: boolean
    /** Shown above the tree; omit inside a case row where the label is enough. */
    label?: string
    hint?: string
}

export default function FilterEditor({
    value,
    onChange,
    triggerType,
    readOnly,
    label,
    hint,
}: FilterEditorProps) {
    return (
        <div className="space-y-2">
            {label ? (
                <div>
                    <Label className="text-sm">{label}</Label>
                    {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
                </div>
            ) : null}
            <ExpressionNode
                expression={value}
                onChange={onChange}
                onRemove={undefined}
                triggerType={triggerType}
                readOnly={readOnly}
                depth={0}
            />
        </div>
    )
}

function ExpressionNode({
    expression,
    onChange,
    onRemove,
    triggerType,
    readOnly,
    depth,
}: {
    expression: FilterExpression
    onChange: (next: FilterExpression) => void
    onRemove?: () => void
    triggerType?: TriggerType
    readOnly?: boolean
    depth: number
}) {
    if (expression.kind === "group") {
        return (
            <GroupNode
                group={expression}
                onChange={onChange}
                onRemove={onRemove}
                triggerType={triggerType}
                readOnly={readOnly}
                depth={depth}
            />
        )
    }
    return (
        <RuleRow
            rule={expression}
            onChange={onChange}
            onRemove={onRemove}
            onGroup={
                depth < MAX_DEPTH
                    ? () => onChange({ kind: "group", op: "and", children: [expression, newRule()] })
                    : undefined
            }
            triggerType={triggerType}
            readOnly={readOnly}
        />
    )
}

function GroupNode({
    group,
    onChange,
    onRemove,
    triggerType,
    readOnly,
    depth,
}: {
    group: FilterGroup
    onChange: (next: FilterExpression) => void
    onRemove?: () => void
    triggerType?: TriggerType
    readOnly?: boolean
    depth: number
}) {
    const setChild = (index: number, child: FilterExpression) =>
        onChange({ ...group, children: group.children.map((c, i) => (i === index ? child : c)) })

    const removeChild = (index: number) => {
        const children = group.children.filter((_, i) => i !== index)
        // A group with one child adds nothing; collapse back to the child so the
        // tree does not accumulate empty scaffolding as an author edits.
        if (children.length === 1) {
            onChange(children[0])
            return
        }
        if (children.length === 0) {
            onRemove?.()
            return
        }
        onChange({ ...group, children })
    }

    return (
        <div
            className={cn(
                "space-y-2",
                depth > 0 && "border-l-2 border-border pl-3",
            )}
        >
            <div className="flex items-center gap-2">
                <Select
                    value={group.op}
                    disabled={readOnly}
                    onValueChange={(v) => onChange({ ...group, op: v as FilterGroup["op"] })}
                >
                    <SelectTrigger className="h-8 w-[130px]" aria-label="Combine with">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="and">Match all</SelectItem>
                        <SelectItem value="or">Match any</SelectItem>
                        <SelectItem value="not">Match none</SelectItem>
                    </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground">
                    {group.children.length} condition{group.children.length === 1 ? "" : "s"}
                </span>
                <div className="ml-auto flex items-center gap-1">
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 gap-1 px-2 text-xs"
                        disabled={readOnly}
                        onClick={() => onChange({ ...group, children: [...group.children, newRule()] })}
                    >
                        <Plus className="h-3.5 w-3.5" /> Condition
                    </Button>
                    {depth < MAX_DEPTH ? (
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 px-2 text-xs"
                            disabled={readOnly}
                            onClick={() => onChange({ ...group, children: [...group.children, newGroup("or")] })}
                        >
                            <CornerDownRight className="h-3.5 w-3.5" /> Group
                        </Button>
                    ) : null}
                    {onRemove ? (
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            aria-label="Remove group"
                            disabled={readOnly}
                            onClick={onRemove}
                        >
                            <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                    ) : null}
                </div>
            </div>

            <div className="space-y-2">
                {group.children.map((child, index) => (
                    <Fragment key={index}>
                        <ExpressionNode
                            expression={child}
                            onChange={(next) => setChild(index, next)}
                            onRemove={() => removeChild(index)}
                            triggerType={triggerType}
                            readOnly={readOnly}
                            depth={depth + 1}
                        />
                    </Fragment>
                ))}
            </div>
        </div>
    )
}

function RuleRow({
    rule,
    onChange,
    onRemove,
    onGroup,
    triggerType,
    readOnly,
}: {
    rule: FilterRule
    onChange: (next: FilterExpression) => void
    onRemove?: () => void
    onGroup?: () => void
    triggerType?: TriggerType
    readOnly?: boolean
}) {
    const suggestions = triggerType ? contextFieldsForTrigger(triggerType) : []
    const known = suggestions.some((f) => f.name === rule.field)
    const takesValue = !OPS_WITHOUT_VALUE.has(rule.op)
    const takesList = OPS_WITH_LIST_VALUE.has(rule.op)

    const setOp = (op: FilterOp) => {
        // Reshape the value to what the new operator expects, rather than
        // leaving a list behind a scalar operator for the backend to reject.
        const nowList = OPS_WITH_LIST_VALUE.has(op)
        const wasList = Array.isArray(rule.value)
        let value = rule.value
        if (OPS_WITHOUT_VALUE.has(op)) value = null
        else if (nowList && !wasList) value = rule.value == null || rule.value === "" ? [] : [String(rule.value)]
        else if (!nowList && wasList) value = (rule.value as unknown[])[0] != null ? String((rule.value as unknown[])[0]) : ""
        onChange({ ...rule, op, value: value as FilterRule["value"] })
    }

    return (
        <div className="space-y-1.5 rounded-md border border-border bg-card/60 p-2">
            <div className="flex items-center gap-1.5">
                {suggestions.length ? (
                    <Select
                        value={known ? rule.field : CUSTOM_FIELD}
                        disabled={readOnly}
                        onValueChange={(v) =>
                            onChange({ ...rule, field: v === CUSTOM_FIELD ? "" : v })
                        }
                    >
                        <SelectTrigger className="h-8 flex-1" aria-label="Field">
                            <SelectValue placeholder="Field" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectGroup>
                                <SelectLabel>Event fields</SelectLabel>
                                {suggestions.map((f) => (
                                    <SelectItem key={f.name} value={f.name}>
                                        {f.label}
                                    </SelectItem>
                                ))}
                            </SelectGroup>
                            <SelectItem value={CUSTOM_FIELD}>Custom field…</SelectItem>
                        </SelectContent>
                    </Select>
                ) : null}
                {onRemove ? (
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 shrink-0"
                        aria-label="Remove condition"
                        disabled={readOnly}
                        onClick={onRemove}
                    >
                        <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                ) : null}
            </div>

            {(!suggestions.length || !known) && (
                <Input
                    className="h-8"
                    aria-label="Field path"
                    placeholder="appointment.start_at"
                    value={rule.field}
                    disabled={readOnly}
                    onChange={(e) => onChange({ ...rule, field: e.target.value })}
                />
            )}

            <div className="flex items-center gap-1.5">
                <Select value={rule.op} disabled={readOnly} onValueChange={(v) => setOp(v as FilterOp)}>
                    <SelectTrigger className="h-8 flex-1" aria-label="Operator">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {FILTER_OP_GROUPS.map((group) => (
                            <SelectGroup key={group.label}>
                                <SelectLabel>{group.label}</SelectLabel>
                                {group.ops.map((op) => (
                                    <SelectItem key={op} value={op}>
                                        {FILTER_OP_LABELS[op]}
                                    </SelectItem>
                                ))}
                            </SelectGroup>
                        ))}
                    </SelectContent>
                </Select>
                {onGroup ? (
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 shrink-0"
                        aria-label="Group with another condition"
                        title="Group with another condition"
                        disabled={readOnly}
                        onClick={onGroup}
                    >
                        <CornerDownRight className="h-3.5 w-3.5" />
                    </Button>
                ) : null}
            </div>

            {takesValue ? (
                <Input
                    className="h-8"
                    aria-label="Value"
                    placeholder={valuePlaceholder(rule.op)}
                    value={valueToText(rule.value)}
                    disabled={readOnly}
                    onChange={(e) =>
                        onChange({ ...rule, value: textToValue(e.target.value, takesList) })
                    }
                />
            ) : null}
        </div>
    )
}

function valueToText(value: FilterRule["value"]): string {
    if (value == null) return ""
    if (Array.isArray(value)) return value.join(", ")
    if (typeof value === "boolean") return value ? "true" : "false"
    return String(value)
}

/**
 * Text stays text. Numeric and boolean coercion is the backend evaluator's job
 * — doing it here too would mean two places deciding that `"1"` is `1`, and
 * they would eventually disagree.
 */
function textToValue(text: string, asList: boolean): FilterRule["value"] {
    if (!asList) return text
    return text
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
}
