/**
 * Definition <-> graph conversion, deterministic layout, and mutation helpers.
 *
 * The workflow definition stores forward pointers (`next_node_id`,
 * `true/false_next_node_id`), NOT an edges array or coordinates. React Flow needs
 * nodes[] + edges[] + positions, so we DERIVE them here. The fallback layout is a
 * deterministic layered algorithm. The explicit Auto Layout action persists
 * calculated positions into `definition.layout` as presentational metadata only.
 *
 * Pure module: `@xyflow/react` is imported type-only so unit tests don't load the
 * browser runtime.
 */
import type { Edge, Node } from "@xyflow/react"
import {
    SCHEMA_VERSION,
    type ChannelKey,
    type NodePosition,
    type NodeType,
    type SmsResponseMapping,
    type TriggerType,
    type WaitDelay,
    type WorkflowDefinition,
    type WorkflowNode,
    type WorkflowTrigger,
} from "@/types/workflow"

export const TRIGGER_NODE_ID = "__trigger__"
export const VOICE_OUTCOME_BRANCH_VALUES = [
    "booked",
    "answered",
    "transferred",
    "callback_requested",
    "voicemail",
    "no_answer",
    "busy",
    "failed",
    "do_not_call",
] as const

const COL_W = 300
const ROW_H = 150
const X0 = 40
const Y0 = 40
const AUTO_COL_W = 340
const AUTO_ROW_H = 170
const NODE_W = 240
const NODE_H = 92

// ---------------------------------------------------------------------------
// React Flow node/edge data payloads
// ---------------------------------------------------------------------------
/**
 * React Flow node data. Written as type-alias object literals (NOT interfaces) so the
 * union satisfies React Flow v12's `Record<string, unknown>` data constraint.
 * `issueLevel` is a validation overlay injected by the builder page (undefined = clean).
 */
export type FlowNodeData =
    | {
          kind: "trigger"
          trigger: WorkflowTrigger
          /** False when no entry step is wired, which is when the port offers a `+`. */
          hasEntry?: boolean
          issueLevel?: "error" | "warning" | null
          executionStatus?: ExecutionNodeStatus
          /**
           * Injected by the canvas, not by `definitionToFlow` — this module stays
           * pure. Absent in read-only previews, which is what hides the `+`.
           */
          onAddFromPort?: (sourceId: string, handle?: string) => void
      }
    | {
          kind: "step"
          node: WorkflowNode
          isEntry: boolean
          issueLevel?: "error" | "warning" | null
          executionStatus?: ExecutionNodeStatus
          executionAttempts?: number
          onAddFromPort?: (sourceId: string, handle?: string) => void
      }

export type ExecutionNodeStatus =
    | "pending"
    | "running"
    | "waiting"
    | "completed"
    | "skipped"
    | "failed"
    | "blocked"

export type FlowNode = Node<FlowNodeData>
export type FlowEdge = Edge & {
    pathOptions?: {
        borderRadius?: number
        offset?: number
    }
    interactionWidth?: number
}

const EDGE_STYLE = {
    strokeWidth: 2,
    stroke: "hsl(var(--muted-foreground))",
    strokeOpacity: 0.75,
}

const EDGE_LABEL_STYLE = {
    fontSize: 11,
    fontWeight: 600,
    fill: "hsl(var(--foreground))",
    color: "hsl(var(--foreground))",
}

const EDGE_LABEL_BG_STYLE = {
    fill: "hsl(var(--card))",
    fillOpacity: 0.96,
}

function smartEdge(edge: Omit<FlowEdge, "type"> & { type?: FlowEdge["type"] }): FlowEdge {
    const handle = edge.sourceHandle
    const isFalse = handle === "false"
    const isTrue = handle === "true"
    const caseIndex = switchCaseIndex(handle ?? undefined)
    const isDefault = handle === SWITCH_DEFAULT_HANDLE
    const isBranch = isFalse || isTrue || caseIndex !== null || isDefault
    // Stagger branch offsets so a switch with several ports does not draw its
    // edges on top of one another.
    const branchOffset =
        caseIndex !== null ? 20 + caseIndex * 6 : isDefault ? 34 : isFalse ? 30 : 22
    return {
        type: "step",
        pathOptions: {
            offset: isBranch ? branchOffset : 14,
        },
        interactionWidth: 18,
        style: EDGE_STYLE,
        labelStyle: EDGE_LABEL_STYLE,
        labelShowBg: true,
        labelBgStyle: EDGE_LABEL_BG_STYLE,
        labelBgPadding: [6, 3],
        labelBgBorderRadius: 4,
        ...edge,
    }
}

