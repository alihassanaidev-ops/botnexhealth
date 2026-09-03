/**
 * React Flow canvas wrapper. Renders derived nodes/edges with pan/zoom, node
 * selection, and validation tinting.
 *
 * In `editable` mode nodes can be dragged (positions bubble up as presentational
 * `layout` — never touching execution semantics) and edges can be drawn between
 * handles (which sets the source node's `next_node_id` / condition branch). The
 * Auto layout action persists a computed presentational layout. Read-only previews
 * (default) keep nodes fixed & non-connectable.
 */
import { useCallback, useEffect, useMemo } from "react"
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
import { LayoutGrid, Loader2 } from "lucide-react"
import { StepNodeCard, TriggerNodeCard } from "./WorkflowNode"
import { InsertableEdge } from "./WorkflowEdge"
import { WORKFLOW_NODE_DND_MIME } from "@/lib/workflow/catalog"
import type { FlowEdge, FlowNode } from "@/lib/workflow/graph"
import type { NodePosition, NodeType } from "@/types/workflow"

/** Stable nodeTypes map for React Flow (module scope so the reference never changes). */
const workflowNodeTypes = {
    trigger: TriggerNodeCard,
    step: StepNodeCard,
}

const workflowEdgeTypes = {
    insertable: InsertableEdge,
}

export interface WorkflowCanvasProps {
    nodes: FlowNode[]
    edges: FlowEdge[]
    selectedId?: string | null
    onSelect?: (id: string | null) => void
    /**
     * Every selected node. `selectedId` remains the one the config panel edits;
     * this is the set that bulk actions (duplicate, copy, delete) operate on.
     */
    selectedIds?: string[]
    onSelectionChange?: (ids: string[]) => void
    /**
     * Add a step from a node's unconnected port. Injected into node data rather
     * than passed down, because React Flow owns the node components; leaving it
     * undefined is what hides the `+` in read-only previews.
     */
    onAddFromPort?: (sourceId: string, handle?: string) => void
    /** Hide zoom controls + minimap for compact read-only previews. */
    minimal?: boolean
    /** Enable node dragging + drag-to-connect (author mode). Default: read-only. */
    editable?: boolean
    /** Drag-to-connect: set the source node's forward pointer to `targetId`. */
    onConnectNodes?: (sourceId: string, targetId: string, handle?: "true" | "false") => void
    /** Node drag settled: persist its presentational position. */
    onNodePositionChange?: (id: string, position: NodePosition) => void
    /** Re-run the auto-layout and persist fresh presentational positions. */
    onAutoLayout?: () => void | Promise<void>
    autoLayoutBusy?: boolean
    /**
     * Insert a step into an existing connection: the new node takes `targetId`
     * as its own next, and the source port repoints at it. Leaving this
     * undefined is what hides the `+` on edges in read-only previews.
     */
    onInsertOnEdge?: (sourceId: string, targetId: string, handle?: string) => void
    /** Palette node dropped on the canvas at a flow-space position (author mode). */
    onAddNodeAt?: (type: NodeType, position: NodePosition) => void
}

function InnerCanvas({
    nodes,
    edges,
    selectedId,
    onSelect,
    selectedIds,
    onSelectionChange,
    onAddFromPort,
    minimal,
    editable,
    onConnectNodes,
    onNodePositionChange,
    onAutoLayout,
    autoLayoutBusy,
    onAddNodeAt,
    onInsertOnEdge,
}: WorkflowCanvasProps) {
    const { screenToFlowPosition } = useReactFlow()
    // Local node state so React Flow can drive drag interactions smoothly; we re-sync
    // from the derived prop whenever the definition/selection changes. (Prop remains the
    // single source of truth — drag results are bubbled up via onNodeDragStop.)
    const [rfNodes, setRfNodes, onNodesChange] = useNodesState<FlowNode>([])

    useEffect(() => {
        const multi = new Set(selectedIds ?? [])
        setRfNodes(
            nodes.map((n) => ({
                ...n,
                selected: n.id === selectedId || multi.has(n.id),
                data: { ...n.data, onAddFromPort },
            })),
        )
    }, [nodes, selectedId, selectedIds, onAddFromPort, setRfNodes])

    const displayEdges = useMemo(
        () =>
            edges.map((edge) => ({
                ...edge,
                type: "insertable",
                data: {
                    branchLabel: typeof edge.label === "string" ? edge.label : undefined,
                    onInsert:
                        editable && onInsertOnEdge
                            ? () =>
                                  onInsertOnEdge(
                                      edge.source,
                                      edge.target,
                                      edge.sourceHandle ?? undefined,
                                  )
                            : undefined,
                },
            })),
        [edges, editable, onInsertOnEdge],
    )

    const handleNodeClick: NodeMouseHandler = (event, node) => {
        // Shift/Cmd-click toggles membership; a plain click replaces the
        // selection, which is what every canvas editor does.
        if (event.shiftKey || event.metaKey || event.ctrlKey) {
            const current = new Set(selectedIds ?? (selectedId ? [selectedId] : []))
            if (current.has(node.id)) current.delete(node.id)
            else current.add(node.id)
            onSelectionChange?.([...current])
            return
        }
        onSelectionChange?.([node.id])
        onSelect?.(node.id)
    }

    // Rubber-band selection over the pane.
    const handleSelectionChange = useCallback(
        ({ nodes: picked }: { nodes: { id: string }[] }) => {
            if (!onSelectionChange) return
            const ids = picked.map((n) => n.id)
            // React Flow also fires this on programmatic updates; ignoring an
            // empty payload keeps a click on the pane from clearing twice.
            if (ids.length) onSelectionChange(ids)
        },
        [onSelectionChange],
    )

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
            edges={displayEdges}
            nodeTypes={workflowNodeTypes}
            edgeTypes={workflowEdgeTypes}
            onNodesChange={onNodesChange}
            onNodeClick={handleNodeClick}
            onPaneClick={() => {
                onSelect?.(null)
                onSelectionChange?.([])
            }}
            onSelectionChange={handleSelectionChange}
            // Drag on the pane draws a selection box while a modifier is held;
            // without a modifier it pans, which is the more common intent.
            selectionOnDrag={editable}
            panOnDrag={editable ? [1, 2] : true}
            multiSelectionKeyCode={["Shift", "Meta", "Control"]}
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
            {editable && onAutoLayout && (
                <Panel position="top-right">
                    <button
                        type="button"
                        onClick={onAutoLayout}
                        disabled={autoLayoutBusy}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/90 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
                    >
                        {autoLayoutBusy ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <LayoutGrid className="h-3.5 w-3.5" />
                        )}
                        Auto layout
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
