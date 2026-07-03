/**
 * Dry-run simulation dialog. Walks the workflow from the entry node WITHOUT
 * dispatching anything (findings.md §3 — there is no backend test-run endpoint;
 * `/enroll` runs for real). Lets the tester flip condition branches to explore paths.
 */
import { useMemo, useState } from "react"
import { AlertTriangle, FlaskConical } from "lucide-react"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { NODE_META } from "@/lib/workflow/catalog"
import { simulateRun } from "@/lib/workflow/test-run"
import type { WorkflowDefinition } from "@/types/workflow"

export interface TestRunDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    def: WorkflowDefinition
}

export default function TestRunDialog({ open, onOpenChange, def }: TestRunDialogProps) {
    const [choices, setChoices] = useState<Record<string, boolean>>({})

    const conditions = useMemo(() => def.nodes.filter((n) => n.type === "condition"), [def.nodes])
    const result = useMemo(
        () => simulateRun(def, { conditionChoices: choices }),
        [def, choices],
    )

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <FlaskConical className="h-4 w-4" /> Test run (simulation)
                    </DialogTitle>
                    <DialogDescription>
                        Simulates the path a sample contact would take. Uses sample merge data and sends
                        nothing.
                    </DialogDescription>
                </DialogHeader>

                {conditions.length > 0 && (
                    <div className="space-y-2 rounded-md border border-border p-3">
                        <p className="text-xs font-medium text-muted-foreground">Condition branches</p>
                        {conditions.map((c) => (
                            <div key={c.id} className="flex items-center justify-between gap-3">
                                <Label className="font-mono text-xs">{c.id}</Label>
                                <div className="flex items-center gap-2 text-xs">
                                    <span className={cn(!(choices[c.id] ?? true) && "font-medium text-foreground")}>No</span>
                                    <Switch
                                        checked={choices[c.id] ?? true}
                                        onCheckedChange={(v) => setChoices((prev) => ({ ...prev, [c.id]: v }))}
                                    />
                                    <span className={cn((choices[c.id] ?? true) && "font-medium text-foreground")}>Yes</span>
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                <ol className="space-y-2">
                    {result.steps.map((step, i) => {
                        const meta = NODE_META[step.node_type]
                        const Icon = meta.icon
                        return (
                            <li key={i} className="flex gap-2.5">
                                <div className={cn("grid size-7 shrink-0 place-items-center rounded-md", meta.accent)}>
                                    <Icon className="h-3.5 w-3.5" />
                                </div>
                                <div className="min-w-0 flex-1 rounded-md border border-border bg-card px-2.5 py-1.5">
                                    <div className="text-sm font-medium">{step.summary}</div>
                                    {step.detail && (
                                        <div className="mt-0.5 whitespace-pre-wrap break-words text-xs text-muted-foreground">
                                            {step.detail}
                                        </div>
                                    )}
                                </div>
                            </li>
                        )
                    })}
                </ol>

                {result.truncated && (
                    <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        Simulation stopped after 50 steps — the workflow may contain a loop.
                    </div>
                )}

                <div className="rounded-md bg-muted/50 px-3 py-2 text-sm">
                    <span className="text-muted-foreground">Final outcome: </span>
                    <span className="font-medium">{result.outcome ?? "— (no exit reached)"}</span>
                </div>
            </DialogContent>
        </Dialog>
    )
}
