/**
 * Side palette for adding steps to the workflow, grouped by channel / control flow
 * (click-to-add). Also exposes a "Trigger" affordance to open the trigger config.
 */
import { cn } from "@/lib/utils"
import { NODE_META, PALETTE_GROUPS, TRIGGER_META, WORKFLOW_NODE_DND_MIME } from "@/lib/workflow/catalog"
import type { NodeType, WorkflowTrigger } from "@/types/workflow"
import { Input } from "@/components/ui/input"
import { Search } from "lucide-react"

export interface WorkflowPaletteProps {
    trigger: WorkflowTrigger
    onEditTrigger: () => void
    disabled?: boolean
    supportedNodeTypes?: ReadonlySet<string>
    /** Steps matching the current search, with a jump-to handler. */
    searchResults?: { id: string; type: NodeType; label: string }[]
    searchQuery?: string
    onSearchChange?: (query: string) => void
    onSelectResult?: (id: string) => void
}

export default function WorkflowPalette({
    trigger,
    onEditTrigger,
    disabled,
    supportedNodeTypes,
    searchResults,
    searchQuery,
    onSearchChange,
    onSelectResult,
}: WorkflowPaletteProps) {
    const triggerMeta = TRIGGER_META[trigger.type]
    return (
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-3">
            {onSearchChange && (
                <div className="space-y-1.5">
                    {/* Large graphs cannot be scanned by eye; this jumps straight
                        to a step by id, type, or its configured content. */}
                    <div className="relative">
                        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                            className="h-8 pl-7"
                            aria-label="Find a step"
                            placeholder="Find a step…"
                            value={searchQuery ?? ""}
                            onChange={(e) => onSearchChange(e.target.value)}
                        />
                    </div>
                    {searchQuery?.trim() ? (
                        <div className="space-y-1">
                            {searchResults?.length ? (
                                searchResults.map((result) => (
                                    <button
                                        key={result.id}
                                        type="button"
                                        onClick={() => onSelectResult?.(result.id)}
                                        className="flex w-full items-center gap-2 rounded-md border border-border bg-card p-1.5 text-left text-xs hover:bg-accent"
                                    >
                                        <span className="truncate font-medium">{result.label}</span>
                                        <span className="ml-auto shrink-0 text-muted-foreground">
                                            {result.id}
                                        </span>
                                    </button>
                                ))
                            ) : (
                                <p className="px-1 text-xs text-muted-foreground">No steps match.</p>
                            )}
                        </div>
                    ) : null}
                </div>
            )}
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
                        {group.types.filter((type) => !supportedNodeTypes || supportedNodeTypes.has(type)).map((type) => (
                            <PaletteItem key={type} type={type} disabled={disabled} />
                        ))}
                    </div>
                </div>
            ))}
        </div>
    )
}

function PaletteItem({ type, disabled }: { type: NodeType; disabled?: boolean }) {
    const meta = NODE_META[type]
    return (
        <div
            role="button"
            aria-disabled={disabled}
            title={disabled ? undefined : "Drag onto the canvas to add"}
            draggable={!disabled}
            onDragStart={(e) => {
                // Drag a node type onto the canvas to add it (drag-only; no click-to-add).
                e.dataTransfer.setData(WORKFLOW_NODE_DND_MIME, type)
                e.dataTransfer.effectAllowed = "copy"
            }}
            className={cn(
                "flex w-full items-center gap-2.5 rounded-md border border-border bg-card p-2.5 text-left transition-colors hover:bg-accent",
                disabled ? "cursor-not-allowed opacity-50" : "cursor-grab active:cursor-grabbing",
            )}
        >
            <div className={cn("grid size-8 shrink-0 place-items-center rounded-md", meta.accent)}>
                <meta.icon className="h-4 w-4" />
            </div>
            <div className="min-w-0">
                <div className="truncate text-sm font-medium">{meta.label}</div>
                <div className="truncate text-xs text-muted-foreground">{meta.description}</div>
            </div>
        </div>
    )
}
