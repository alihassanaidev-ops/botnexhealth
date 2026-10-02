/**
 * Operator metadata for the filter DSL.
 *
 * Mirrors `src/app/services/automation/filter_expression.py`. Kept in its own
 * module so the pure validation logic can import it without pulling in React.
 */
import type {
    FilterExpression,
    FilterGroup,
    FilterOp,
    FilterRule,
} from "@/types/workflow"

/** Operators that test presence only — the editor hides the value input. */
export const OPS_WITHOUT_VALUE: ReadonlySet<FilterOp> = new Set<FilterOp>([
    "is_null",
    "is_not_null",
    "is_empty",
    "is_not_empty",
])

/** Operators whose value is a list. */
export const OPS_WITH_LIST_VALUE: ReadonlySet<FilterOp> = new Set<FilterOp>([
    "in",
    "not_in",
    "in_case_insensitive",
    "not_in_case_insensitive",
    "any_of",
    "all_of",
    "between",
])

/** Operators whose value names another context field rather than a literal. */
export const OPS_WITH_FIELD_VALUE: ReadonlySet<FilterOp> = new Set<FilterOp>([
    "field_eq",
    "field_neq",
    "field_gt",
    "field_lt",
])

/** Operators whose value is an ISO-8601 duration. */
export const OPS_WITH_DURATION_VALUE: ReadonlySet<FilterOp> = new Set<FilterOp>([
    "within",
    "older_than",
])

/** Operators whose value is a date, a datetime, or a `now`-relative expression. */
export const OPS_WITH_DATE_VALUE: ReadonlySet<FilterOp> = new Set<FilterOp>([
    "before",
    "after",
])

export interface FilterOpGroup {
    label: string
    ops: FilterOp[]
}

/** Grouped for the operator picker, so 30 operators stay scannable. */
export const FILTER_OP_GROUPS: FilterOpGroup[] = [
    { label: "Is", ops: ["eq", "neq"] },
    {
        label: "One of",
        ops: ["in_case_insensitive", "not_in_case_insensitive", "in", "not_in"],
    },
    {
        label: "Text",
        ops: ["contains", "not_contains", "starts_with", "ends_with", "matches"],
    },
    { label: "Number", ops: ["gt", "gte", "lt", "lte", "between"] },
    { label: "Date & time", ops: ["before", "after", "within", "older_than"] },
    { label: "Presence", ops: ["is_not_empty", "is_empty", "is_not_null", "is_null"] },
    { label: "List", ops: ["any_of", "all_of"] },
    {
        label: "Compare to another field",
        ops: ["field_eq", "field_neq", "field_gt", "field_lt"],
    },
]

export const FILTER_OP_LABELS: Record<FilterOp, string> = {
    eq: "equals",
    neq: "does not equal",
    in: "is one of (exact case)",
    not_in: "is not one of (exact case)",
    in_case_insensitive: "is one of",
    not_in_case_insensitive: "is not one of",
    contains: "contains",
    not_contains: "does not contain",
    starts_with: "starts with",
    ends_with: "ends with",
    matches: "matches pattern",
    gt: "is greater than",
    gte: "is at least",
    lt: "is less than",
    lte: "is at most",
    between: "is between",
    before: "is before",
    after: "is after",
    within: "is within the last",
    older_than: "is older than",
    is_null: "is not set",
    is_not_null: "is set",
    is_empty: "is empty",
    is_not_empty: "is not empty",
    any_of: "includes any of",
    all_of: "includes all of",
    field_eq: "equals field",
    field_neq: "does not equal field",
    field_gt: "is greater than field",
    field_lt: "is less than field",
}

/** Placeholder text that teaches the value format for the chosen operator. */
export function valuePlaceholder(op: FilterOp): string {
    if (OPS_WITH_DURATION_VALUE.has(op)) return "P14D  ·  PT6H  ·  P2W"
    if (OPS_WITH_DATE_VALUE.has(op)) return "now  ·  now+P3D  ·  2026-09-01"
    if (OPS_WITH_FIELD_VALUE.has(op)) return "another field path"
    if (op === "between") return "low, high"
    if (OPS_WITH_LIST_VALUE.has(op)) return "comma, separated, values"
    if (op === "matches") return "regular expression"
    return "value"
}

export const ALL_FILTER_OPS: FilterOp[] = FILTER_OP_GROUPS.flatMap((g) => g.ops)

// ---------------------------------------------------------------------------
// Expression constructors
// ---------------------------------------------------------------------------
export function newRule(): FilterRule {
    return { kind: "rule", field: "", op: "eq", value: "" }
}

export function newGroup(op: FilterGroup["op"] = "and"): FilterGroup {
    return { kind: "group", op, children: [newRule()] }
}

/** A rule always renders; wrap it so the root can also grow into a group. */
export function asGroup(expression: FilterExpression): FilterGroup {
    return expression.kind === "group" ? expression : { kind: "group", op: "and", children: [expression] }
}
