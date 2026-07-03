/**
 * Version history / published-snapshot viewer.
 *
 * Lists every published version of a workflow (newest-first) from
 * `GET /automation/workflows/{id}/versions` and shows the selected version's
 * definition on a read-only canvas. Defaults to the current published version.
 */
import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, History, PencilRuler, ShieldCheck } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { getWorkflow, listVersions } from "@/lib/workflow-api"
import { definitionToFlow } from "@/lib/workflow/graph"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import type { AutomationWorkflow } from "@/types"
import type { WorkflowDefinition, WorkflowVersion } from "@/types/workflow"

const CONTENT_CLASS_LABELS: Record<string, string> = {
    transactional_care: "Transactional care",
    recall: "Recall",
    sales: "Sales",
    marketing: "Marketing",
}

function formatTimestamp(iso: string): string {
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export default function WorkflowVersions() {
    const { id } = useParams<{ id: string }>()
    const [workflow, setWorkflow] = useState<AutomationWorkflow | null>(null)
    const [versions, setVersions] = useState<WorkflowVersion[]>([])
    const [selectedId, setSelectedId] = useState<string | null>(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        if (!id) return
        ;(async () => {
            setLoading(true)
            try {
                const [wf, vers] = await Promise.all([getWorkflow(id), listVersions(id)])
                setWorkflow(wf)
                setVersions(vers)
                const current = vers.find((v) => v.is_current) ?? vers[0] ?? null
                setSelectedId(current?.id ?? null)
            } catch {
                toast.error("Failed to load version history")
            } finally {
                setLoading(false)
            }
        })()
    }, [id])

    const selected = useMemo(
        () => versions.find((v) => v.id === selectedId) ?? null,
        [versions, selectedId],
    )
    const def = (selected?.definition as WorkflowDefinition | null) ?? null
    const flow = useMemo(() => (def ? definitionToFlow(def) : { nodes: [], edges: [] }), [def])

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-32 -right-32 h-[420px] w-[420px] rounded-full bg-transparent blur-[100px] dark:bg-violet-700/20" />
            </div>

            <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" asChild className="h-8 w-8">
                    <Link to={id ? `/institution-admin/campaigns/${id}` : "/institution-admin/campaigns"}>
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <span className="text-sm text-muted-foreground">Campaign</span>
            </div>

            {loading ? (
                <Skeleton className="h-9 w-64" />
            ) : (
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <h2 className="flex items-center gap-2 text-3xl font-bold tracking-tight">
                            <History className="h-7 w-7" /> Version history
                        </h2>
                        <p className="mt-1 text-muted-foreground">
                            {workflow?.name}
                            {versions.length > 0 && (
                                <span className="ml-2 text-sm">
                                    · {versions.length} version{versions.length > 1 ? "s" : ""}
                                </span>
                            )}
                        </p>
                    </div>
                    {workflow && workflow.status !== "archived" && (
                        <Button variant="outline" size="sm" className="gap-1.5" asChild>
                            <Link to={`/institution-admin/campaigns/${workflow.id}/builder`}>
                                <PencilRuler className="h-3.5 w-3.5" /> Open builder
                            </Link>
                        </Button>
                    )}
                </div>
            )}

            {!loading && versions.length === 0 && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                    This workflow has no published versions yet.
                </p>
            )}

            {!loading && versions.length > 0 && (
                <div className="grid gap-6 lg:grid-cols-[minmax(0,340px)_1fr]">
                    {/* Version list */}
                    <ul className="space-y-2">
                        {versions.map((v) => {
                            const active = v.id === selectedId
                            return (
                                <li key={v.id}>
                                    <button
                                        type="button"
                                        onClick={() => setSelectedId(v.id)}
                                        className={cn(
                                            "flex w-full flex-col gap-1 rounded-md border px-3 py-2.5 text-left text-sm transition-colors",
                                            active
                                                ? "border-primary bg-primary/5"
                                                : "border-border hover:bg-muted/50",
                                        )}
                                    >
                                        <div className="flex items-center gap-2">
                                            <span className="font-semibold">Version {v.version_number}</span>
                                            {v.is_current && (
                                                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-400">
                                                    <ShieldCheck className="h-3 w-3" /> Current
                                                </span>
                                            )}
                                        </div>
                                        <span className="text-xs text-muted-foreground">
                                            Published {formatTimestamp(v.published_at)}
                                        </span>
                                        {v.content_classification && (
                                            <span className="text-xs text-muted-foreground">
                                                {CONTENT_CLASS_LABELS[v.content_classification] ??
                                                    v.content_classification}
                                            </span>
                                        )}
                                    </button>
                                </li>
                            )
                        })}
                    </ul>

                    {/* Selected version canvas */}
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base font-semibold">
                                {selected ? `Version ${selected.version_number}` : "Version"}{" "}
                                {selected?.definition_checksum && (
                                    <span className="ml-1 font-mono text-xs font-normal text-muted-foreground">
                                        {selected.definition_checksum.slice(0, 8)}…
                                    </span>
                                )}
                            </CardTitle>
                            <p className="text-xs text-muted-foreground">
                                {def?.nodes.length ?? 0} step(s)
                            </p>
                        </CardHeader>
                        <CardContent>
                            {def ? (
                                <div className="h-[55vh] w-full overflow-hidden rounded-md border border-border">
                                    <WorkflowCanvas nodes={flow.nodes} edges={flow.edges} minimal />
                                </div>
                            ) : (
                                <p className="py-8 text-center text-sm text-muted-foreground">
                                    This version has no definition.
                                </p>
                            )}
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    )
}
