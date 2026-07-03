/**
 * Side palette for adding steps to the workflow, grouped by channel / control flow
 * (click-to-add). Also exposes a "Trigger" affordance to open the trigger config.
 */
import { cn } from "@/lib/utils"
import { NODE_META, PALETTE_GROUPS, TRIGGER_META } from "@/lib/workflow/catalog"
import type { NodeType, WorkflowTrigger } from "@/types/workflow"

export interface WorkflowPaletteProps {
    trigger: WorkflowTrigger
    onAddNode: (type: NodeType) => void
    onEditTrigger: () => void
    disabled?: boolean
}

export default function WorkflowPalette({ trigger, onAddNode, onEditTrigger, disabled }: WorkflowPaletteProps) {
    const triggerMeta = TRIGGER_META[trigger.type]
    return (
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-3">
            <div>
                <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Trigger
                </h3>
                <button
                    type="button"
                    onClick={onEditTrigger}
                    className="flex w-full items-center gap-2.5 rounded-md border border-dashed border-primary/50 bg-primary/5 p-2.5 text-left transition-colors hover:bg-primary/10"
                >
                    <div className="grid size-8 shrink-0 place-items-center rounded-md bg-primary/15 text-primary">
                        <triggerMeta.icon className="h-4 w-4" />
                    </div>
                    <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{triggerMeta.label}</div>
                        <div className="truncate text-xs text-muted-foreground">Configure enrollment</div>
                    </div>
                </button>
            </div>

            {PALETTE_GROUPS.map((group) => (
                <div key={group.group}>
                    <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {group.title}
                    </h3>
                    <div className="space-y-1.5">
                        {group.types.map((type) => (
                            <PaletteItem key={type} type={type} onClick={() => onAddNode(type)} disabled={disabled} />
                        ))}
                    </div>
                </div>
            ))}
        </div>
    )
}

function PaletteItem({ type, onClick, disabled }: { type: NodeType; onClick: () => void; disabled?: boolean }) {
    const meta = NODE_META[type]
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            className={cn(
                "flex w-full items-center gap-2.5 rounded-md border border-border bg-card p-2.5 text-left transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
            )}
        >
            <div className={cn("grid size-8 shrink-0 place-items-center rounded-md", meta.accent)}>
                <meta.icon className="h-4 w-4" />
            </div>
            <div className="min-w-0">
                <div className="truncate text-sm font-medium">{meta.label}</div>
                <div className="truncate text-xs text-muted-foreground">{meta.description}</div>
            </div>
        </button>
    )
}
