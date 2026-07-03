/**
 * Version history / published-snapshot viewer.
 *
 * LIMITATION (findings.md §3/§5): the backend exposes only the CURRENT published
 * version (`current_version_id`) — there is no version-list endpoint. This page shows
 * the current published definition as a read-only canvas + summary. When a version-list
 * endpoint lands, this page can list prior versions and diff them.
 */
import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, History, Info, PencilRuler } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { getWorkflow } from "@/lib/workflow-api"
import { definitionToFlow } from "@/lib/workflow/graph"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import type { AutomationWorkflow } from "@/types"
import type { WorkflowDefinition } from "@/types/workflow"

export default function WorkflowVersions() {
    const { id } = useParams<{ id: string }>()
    const [workflow, setWorkflow] = useState<AutomationWorkflow | null>(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        if (!id) return
        ;(async () => {
            setLoading(true)
            try {
                setWorkflow(await getWorkflow(id))
            } catch {
                toast.error("Failed to load workflow")
            } finally {
                setLoading(false)
            }
        })()
    }, [id])

    const def = (workflow?.definition as WorkflowDefinition | null) ?? null
    const flow = useMemo(() => (def ? definitionToFlow(def) : { nodes: [], edges: [] }), [def])
    const nodeCount = def?.nodes.length ?? 0

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
                        <p className="mt-1 text-muted-foreground">{workflow?.name}</p>
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

            <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                    Showing the current published version. A full version list and visual diff are planned once
                    the backend exposes prior versions.
                </span>
            </div>

            {!loading && workflow && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">
                            Current published version{" "}
                            {workflow.current_version_id && (
                                <span className="ml-1 font-mono text-xs font-normal text-muted-foreground">
                                    {workflow.current_version_id.slice(0, 8)}…
                                </span>
                            )}
                        </CardTitle>
                        <p className="text-xs text-muted-foreground">{nodeCount} step(s)</p>
                    </CardHeader>
                    <CardContent>
                        {def ? (
                            <div className="h-[55vh] w-full overflow-hidden rounded-md border border-border">
                                <WorkflowCanvas nodes={flow.nodes} edges={flow.edges} minimal />
                            </div>
                        ) : (
                            <p className="py-8 text-center text-sm text-muted-foreground">
                                This workflow has no published definition yet.
                            </p>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    )
}
