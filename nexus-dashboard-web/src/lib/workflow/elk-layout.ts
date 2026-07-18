import ELK, { type ElkNode } from "elkjs/lib/elk.bundled.js"
import {
    TRIGGER_NODE_ID,
    autoLayoutDefinition,
    outgoing,
    type FlowEdge,
} from "./graph"
import type { NodePosition, WorkflowDefinition } from "@/types/workflow"

const NODE_W = 240
const NODE_H = 92

const elk = new ELK({
    defaultLayoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "RIGHT",
        "elk.layered.spacing.nodeNodeBetweenLayers": "110",
        "elk.spacing.nodeNode": "70",
        "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
        "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
        "elk.edgeRouting": "ORTHOGONAL",
        "elk.padding": "[top=40,left=40,bottom=40,right=40]",
    },
})

function elkEdges(def: WorkflowDefinition): FlowEdge[] {
    const ids = new Set(def.nodes.map((n) => n.id))
    const edges: FlowEdge[] = []
    if (ids.has(def.entry_node_id)) {
        edges.push({
            id: `elk-${TRIGGER_NODE_ID}-${def.entry_node_id}`,
            source: TRIGGER_NODE_ID,
            target: def.entry_node_id,
        })
    }
    for (const node of def.nodes) {
        for (const out of outgoing(node)) {
            if (!out.targetId || !ids.has(out.targetId)) continue
            edges.push({
                id: `elk-${node.id}-${out.handle ?? "next"}-${out.targetId}`,
                source: node.id,
                target: out.targetId,
            })
        }
    }
    return edges
}

function toElkGraph(def: WorkflowDefinition): ElkNode {
    return {
        id: "workflow",
        children: [
            { id: TRIGGER_NODE_ID, width: NODE_W, height: NODE_H },
            ...def.nodes.map((node) => ({
                id: node.id,
                width: NODE_W,
                height: NODE_H,
            })),
        ],
        edges: elkEdges(def).map((edge) => ({
            id: edge.id,
            sources: [edge.source],
            targets: [edge.target],
        })),
    }
}

/** Compute an ELK layout and persist it as presentational workflow layout. */
export async function elkAutoLayoutDefinition(def: WorkflowDefinition): Promise<WorkflowDefinition> {
    try {
        const graph = await elk.layout(toElkGraph(def))
        const layout: Record<string, NodePosition> = {}
        for (const child of graph.children ?? []) {
            if (child.x === undefined || child.y === undefined) continue
            layout[child.id] = { x: child.x, y: child.y }
        }
        if (!layout[TRIGGER_NODE_ID] || def.nodes.some((node) => !layout[node.id])) {
            return autoLayoutDefinition(def)
        }
        return { ...def, layout }
    } catch {
        return autoLayoutDefinition(def)
    }
}
