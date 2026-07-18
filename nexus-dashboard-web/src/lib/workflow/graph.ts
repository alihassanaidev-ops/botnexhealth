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
    type TriggerType,
    type WorkflowDefinition,
    type WorkflowNode,
    type WorkflowTrigger,
} from "@/types/workflow"

export const TRIGGER_NODE_ID = "__trigger__"

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
    | { kind: "trigger"; trigger: WorkflowTrigger; issueLevel?: "error" | "warning" | null }
    | {
          kind: "step"
          node: WorkflowNode
          isEntry: boolean
          issueLevel?: "error" | "warning" | null
      }

export type FlowNode = Node<FlowNodeData>
export type FlowEdge = Edge & {
    pathOptions?: {
        borderRadius?: number
        offset?: number
    }
    interactionWidth?: number
}

const EDGE_STYLE = {
    strokeWidth: 1.8,
}

const EDGE_LABEL_STYLE = {
    fontSize: 11,
    fontWeight: 600,
}

const EDGE_LABEL_BG_STYLE = {
    fill: "hsl(var(--background))",
    fillOpacity: 0.92,
}

function smartEdge(edge: Omit<FlowEdge, "type"> & { type?: FlowEdge["type"] }): FlowEdge {
    const handle = edge.sourceHandle
    const isFalse = handle === "false"
    const isTrue = handle === "true"
    return {
        type: "smoothstep",
        pathOptions: {
            borderRadius: 14,
            offset: isFalse ? 34 : isTrue ? 22 : 26,
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
    handle?: "true" | "false"
    label?: string
}

/** The forward pointer(s) a node declares (empty targets included so danglers show). */
export function outgoing(node: WorkflowNode): Outgoing[] {
    switch (node.type) {
        case "wait":
        case "send_sms":
        case "send_voice":
        case "send_email":
            return [{ targetId: node.next_node_id }]
        case "condition":
            return [
                { targetId: node.true_next_node_id, handle: "true", label: "Yes" },
                { targetId: node.false_next_node_id, handle: "false", label: "No" },
            ]
        case "exit":
            return []
    }
}

/** Delivery channel a send-node type targets (undefined for non-send nodes). */
const CHANNEL_BY_NODE_TYPE: Partial<Record<NodeType, ChannelKey>> = {
    send_sms: "sms",
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
        node.type === "send_sms" ||
        node.type === "send_voice" ||
        node.type === "send_email"
    ) {
        return node.next_node_id
    }
    return undefined
}

// ---------------------------------------------------------------------------
// Layout — layered BFS from the trigger
// ---------------------------------------------------------------------------
/** Assign a column depth to every node id (trigger = 0, entry = 1, ...). */
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

    // Unreachable nodes: place them in a trailing column so they still render.
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

    // Order nodes within each column by definition order for determinism.
    const rowByDepth = new Map<number, number>()
    const nextRow = (d: number): number => {
        const r = rowByDepth.get(d) ?? 0
        rowByDepth.set(d, r + 1)
        return r
    }

    const nodes: FlowNode[] = []

    // Trigger node (synthetic, column 0).
    nodes.push({
        id: TRIGGER_NODE_ID,
        type: "trigger",
        position: layout[TRIGGER_NODE_ID] ?? { x: X0, y: Y0 + nextRow(0) * ROW_H },
        data: { kind: "trigger", trigger: def.trigger },
        deletable: false,
    })

    for (const n of def.nodes) {
        const d = depth.get(n.id) ?? 1
        nodes.push({
            id: n.id,
            type: "step",
            position: layout[n.id] ?? { x: X0 + d * COL_W, y: Y0 + nextRow(d) * ROW_H },
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
    handle?: "true" | "false"
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
        const layerHeight = Math.max(0, (ids.length - 1) * AUTO_ROW_H)
        const yOffset = Math.max(0, (AUTO_ROW_H * 1.1 - layerHeight) / 2)
        ids.forEach((id, index) => {
            layout[id] = {
                x: X0 + d * AUTO_COL_W,
                y: Y0 + yOffset + index * AUTO_ROW_H,
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
        case "wait":
            return {
                type,
                id,
                delay: { delay_type: "duration", duration_seconds: 3600 },
                next_node_id: "",
                respect_quiet_hours: true,
            }
        case "send_sms":
            return {
                type,
                id,
                body_template: "",
                next_node_id: "",
                respect_quiet_hours: true,
                max_attempts: 1,
            }
        case "send_voice":
            return {
                type,
                id,
                retell_agent_id: "",
                next_node_id: "",
                respect_quiet_hours: true,
                max_attempts: 1,
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

export function createTrigger(type: TriggerType): WorkflowTrigger {
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
                case "wait":
                case "send_sms":
                case "send_voice":
                case "send_email":
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
    handle?: "true" | "false",
): WorkflowDefinition {
    if (sourceId === TRIGGER_NODE_ID) return setEntry(def, targetId)
    const node = def.nodes.find((n) => n.id === sourceId)
    if (!node) return def
    switch (node.type) {
        case "wait":
        case "send_sms":
        case "send_voice":
        case "send_email":
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
    return { ...def, schema_version: SCHEMA_VERSION }
}