// ---------------------------------------------------------------------------
// Outgoing-pointer helpers
// ---------------------------------------------------------------------------
interface Outgoing {
    targetId: string
    /**
     * Source handle id. `true`/`false` for a condition; `case-<n>` / `default`
     * for a switch, whose port count varies with the authored cases.
     */
    handle?: string
    label?: string
}

/** The forward pointer(s) a node declares (empty targets included so danglers show). */
export const SWITCH_DEFAULT_HANDLE = "default"

/** Stable source-handle id for the nth switch case. */
export function switchCaseHandle(index: number): string {
    return `case-${index}`
}

/** The case index a switch handle refers to, or null for the default port. */
export function switchCaseIndex(handle: string | undefined): number | null {
    if (!handle || !handle.startsWith("case-")) return null
    const index = Number(handle.slice("case-".length))
    return Number.isInteger(index) && index >= 0 ? index : null
}

export function outgoing(node: WorkflowNode): Outgoing[] {
    switch (node.type) {
        case "wait":
        case "drip":
        case "send_sms":
        case "retell_sms_conversation":
        case "send_voice":
        case "send_email":
        case "update_patient_status":
        case "update_appointment":
        case "update_gotracker_appointment":
        case "booking_link":
        case "patient_registration":
        case "json_mapper":
        case "llm":
            return [{ targetId: node.next_node_id }]
        case "condition":
            return [
                { targetId: node.true_next_node_id, handle: "true", label: "Yes" },
                { targetId: node.false_next_node_id, handle: "false", label: "No" },
            ]
        case "switch":
            return [
                ...node.cases.map((c, index) => ({
                    targetId: c.next_node_id,
                    // Index, not label, so renaming a case does not orphan its edge.
                    handle: switchCaseHandle(index),
                    label: c.label,
                })),
                { targetId: node.default_next_node_id, handle: SWITCH_DEFAULT_HANDLE, label: "Otherwise" },
            ]
        case "exit":
            return []
    }
}

/** Delivery channel a send-node type targets (undefined for non-send nodes). */
const CHANNEL_BY_NODE_TYPE: Partial<Record<NodeType, ChannelKey>> = {
    send_sms: "sms",
    retell_sms_conversation: "sms",
    send_email: "email",
    send_voice: "voice",
}

/** The set of delivery channels the definition actually uses (from its send nodes). */
export function channelsUsed(def: WorkflowDefinition): Set<ChannelKey> {
    const used = new Set<ChannelKey>()
    for (const n of def.nodes) {
        const channel = CHANNEL_BY_NODE_TYPE[n.type]
        if (channel) used.add(channel)
    }
    return used
}

/** All node ids this node references (non-empty). */
export function referencedIds(node: WorkflowNode): string[] {
    return outgoing(node)
        .map((o) => o.targetId)
        .filter((id) => id.length > 0)
}

/** The single forward pointer for linear/send nodes, else undefined. */
function singleNext(node: WorkflowNode): string | undefined {
    if (
        node.type === "wait" ||
        node.type === "drip" ||
        node.type === "send_sms" ||
        node.type === "retell_sms_conversation" ||
        node.type === "send_voice" ||
        node.type === "send_email" ||
        node.type === "update_patient_status" ||
        node.type === "update_appointment" ||
        node.type === "update_gotracker_appointment" ||
        node.type === "booking_link" ||
        node.type === "patient_registration" ||
        node.type === "json_mapper" ||
        node.type === "llm"
    ) {
        return node.next_node_id
    }
    return undefined
}

// ---------------------------------------------------------------------------
// Layout — layered BFS from the trigger
// ---------------------------------------------------------------------------
/** Assign a vertical depth to every node id (trigger = 0, entry = 1, ...). */
export function computeDepths(def: WorkflowDefinition): Map<string, number> {
    const byId = new Map(def.nodes.map((n) => [n.id, n]))
    const depth = new Map<string, number>()
    depth.set(TRIGGER_NODE_ID, 0)

    // BFS starting from the entry node at depth 1.
    const queue: Array<[string, number]> = []
    if (byId.has(def.entry_node_id)) queue.push([def.entry_node_id, 1])
    while (queue.length) {
        const [id, d] = queue.shift() as [string, number]
        const existing = depth.get(id)
        if (existing !== undefined && existing <= d) continue
        depth.set(id, d)
        const node = byId.get(id)
        if (!node) continue
        for (const t of referencedIds(node)) {
            if (byId.has(t)) queue.push([t, d + 1])
        }
    }

    // Unreachable nodes: place them in a trailing layer so they still render.
    const maxDepth = Math.max(1, ...Array.from(depth.values()))
    for (const n of def.nodes) {
        if (!depth.has(n.id)) depth.set(n.id, maxDepth + 1)
    }
    return depth
}

