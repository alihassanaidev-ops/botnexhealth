/**
 * React Flow canvas wrapper. Renders derived nodes/edges with pan/zoom, node
 * selection, and validation tinting. Nodes are NOT draggable/connectable: the layout
 * is derived deterministically (not persisted) and edges are authored via the step
 * config panel's next-step selectors — keeping authoring on typed forms, not raw
 * edge-dragging (Plan 02 architecture decision).
 */
import { useMemo } from "react"
import {
    Background,
    BackgroundVariant,
    Controls,
    MiniMap,
    ReactFlow,
    ReactFlowProvider,
    type NodeMouseHandler,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { StepNodeCard, TriggerNodeCard } from "./WorkflowNode"
import type { FlowEdge, FlowNode } from "@/lib/workflow/graph"

/** Stable nodeTypes map for React Flow (module scope so the reference never changes). */
const workflowNodeTypes = {
    trigger: TriggerNodeCard,
    step: StepNodeCard,
}

export interface WorkflowCanvasProps {
    nodes: FlowNode[]
    edges: FlowEdge[]
    selectedId?: string | null
    onSelect?: (id: string | null) => void
    /** Hide zoom controls + minimap for compact read-only previews. */
    minimal?: boolean
}

function InnerCanvas({ nodes, edges, selectedId, onSelect, minimal }: WorkflowCanvasProps) {
    // Reflect selection into React Flow's node state (single source of truth: our prop).
    const rfNodes = useMemo(
        () => nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
        [nodes, selectedId],
    )

    const handleNodeClick: NodeMouseHandler = (_e, node) => {
        onSelect?.(node.id)
    }

    return (
        <ReactFlow
            nodes={rfNodes}
            edges={edges}
            nodeTypes={workflowNodeTypes}
            onNodeClick={handleNodeClick}
            onPaneClick={() => onSelect?.(null)}
            nodesDraggable={false}
            nodesConnectable={false}
            edgesFocusable={false}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.3}
            proOptions={{ hideAttribution: true }}
            className="bg-muted/20"
        >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} className="opacity-60" />
            {!minimal && <Controls showInteractive={false} />}
            {!minimal && (
                <MiniMap
                    pannable
                    zoomable
                    className="!bg-background/80 !border !border-border"
                    maskColor="rgba(0,0,0,0.06)"
                />
            )}
        </ReactFlow>
    )
}

export default function WorkflowCanvas(props: WorkflowCanvasProps) {
    return (
        <ReactFlowProvider>
            <InnerCanvas {...props} />
        </ReactFlowProvider>
    )
}
