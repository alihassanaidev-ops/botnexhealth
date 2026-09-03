/**
 * Editable connection: draws the same rounded orthogonal path as a read-only
 * edge, and reveals a `+` at its midpoint on hover.
 *
 * Without this, adding a step between two existing ones means dropping it loose
 * on the canvas and repointing both sides by hand. The port `+` on a node only
 * appears on ports that are still unconnected, which is exactly the case that
 * does not need help.
 *
 * The branch label and the `+` share the midpoint: the label is the resting
 * state, the button takes over on hover, so a dense fan-out gains no extra
 * clutter.
 */
import { useState } from "react"
import {
    BaseEdge,
    EdgeLabelRenderer,
    getSmoothStepPath,
    type EdgeProps,
} from "@xyflow/react"
import { Plus } from "lucide-react"

export type InsertableEdgeData = {
    /** Branch label ("Yes", "Otherwise", a switch case name). */
    branchLabel?: string
    /** Open the step picker for an insert between this edge's two nodes. */
    onInsert?: () => void
}

export function InsertableEdge({
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    markerEnd,
    style,
    data,
    pathOptions,
}: EdgeProps & { data?: InsertableEdgeData; pathOptions?: { offset?: number; borderRadius?: number } }) {
    const [hovered, setHovered] = useState(false)
    const [edgePath, labelX, labelY] = getSmoothStepPath({
        sourceX,
        sourceY,
        targetX,
        targetY,
        sourcePosition,
        targetPosition,
        offset: pathOptions?.offset,
        borderRadius: pathOptions?.borderRadius ?? 12,
    })

    const showButton = Boolean(data?.onInsert) && hovered

    return (
        <>
            <BaseEdge
                id={id}
                path={edgePath}
                markerEnd={markerEnd}
                style={style}
            />
            {/* Invisible fat stroke so the pointer does not have to land on the
                2px visible line to reveal the button. */}
            <path
                d={edgePath}
                fill="none"
                stroke="transparent"
                strokeWidth={22}
                style={{ pointerEvents: "stroke" }}
                onMouseEnter={() => setHovered(true)}
                onMouseLeave={() => setHovered(false)}
            />
            <EdgeLabelRenderer>
                <div
                    className="nodrag nopan absolute -translate-x-1/2 -translate-y-1/2"
                    style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
                    onMouseEnter={() => setHovered(true)}
                    onMouseLeave={() => setHovered(false)}
                >
                    {showButton ? (
                        <button
                            type="button"
                            title="Insert a step here"
                            aria-label="Insert a step here"
                            onClick={(event) => {
                                event.stopPropagation()
                                data?.onInsert?.()
                            }}
                            className="grid size-5 place-items-center rounded-full border border-border bg-background text-muted-foreground shadow-sm transition-colors hover:border-primary hover:bg-primary hover:text-primary-foreground"
                        >
                            <Plus className="h-3 w-3" />
                        </button>
                    ) : data?.branchLabel ? (
                        <span className="rounded bg-card/95 px-1.5 py-0.5 text-[11px] font-semibold text-foreground">
                            {data.branchLabel}
                        </span>
                    ) : null}
                </div>
            </EdgeLabelRenderer>
        </>
    )
}