/** Convert a definition into React Flow nodes + edges with computed positions. */
export function definitionToFlow(def: WorkflowDefinition): {
    nodes: FlowNode[]
    edges: FlowEdge[]
} {
    const depth = computeDepths(def)
    // Manual positions (presentational) win over the auto-layout when present.
    const layout = def.layout ?? {}

    // Order nodes within each vertical layer by definition order for determinism.
    const slotByDepth = new Map<number, number>()
    const nextSlot = (d: number): number => {
        const slot = slotByDepth.get(d) ?? 0
        slotByDepth.set(d, slot + 1)
        return slot
    }

    const nodes: FlowNode[] = []

    // Trigger node (synthetic, column 0).
    nodes.push({
        id: TRIGGER_NODE_ID,
        type: "trigger",
        position: layout[TRIGGER_NODE_ID] ?? { x: X0 + nextSlot(0) * COL_W, y: Y0 },
        data: {
            kind: "trigger",
            trigger: def.trigger,
            hasEntry: Boolean(def.entry_node_id),
        },
        deletable: false,
    })

    for (const n of def.nodes) {
        const d = depth.get(n.id) ?? 1
        nodes.push({
            id: n.id,
            type: "step",
            position: layout[n.id] ?? { x: X0 + nextSlot(d) * COL_W, y: Y0 + d * ROW_H },
            data: { kind: "step", node: n, isEntry: n.id === def.entry_node_id },
        })
    }

    const ids = new Set(def.nodes.map((n) => n.id))
    const edges: FlowEdge[] = []

    // Trigger -> entry.
    if (ids.has(def.entry_node_id)) {
        edges.push(smartEdge({
            id: `e-${TRIGGER_NODE_ID}-${def.entry_node_id}`,
            source: TRIGGER_NODE_ID,
            target: def.entry_node_id,
        }))
    }

    for (const n of def.nodes) {
        for (const o of outgoing(n)) {
            if (!o.targetId || !ids.has(o.targetId)) continue
            edges.push(smartEdge({
                id: `e-${n.id}-${o.handle ?? "next"}-${o.targetId}`,
                source: n.id,
                target: o.targetId,
                sourceHandle: o.handle,
                label: o.label,
            }))
        }
    }

    return { nodes, edges }
}

// ---------------------------------------------------------------------------
// Auto layout — ELK-style layered placement
// ---------------------------------------------------------------------------
interface ParentRef {
    parentId: string
    /** Matches `Outgoing.handle`: condition ports plus variable switch ports. */
    handle?: string
}

function allGraphIds(def: WorkflowDefinition): string[] {
    return [TRIGGER_NODE_ID, ...def.nodes.map((n) => n.id)]
}

function incomingRefs(def: WorkflowDefinition): Map<string, ParentRef[]> {
    const incoming = new Map<string, ParentRef[]>()
    const add = (targetId: string, ref: ParentRef) => {
        if (!targetId) return
        const list = incoming.get(targetId) ?? []
        list.push(ref)
        incoming.set(targetId, list)
    }
    add(def.entry_node_id, { parentId: TRIGGER_NODE_ID })
    for (const node of def.nodes) {
        for (const out of outgoing(node)) {
            add(out.targetId, { parentId: node.id, handle: out.handle })
        }
    }
    return incoming
}

function branchBias(refs: ParentRef[]): number {
    if (refs.some((r) => r.handle === "true")) return -0.28
    if (refs.some((r) => r.handle === "false")) return 0.28
    return 0
}

function parentAverage(refs: ParentRef[], rowIndex: Map<string, number>): number {
    if (refs.length === 0) return Number.POSITIVE_INFINITY
    const rows = refs
        .map((r) => rowIndex.get(r.parentId))
        .filter((r): r is number => r !== undefined)
    if (rows.length === 0) return Number.POSITIVE_INFINITY
    return rows.reduce((a, b) => a + b, 0) / rows.length
}

