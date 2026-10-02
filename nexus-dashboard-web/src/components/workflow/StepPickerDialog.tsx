/**
 * Step picker for the `+` on a node's unconnected port.
 *
 * The palette on the left is a drag source; this is the same catalogue reached
 * by clicking, for the case where you know which port you are extending and do
 * not want to aim a drop. It groups exactly as the palette does, because two
 * orderings of the same list is how people stop trusting either.
 *
 * Unsupported steps are shown and disabled rather than hidden: "this clinic
 * cannot send email" is useful, "email does not exist" is misleading.
 */
import { useMemo, useState } from "react"
import { Search } from "lucide-react"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { NODE_META, PALETTE_GROUPS } from "@/lib/workflow/catalog"
import type { NodeType } from "@/types/workflow"

export interface StepPickerDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    onPick: (type: NodeType) => void
    /** Node types the clinic's plan and integrations can actually run. */
    supportedNodeTypes?: ReadonlySet<string>
    /** Named in the heading so it is clear which port is being extended. */
    portLabel?: string
}

export default function StepPickerDialog({
    open,
    onOpenChange,
    onPick,
    supportedNodeTypes,
    portLabel,
}: StepPickerDialogProps) {
    const [query, setQuery] = useState("")

    // Clear the filter as the dialog opens: a stale query would hide most of
    // the catalogue behind something the person did not type. Adjusted during
    // render rather than in an effect, so no frame ever shows the old filter.
    const [wasOpen, setWasOpen] = useState(open)
    if (open !== wasOpen) {
        setWasOpen(open)
        if (open) setQuery("")
    }

    const groups = useMemo(() => {
        const needle = query.trim().toLowerCase()
        return PALETTE_GROUPS.map((group) => ({
            ...group,
            types: group.types.filter((type) => {
                if (!needle) return true
                const meta = NODE_META[type]
                return (
                    meta.label.toLowerCase().includes(needle)
                    || meta.description.toLowerCase().includes(needle)
                    || type.replace(/_/g, " ").includes(needle)
                )
            }),
        })).filter((group) => group.types.length > 0)
    }, [query])

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Add a step</DialogTitle>
                    <DialogDescription>
                        {portLabel
                            ? `Runs on the "${portLabel}" branch.`
                            : "Runs after the selected step."}
                    </DialogDescription>
                </DialogHeader>

                <div className="relative">
                    <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        autoFocus
                        className="h-9 pl-7"
                        placeholder="Search steps…"
                        aria-label="Search steps"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                    />
                </div>

                <div className="max-h-[50vh] space-y-4 overflow-y-auto pr-1">
                    {groups.map((group) => (
                        <div key={group.group}>
                            <h4 className="mb-1.5 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                {group.title}
                            </h4>
                            <div className="space-y-1">
                                {group.types.map((type) => {
                                    const meta = NODE_META[type]
                                    const Icon = meta.icon
                                    const unsupported =
                                        supportedNodeTypes !== undefined
                                        && !supportedNodeTypes.has(type)
                                    return (
                                        <button
                                            key={type}
                                            type="button"
                                            disabled={unsupported}
                                            title={
                                                unsupported
                                                    ? "Not available for this clinic"
                                                    : meta.description
                                            }
                                            onClick={() => onPick(type)}
                                            className={cn(
                                                "flex w-full items-start gap-2.5 rounded-md border border-transparent p-2 text-left transition-colors",
                                                unsupported
                                                    ? "cursor-not-allowed opacity-40"
                                                    : "hover:border-border hover:bg-accent",
                                            )}
                                        >
                                            <div
                                                className={cn(
                                                    "grid size-7 shrink-0 place-items-center rounded-md",
                                                    meta.accent,
                                                )}
                                            >
                                                <Icon className="h-3.5 w-3.5" />
                                            </div>
                                            <div className="min-w-0">
                                                <div className="text-sm font-medium">{meta.label}</div>
                                                <p className="truncate text-xs text-muted-foreground">
                                                    {meta.description}
                                                </p>
                                            </div>
                                        </button>
                                    )
                                })}
                            </div>
                        </div>
                    ))}
                    {groups.length === 0 && (
                        <p className="px-1 py-6 text-center text-sm text-muted-foreground">
                            No steps match “{query.trim()}”.
                        </p>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    )
}
