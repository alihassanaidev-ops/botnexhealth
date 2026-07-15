/**
 * React Flow canvas wrapper. Renders derived nodes/edges with pan/zoom, node
 * selection, and validation tinting.
 *
 * In `editable` mode nodes can be dragged (positions bubble up as presentational
 * `layout` — never touching execution semantics) and edges can be drawn between
 * handles (which sets the source node's `next_node_id` / condition branch). A
 * "Tidy layout" action clears manual positions and re-runs the deterministic
 * auto-layout. Read-only previews (default) keep nodes fixed & non-connectable.
 */
import { useCallback, useEffect } from "react"
import {
    Background,
    BackgroundVariant,
    Controls,
    Panel,
    ReactFlow,
    ReactFlowProvider,
    useNodesState,
    useReactFlow,
    type NodeMouseHandler,
    type OnConnect,
    type OnNodeDrag,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { LayoutGrid } from "lucide-react"
import { StepNodeCard, TriggerNodeCard } from "./WorkflowNode"
import { WORKFLOW_NODE_DND_MIME } from "@/lib/workflow/catalog"
import type { FlowEdge, FlowNode } from "@/lib/workflow/graph"
import type { NodePosition, NodeType } from "@/types/workflow"

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
    /** Enable node dragging + drag-to-connect (author mode). Default: read-only. */
    editable?: boolean
    /** Drag-to-connect: set the source node's forward pointer to `targetId`. */
    onConnectNodes?: (sourceId: string, targetId: string, handle?: "true" | "false") => void
    /** Node drag settled: persist its presentational position. */
    onNodePositionChange?: (id: string, position: NodePosition) => void
    /** Re-run the auto-layout and drop manual positions. */
    onTidyLayout?: () => void
    /** Palette node dropped on the canvas at a flow-space position (author mode). */
    onAddNodeAt?: (type: NodeType, position: NodePosition) => void
}

function InnerCanvas({
    nodes,
    edges,
    selectedId,
    onSelect,
    minimal,
    editable,
    onConnectNodes,
    onNodePositionChange,
    onTidyLayout,
    onAddNodeAt,
}: WorkflowCanvasProps) {
    const { screenToFlowPosition } = useReactFlow()
    // Local node state so React Flow can drive drag interactions smoothly; we re-sync
    // from the derived prop whenever the definition/selection changes. (Prop remains the
    // single source of truth — drag results are bubbled up via onNodeDragStop.)
    const [rfNodes, setRfNodes, onNodesChange] = useNodesState<FlowNode>([])

    useEffect(() => {
        setRfNodes(nodes.map((n) => ({ ...n, selected: n.id === selectedId })))
    }, [nodes, selectedId, setRfNodes])

    const handleNodeClick: NodeMouseHandler = (_e, node) => {
        onSelect?.(node.id)
    }

    const handleConnect: OnConnect = useCallback(
        (conn) => {
            if (!conn.source || !conn.target) return
            const handle =
                conn.sourceHandle === "true" || conn.sourceHandle === "false"
                    ? conn.sourceHandle
                    : undefined
            onConnectNodes?.(conn.source, conn.target, handle)
        },
        [onConnectNodes],
    )

    const handleNodeDragStop: OnNodeDrag<FlowNode> = useCallback(
        (_e, node) => {
            onNodePositionChange?.(node.id, { x: node.position.x, y: node.position.y })
        },
        [onNodePositionChange],
    )

    const handleDragOver = useCallback((e: React.DragEvent) => {
        if (!e.dataTransfer.types.includes(WORKFLOW_NODE_DND_MIME)) return
        e.preventDefault()
        e.dataTransfer.dropEffect = "copy"
    }, [])

    const handleDrop = useCallback(
        (e: React.DragEvent) => {
            const type = e.dataTransfer.getData(WORKFLOW_NODE_DND_MIME)
            if (!type) return
            e.preventDefault()
            // Convert the cursor point to flow coordinates so the node lands under the drop.
            const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
            onAddNodeAt?.(type as NodeType, position)
        },
        [screenToFlowPosition, onAddNodeAt],
    )

    return (
        <ReactFlow
            nodes={rfNodes}
            edges={edges}
            nodeTypes={workflowNodeTypes}
            onNodesChange={onNodesChange}
            onNodeClick={handleNodeClick}
            onPaneClick={() => onSelect?.(null)}
            onConnect={editable ? handleConnect : undefined}
            onNodeDragStop={editable ? handleNodeDragStop : undefined}
            onDragOver={editable ? handleDragOver : undefined}
            onDrop={editable ? handleDrop : undefined}
            nodesDraggable={!!editable}
            nodesConnectable={!!editable}
            edgesFocusable={false}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.3}
            proOptions={{ hideAttribution: true }}
            className="bg-muted/20"
        >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} className="opacity-60" />
            {!minimal && <Controls showInteractive={false} showFitView />}
            {editable && onTidyLayout && (
                <Panel position="top-right">
                    <button
                        type="button"
                        onClick={onTidyLayout}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/90 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
                    >
                        <LayoutGrid className="h-3.5 w-3.5" /> Tidy layout
                    </button>
                </Panel>
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