/** Return a new definition with a freshly computed presentational layout. */
export function autoLayoutDefinition(def: WorkflowDefinition): WorkflowDefinition {
    const depth = computeDepths(def)
    const incoming = incomingRefs(def)
    const definitionOrder = new Map(allGraphIds(def).map((id, index) => [id, index]))

    const layers = new Map<number, string[]>()
    for (const id of allGraphIds(def)) {
        const d = depth.get(id) ?? 1
        const list = layers.get(d) ?? []
        list.push(id)
        layers.set(d, list)
    }

    const rowIndex = new Map<string, number>()
    const sortedDepths = Array.from(layers.keys()).sort((a, b) => a - b)

    for (const d of sortedDepths) {
        const ids = layers.get(d) ?? []
        ids.sort((a, b) => {
            const aRefs = incoming.get(a) ?? []
            const bRefs = incoming.get(b) ?? []
            const parentDelta =
                parentAverage(aRefs, rowIndex) + branchBias(aRefs) -
                (parentAverage(bRefs, rowIndex) + branchBias(bRefs))
            if (Number.isFinite(parentDelta) && Math.abs(parentDelta) > 0.001) {
                return parentDelta
            }
            return (definitionOrder.get(a) ?? 0) - (definitionOrder.get(b) ?? 0)
        })
        ids.forEach((id, index) => rowIndex.set(id, index))
    }

    const layout: Record<string, NodePosition> = {}
    for (const d of sortedDepths) {
        const ids = layers.get(d) ?? []
        const layerWidth = Math.max(0, (ids.length - 1) * AUTO_COL_W)
        const xOffset = Math.max(0, (AUTO_COL_W * 1.1 - layerWidth) / 2)
        ids.forEach((id, index) => {
            layout[id] = {
                x: X0 + xOffset + index * AUTO_COL_W,
                y: Y0 + d * AUTO_ROW_H,
            }
        })
    }

    // Unreachable islands tend to end up compressed into the trailing layer.
    // Stagger them slightly so their edges and labels remain inspectable.
    const reachable = new Set<string>()
    const stack = def.entry_node_id ? [def.entry_node_id] : []
    const byId = new Map(def.nodes.map((n) => [n.id, n]))
    while (stack.length) {
        const id = stack.pop() as string
        if (reachable.has(id)) continue
        reachable.add(id)
        const node = byId.get(id)
        if (!node) continue
        for (const next of referencedIds(node)) stack.push(next)
    }
    let orphanIndex = 0
    for (const node of def.nodes) {
        if (reachable.has(node.id)) continue
        const pos = layout[node.id]
        if (!pos) continue
        layout[node.id] = { x: pos.x + NODE_W * 0.18, y: pos.y + orphanIndex * (NODE_H * 0.32) }
        orphanIndex += 1
    }

    return { ...def, layout }
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------
export function genId(type: NodeType, existing: Iterable<string>): string {
    const taken = new Set(existing)
    const base = type.replace(/_/g, "-")
    let n = 1
    let id = `${base}-${n}`
    while (taken.has(id)) {
        n += 1
        id = `${base}-${n}`
    }
    return id
}

export function createNode(type: NodeType, id: string): WorkflowNode {
    switch (type) {
        case "switch":
            return {
                type,
                id,
                subject: "",
                cases: [
                    {
                        label: "Case 1",
                        filter: { kind: "rule", field: "", op: "eq", value: "" },
                        next_node_id: "",
                    },
                ],
                default_next_node_id: "",
            }
        case "wait":
            return {
                type,
                id,
                wait_for: {
                    type: "time",
                    delay: { delay_type: "duration", duration_seconds: 3600 },
                    respect_quiet_hours: true,
                },
                next_node_id: "",
            }
        case "drip":
            return {
                type,
                id,
                batch_size: 25,
                interval_seconds: 3600,
                next_node_id: "",
            }
        case "send_sms":
            return {
                type,
                id,
                body_template: "",
                next_node_id: "",
                include_opt_out_footer: true,
                respect_quiet_hours: true,
                max_attempts: 1,
                expect_response: false,
                response_window_seconds: 259200,
                response_mappings: [],
            }
        case "retell_sms_conversation":
            return {
                type,
                id,
                chat_profile_id: "",
                next_node_id: "",
            }
        case "send_voice":
            return {
                type,
                id,
                voice_profile_id: null,
                retell_agent_id: "",
                next_node_id: "",
                respect_quiet_hours: true,
                max_attempts: 1,
                patient_voice_cooldown_hours: 24,
                phone_country_code_enabled: false,
                phone_country_region: "US",
                wait_for_outcome: false,
                // Item 19 defaults match what the engine did before the settings
                // existed: no message left, and voicemail counts as an attempt.
                leave_voicemail: false,
                voicemail_consumes_attempt: true,
                voice_attempt_allowance: 1,
                max_dials: 5,
            }
        case "send_email":
            return {
                type,
                id,
                subject_template: "",
                body_template: "",
                next_node_id: "",
                respect_quiet_hours: true,
                max_attempts: 1,
            }
        case "update_patient_status":
            return {
                type,
                id,
                status: "appointment_confirmed",
                note_template: "",
                next_node_id: "",
            }
        case "update_appointment":
            return {
                type,
                id,
                next_node_id: "",
                operation: "confirm",
                start_time: null,
                end_time: null,
                duration_min: null,
                provider_id: null,
                operatory_id: null,
                reason: null,
            }
        case "update_gotracker_appointment":
            return {
                type,
                id,
                next_node_id: "",
                status_id: null,
                confirmed: true,
                preconfirmed: false,
                start_time: null,
                end_time: null,
                duration_min: null,
                provider_id: null,
                operatory_id: null,
                patient_id: null,
                reason: null,
            }
        case "booking_link":
            return {
                type,
                id,
                next_node_id: "",
                // Booking only, and unrestricted, so dropping the step in
                // changes nothing until the author narrows it deliberately.
                actions: ["book"],
                appointment_type_ids: [],
                window_days: 7,
                provider_id: null,
                // Reschedule and cancel ask; booking does not. Booking shows
                // only the clinic's own free slots and can be undone, while a
                // cancellation cannot.
                identity_check: "sensitive",
            }
        case "patient_registration":
            return {
                type,
                id,
                next_node_id: "",
                // Empty is invalid on the server: the author must choose which
                // provider a self-registered patient is filed under.
                provider_id: "",
                on_abandoned_node_id: null,
            }
        case "json_mapper":
            return {
                type,
                id,
                mappings: [
                    {
                        source_path: "gotracker_payload.appointment.reasons",
                        target_field: "appointment_reasons",
                        default_value: null,
                    },
                ],
                next_node_id: "",
            }
        case "llm":
            return {
                type,
                id,
                source_field: "appointment_reason",
                output_field: "llm_result",
                prompt_template: "Write the instruction for the AI action.",
                model: null,
                output_mode: "text",
                max_output_tokens: 512,
                include_context: true,
                require_model: true,
                allow_keyword_fallback: false,
                json_schema: null,
                labels: [],
                label_rules: [],
                fallback_label: null,
                next_node_id: "",
            }
        case "condition":
            return {
                type,
                id,
                logic: "AND",
                rules: [{ field: "", op: "eq", value: "" }],
                true_next_node_id: "",
                false_next_node_id: "",
            }
        case "exit":
            return { type, id, outcome: null }
    }
}

/** Add a callback-oriented call_outcome branch after a voice node. */
export function addVoiceOutcomeBranch(def: WorkflowDefinition, voiceNodeId: string): WorkflowDefinition {
    const voice = def.nodes.find((n) => n.id === voiceNodeId)
    if (!voice || voice.type !== "send_voice") return def

    const existingIds = def.nodes.map((n) => n.id)
    const conditionId = genId("condition", existingIds)
    const bookedExitId = genId("exit", [...existingIds, conditionId])
    const existingNext = def.nodes.find((n) => n.id === voice.next_node_id)
    const reusableHandoffExit = existingNext?.type === "exit" ? existingNext.id : null
    const handoffExitId = reusableHandoffExit ?? genId("exit", [...existingIds, conditionId, bookedExitId])

    const condition: WorkflowNode = {
        type: "condition",
        id: conditionId,
        logic: "AND",
        rules: [{ field: "call_outcome", op: "eq", value: "booked" }],
        true_next_node_id: bookedExitId,
        false_next_node_id: handoffExitId,
    }
    const bookedExit: WorkflowNode = { type: "exit", id: bookedExitId, outcome: "booked" }
    const handoffExit: WorkflowNode | null = reusableHandoffExit
        ? null
        : { type: "exit", id: handoffExitId, outcome: "staff_handoff" }

    return {
        ...def,
        nodes: [
            ...def.nodes.map((n) => {
                if (n.id === voiceNodeId) {
                    return { ...voice, wait_for_outcome: true, next_node_id: conditionId }
                }
                if (n.id === reusableHandoffExit && n.type === "exit") {
                    return { ...n, outcome: "staff_handoff" }
                }
                return n
            }),
            condition,
            bookedExit,
            ...(handoffExit ? [handoffExit] : []),
        ],
    }
}

export function createTrigger(type: TriggerType): WorkflowTrigger {
    switch (type) {
        case "appointment_offset":
            return { type, offset_hours: -24 }
        case "appointment_state_changed":
            return {
                type,
                status_ids: [],
                confirmed: true,
                preconfirmed: null,
                flow_states: [],
                max_followup_delay_hours: null,
                campaign_goal: "post_op_followup",
            }
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
        case "sms_reply":
            return {
                type,
                tokens: [],
                campaign_goal: "inbound_sms_followup",
            }
        case "email_reply":
            return {
                type,
                tokens: [],
                campaign_goal: null,
            }
    }
}

/** A minimal valid starting point: manual trigger -> single exit. */
export function blankDefinition(): WorkflowDefinition {
    return {
        schema_version: SCHEMA_VERSION,
        trigger: { type: "manual" },
        entry_node_id: "exit-1",
        nodes: [{ type: "exit", id: "exit-1", outcome: "completed" }],
    }
}

// ---------------------------------------------------------------------------
// Immutable mutations (return a new definition)
// ---------------------------------------------------------------------------
export function addNode(def: WorkflowDefinition, node: WorkflowNode): WorkflowDefinition {
    return { ...def, nodes: [...def.nodes, node] }
}

export function updateNode(
    def: WorkflowDefinition,
    id: string,
    replacement: WorkflowNode,
): WorkflowDefinition {
    return { ...def, nodes: def.nodes.map((n) => (n.id === id ? replacement : n)) }
}

/**
 * Remove a node and repair references. Linear/send predecessors are bypassed to the
 * removed node's own `next_node_id`; other references (condition branches) are cleared
 * to "" so validation flags them for the author.
 */
export function removeNode(def: WorkflowDefinition, id: string): WorkflowDefinition {
    const removed = def.nodes.find((n) => n.id === id)
    const bypass = removed ? singleNext(removed) ?? "" : ""

    const repoint = (target: string): string => (target === id ? bypass : target)

    const nodes = def.nodes
        .filter((n) => n.id !== id)
        .map((n): WorkflowNode => {
            switch (n.type) {
                case "switch":
                    return {
                        ...n,
                        cases: n.cases.map((c) => ({
                            ...c,
                            next_node_id: repoint(c.next_node_id),
                        })),
                        default_next_node_id: repoint(n.default_next_node_id),
                    }
                case "wait":
                case "drip":
                case "send_sms":
                case "retell_sms_conversation":
                case "send_voice":
                case "send_email":
                case "update_patient_status":
                case "update_appointment":
                case "update_gotracker_appointment":
                case "booking_link":
                case "patient_registration":
                case "json_mapper":
                case "llm":
                    return { ...n, next_node_id: repoint(n.next_node_id) }
                case "condition":
                    return {
                        ...n,
                        true_next_node_id: repoint(n.true_next_node_id),
                        false_next_node_id: repoint(n.false_next_node_id),
                    }
                case "exit":
                    return n
            }
        })

    let entry = def.entry_node_id
    if (entry === id) entry = bypass || nodes[0]?.id || ""

    return { ...def, entry_node_id: entry, nodes }
}

export function setEntry(def: WorkflowDefinition, id: string): WorkflowDefinition {
    return { ...def, entry_node_id: id }
}

/**
 * Set a forward pointer from a source node to a target — the immutable core of
 * drag-to-connect. Connecting FROM the synthetic trigger repoints the entry node.
 * For a condition node the `handle` selects the true/false branch; for linear/send
 * nodes it sets `next_node_id`. Exit nodes have no outgoing pointer (no-op).
 *
 * This is the ONLY thing a canvas connection mutates: edges/`next_node_id` stay the
 * runtime source of truth, independent of any presentational `layout`.
 */
export function connectNodes(
    def: WorkflowDefinition,
    sourceId: string,
    targetId: string,
    handle?: string,
): WorkflowDefinition {
    if (sourceId === TRIGGER_NODE_ID) return setEntry(def, targetId)
    const node = def.nodes.find((n) => n.id === sourceId)
    if (!node) return def
    switch (node.type) {
        case "switch": {
            const caseIndex = switchCaseIndex(handle)
            const updated: WorkflowNode =
                caseIndex !== null && caseIndex < node.cases.length
                    ? {
                        ...node,
                        cases: node.cases.map((c, index) =>
                            index === caseIndex ? { ...c, next_node_id: targetId } : c,
                        ),
                    }
                    : { ...node, default_next_node_id: targetId }
            return { ...def, nodes: def.nodes.map((n) => (n.id === sourceId ? updated : n)) }
        }
        case "wait":
        case "drip":
        case "send_sms":
        case "retell_sms_conversation":
        case "send_voice":
        case "send_email":
        case "update_patient_status":
        case "update_appointment":
        case "update_gotracker_appointment":
        case "booking_link":
        case "patient_registration":
        case "json_mapper":
        case "llm":
            return updateNode(def, sourceId, { ...node, next_node_id: targetId })
        case "condition":
            return updateNode(
                def,
                sourceId,
                handle === "false"
                    ? { ...node, false_next_node_id: targetId }
                    : { ...node, true_next_node_id: targetId },
            )
        case "exit":
            return def
    }
}

/** Persist a manual canvas position for a node (presentational only). */
export function setNodePosition(
    def: WorkflowDefinition,
    id: string,
    position: NodePosition,
): WorkflowDefinition {
    return {
        ...def,
        layout: { ...(def.layout ?? {}), [id]: { x: position.x, y: position.y } },
    }
}

/** Drop all manual positions so the deterministic fallback layout applies. */
export function clearLayout(def: WorkflowDefinition): WorkflowDefinition {
    const next = { ...def }
    delete next.layout
    return next
}

/** Ensure schema_version is set before sending to the backend. */
export function serializeDefinition(def: WorkflowDefinition): WorkflowDefinition {
    return { ...normalizeDefinition(def), schema_version: SCHEMA_VERSION }
}

/** Upgrade legacy wait shapes at the frontend seam before editing or saving. */
export function normalizeDefinition(def: WorkflowDefinition): WorkflowDefinition {
    const nodes = (def.nodes as unknown[]).map((raw): WorkflowNode => {
        const node = raw as Record<string, unknown>
        if (node.type === "wait_for_sms_reply") {
            return {
                type: "wait",
                id: String(node.id),
                next_node_id: String(node.next_node_id ?? ""),
                wait_for: {
                    type: "sms_reply",
                    response_window_seconds: Number(node.response_window_seconds ?? 259200),
                    response_mappings: Array.isArray(node.response_mappings)
                        ? node.response_mappings as SmsResponseMapping[]
                        : [],
                },
            }
        }
        if (node.type === "wait" && typeof node.wait_for === "object" && node.wait_for !== null) {
            const waitFor = node.wait_for as Record<string, unknown>
            if (waitFor.type === "sms_reply") {
                const cleanWaitFor = { ...waitFor }
                delete cleanWaitFor.include_reply_key
                return { ...node, wait_for: cleanWaitFor } as unknown as WorkflowNode
            }
        }
        if (node.type === "send_sms") {
            const cleanNode = { ...node }
            delete cleanNode.include_reply_key
            return cleanNode as unknown as WorkflowNode
        }
        if (node.type === "wait" && !("wait_for" in node) && node.delay) {
            return {
                type: "wait",
                id: String(node.id),
                next_node_id: String(node.next_node_id ?? ""),
                wait_for: {
                    type: "time",
                    delay: node.delay as WaitDelay,
                    respect_quiet_hours: node.respect_quiet_hours !== false,
                },
            }
        }
        if (node.type === "retell_sms_conversation") {
            return {
                type: "retell_sms_conversation",
                id: String(node.id),
                chat_profile_id: String(node.chat_profile_id ?? ""),
                next_node_id: String(node.next_node_id ?? ""),
            }
        }
        return raw as WorkflowNode
    })
    return { ...def, nodes }
}

// ---------------------------------------------------------------------------
// Duplicate / copy / paste, and node search
// ---------------------------------------------------------------------------
/**
 * Rewrite a set of nodes with fresh ids.
 *
 * Edges *between* the copied nodes are repointed at their copies, so
 * duplicating a subgraph keeps its internal shape. Edges leaving the set are
 * cleared rather than left pointing at the originals: a copy that silently
 * rejoins the original graph is almost never what someone means by "duplicate",
 * and a dangling pointer is visible in validation where a wrong one is not.
 *
 * Every forward pointer has to be listed here. That is the same knowledge
 * `outgoing()` carries, and the two must be kept in step — a pointer missed
 * here does not fail loudly, it silently links a copy back into the original.
 * `patient_registration.on_abandoned_node_id` is the reason this is a switch
 * rather than a blanket `next_node_id` rewrite.
 */
export function cloneNodes(
    nodes: WorkflowNode[],
    existingIds: Iterable<string>,
): { nodes: WorkflowNode[]; idMap: Record<string, string> } {
    const taken = new Set(existingIds)
    const idMap: Record<string, string> = {}

    for (const node of nodes) {
        const id = genId(node.type, taken)
        taken.add(id)
        idMap[node.id] = id
    }

    const inSet = new Set(nodes.map((n) => n.id))
    /** Required pointers leave an empty string, which validation reports. */
    const repoint = (target: string): string => (inSet.has(target) ? idMap[target] : "")
    /** Optional pointers drop to null instead, since "" is not a valid id. */
    const repointOptional = (target: string | null | undefined): string | null =>
        target && inSet.has(target) ? idMap[target] : null

    const cloned = nodes.map((node): WorkflowNode => {
        const next = { ...node, id: idMap[node.id] } as WorkflowNode
        switch (next.type) {
            case "condition":
                return {
                    ...next,
                    true_next_node_id: repoint(next.true_next_node_id),
                    false_next_node_id: repoint(next.false_next_node_id),
                }
            case "switch":
                return {
                    ...next,
                    cases: next.cases.map((c) => ({ ...c, next_node_id: repoint(c.next_node_id) })),
                    default_next_node_id: repoint(next.default_next_node_id),
                }
            case "patient_registration":
                return {
                    ...next,
                    next_node_id: repoint(next.next_node_id),
                    on_abandoned_node_id: repointOptional(next.on_abandoned_node_id),
                }
            case "exit":
                return next
            default:
                return { ...next, next_node_id: repoint(next.next_node_id) }
        }
    })

    return { nodes: cloned, idMap }
}

/** Offset applied to a duplicate so it does not sit exactly on the original. */
const DUPLICATE_OFFSET = { x: NODE_W * 0.35, y: NODE_H * 0.7 }

/**
 * Add copies of `ids` to the definition. Returns the new definition and the
 * ids of the copies, so the caller can select them.
 */
export function duplicateNodes(
    def: WorkflowDefinition,
    ids: string[],
): { def: WorkflowDefinition; newIds: string[] } {
    const selected = def.nodes.filter((n) => ids.includes(n.id))
    if (!selected.length) return { def, newIds: [] }

    const { nodes: cloned, idMap } = cloneNodes(
        selected,
        def.nodes.map((n) => n.id),
    )

    const layout = { ...(def.layout ?? {}) }
    for (const [oldId, newId] of Object.entries(idMap)) {
        const pos = layout[oldId]
        if (pos) {
            layout[newId] = { x: pos.x + DUPLICATE_OFFSET.x, y: pos.y + DUPLICATE_OFFSET.y }
        }
    }

    return {
        def: { ...def, nodes: [...def.nodes, ...cloned], layout },
        newIds: cloned.map((n) => n.id),
    }
}

/** What a copy places on the builder clipboard. */
export interface NodeClipboard {
    nodes: WorkflowNode[]
    layout: Record<string, NodePosition>
}

export function copyNodes(def: WorkflowDefinition, ids: string[]): NodeClipboard | null {
    const nodes = def.nodes.filter((n) => ids.includes(n.id))
    if (!nodes.length) return null
    const layout: Record<string, NodePosition> = {}
    for (const node of nodes) {
        const pos = def.layout?.[node.id]
        if (pos) layout[node.id] = pos
    }
    return { nodes, layout }
}

export function pasteNodes(
    def: WorkflowDefinition,
    clipboard: NodeClipboard,
): { def: WorkflowDefinition; newIds: string[] } {
    const { nodes: cloned, idMap } = cloneNodes(
        clipboard.nodes,
        def.nodes.map((n) => n.id),
    )

    const layout = { ...(def.layout ?? {}) }
    for (const [oldId, newId] of Object.entries(idMap)) {
        const pos = clipboard.layout[oldId]
        if (pos) {
            layout[newId] = { x: pos.x + DUPLICATE_OFFSET.x, y: pos.y + DUPLICATE_OFFSET.y }
        }
    }

    return {
        def: { ...def, nodes: [...def.nodes, ...cloned], layout },
        newIds: cloned.map((n) => n.id),
    }
}

/**
 * Nodes matching a free-text query, by id, type label, or configured content.
 * Used by the builder's node search on graphs too large to scan by eye.
 */
export function searchNodes(def: WorkflowDefinition, query: string): WorkflowNode[] {
    const needle = query.trim().toLowerCase()
    if (!needle) return []
    return def.nodes.filter((node) => {
        if (node.id.toLowerCase().includes(needle)) return true
        if (node.type.replace(/_/g, " ").includes(needle)) return true
        return searchableText(node).toLowerCase().includes(needle)
    })
}

/**
 * The authored content of a node, as one searchable string. Ids of other
 * records (profile ids, template keys) are deliberately left out: they are not
 * what someone is looking for when they search a canvas.
 */
function searchableText(node: WorkflowNode): string {
    switch (node.type) {
        case "send_sms":
            return node.body_template
        case "send_email":
            return `${node.subject_template} ${node.body_template}`
        case "update_patient_status":
            return `${node.status} ${node.note_template ?? ""}`
        case "update_appointment":
            return `${node.operation} ${node.reason ?? ""}`
        case "update_gotracker_appointment":
            return node.reason ?? ""
        case "booking_link":
            return node.actions.join(" ")
        case "switch":
            return `${node.subject ?? ""} ${node.cases.map((c) => c.label).join(" ")}`
        case "exit":
            return node.outcome ?? ""
        case "llm":
            return `${node.source_field} ${node.output_field} ${node.prompt_template}`
        case "json_mapper":
            return node.mappings.map((m) => `${m.source_path} ${m.target_field}`).join(" ")
        default:
            return ""
    }
}
