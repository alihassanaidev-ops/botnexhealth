/**
 * Visual Workflow Builder page — the flagship canvas.
 *
 * Loads a workflow, derives the React Flow graph, and lets an INSTITUTION_ADMIN edit
 * the definition via the palette + typed config panel, see live node-linked validation,
 * dry-run, and publish. The editing buffer is a client-side draft (state + localStorage
 * autosave) because the backend has no draft-with-definition path (findings.md §4);
 * publishing PATCHes a new active version.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, History, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import {
    archiveWorkflow,
    getChannelReadiness,
    getWorkflow,
    pauseWorkflow,
    resumeWorkflow,
    updateWorkflow,
    validateDefinition as validateDefinitionOnServer,
} from "@/lib/workflow-api"
import {
    addNode,
    blankDefinition,
    clearLayout,
    connectNodes,
    createNode,
    definitionToFlow,
    genId,
    removeNode,
    serializeDefinition,
    setEntry,
    setNodePosition,
    TRIGGER_NODE_ID,
    updateNode,
    type FlowNode,
} from "@/lib/workflow/graph"
import { validateDefinition } from "@/lib/workflow/validation"
import { usedChannelStatuses } from "@/lib/workflow/readiness"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import WorkflowPalette from "@/components/workflow/WorkflowPalette"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import WorkflowValidationPanel from "@/components/workflow/WorkflowValidationPanel"
import WorkflowPublishControls from "@/components/workflow/WorkflowPublishControls"
import ComplianceSettings from "@/components/workflow/ComplianceSettings"
import TestRunDialog from "@/components/workflow/TestRunDialog"
import type { AutomationWorkflow } from "@/types"
import type {
    ChannelReadiness,
    ComplianceMetadata,
    NodeType,
    ValidationIssue,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowTrigger,
} from "@/types/workflow"

const STATUS_STYLES: Record<string, string> = {
    active: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800",
    paused: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800",
    archived: "bg-zinc-100 text-zinc-500 border-zinc-200 dark:bg-zinc-800/60 dark:text-zinc-400 dark:border-zinc-700",
    draft: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-800",
}

const draftKey = (id: string) => `nex.workflow-draft.${id}`

export default function WorkflowBuilder() {
    const { id } = useParams<{ id: string }>()

    const [workflow, setWorkflow] = useState<AutomationWorkflow | null>(null)
    const [def, setDef] = useState<WorkflowDefinition | null>(null)
    const [name, setName] = useState("")
    const [dirty, setDirty] = useState(false)
    const [loading, setLoading] = useState(true)
    const [busy, setBusy] = useState(false)
    const [selectedId, setSelectedId] = useState<string | null>(null)
    const [panelOpen, setPanelOpen] = useState(false)
    const [testOpen, setTestOpen] = useState(false)
    const [backendIssues, setBackendIssues] = useState<ValidationIssue[]>([])
    const [readiness, setReadiness] = useState<ChannelReadiness | null>(null)
    const serverDef = useRef<WorkflowDefinition | null>(null)

    const readOnly = workflow?.status === "archived"

    const load = useCallback(async () => {
        if (!id) return
        setLoading(true)
        try {
            const wf = await getWorkflow(id)
            setWorkflow(wf)
            setName(wf.name)
            const base = (wf.definition as WorkflowDefinition | null) ?? blankDefinition()
            serverDef.current = base
            // Restore a local unsaved draft if present (survives refresh).
            const raw = localStorage.getItem(draftKey(id))
            if (raw) {
                try {
                    setDef(JSON.parse(raw) as WorkflowDefinition)
                    setDirty(true)
                    toast.info("Restored unsaved changes from this browser.")
                } catch {
                    setDef(base)
                }
            } else {
                setDef(base)
                setDirty(false)
            }
        } catch {
            toast.error("Failed to load workflow")
            setWorkflow(null)
        } finally {
            setLoading(false)
        }
    }, [id])

    useEffect(() => {
        void load()
    }, [load])

    // Channel readiness (Plan 02 B6): only location-scoped workflows have channels
    // to verify; institution-level / no-location workflows have nothing to check.
    const locationId = workflow?.location_id ?? null
    useEffect(() => {
        if (!locationId) {
            setReadiness(null)
            return
        }
        let cancelled = false
        getChannelReadiness(locationId)
            .then((r) => {
                if (!cancelled) setReadiness(r)
            })
            .catch(() => {
                // Advisory only — a failed lookup silently omits the indicator.
                if (!cancelled) setReadiness(null)
            })
        return () => {
            cancelled = true
        }
    }, [locationId])

    // ---- editing buffer ----
    const applyDef = useCallback(
        (next: WorkflowDefinition) => {
            setDef(next)
            setDirty(true)
            // Stale server issues no longer describe the edited definition.
            setBackendIssues([])
            if (id) localStorage.setItem(draftKey(id), JSON.stringify(next))
        },
        [id],
    )

    const issues = useMemo(() => (def ? validateDefinition(def) : []), [def])
    const errorCount = issues.filter((i) => i.severity === "error").length

    // Readiness of the channels this definition actually uses (empty when no
    // location or no readiness report yet). Unready channels warn but never block.
    const channelStatuses = useMemo(
        () => (def && readiness ? usedChannelStatuses(def, readiness) : []),
        [def, readiness],
    )
    const readinessWarning = useMemo(() => {
        const unready = channelStatuses.filter((s) => !s.ready)
        if (unready.length === 0) return null
        const names = unready.map((s) => s.label).join(", ")
        return `${names} ${unready.length > 1 ? "are" : "is"} not set up for this location. You can still publish, but those steps won't send until it's configured.`
    }, [channelStatuses])

    const flow = useMemo(() => {
        if (!def) return { nodes: [] as FlowNode[], edges: [] }
        const f = definitionToFlow(def)
        const level = new Map<string, "error" | "warning">()
        for (const iss of issues) {
            if (!iss.node_id) continue
            if (iss.severity === "error" || level.get(iss.node_id) !== "error") {
                level.set(iss.node_id, iss.severity === "error" ? "error" : level.get(iss.node_id) ?? "warning")
            }
        }
        const nodes: FlowNode[] = f.nodes.map((n) => ({
            ...n,
            data: { ...n.data, issueLevel: level.get(n.id) ?? null },
        }))
        return { nodes, edges: f.edges }
    }, [def, issues])

    const onSelect = useCallback((sel: string | null) => {
        setSelectedId(sel)
        setPanelOpen(sel !== null)
    }, [])

    const onAddNode = useCallback(
        (type: NodeType) => {
            if (!def) return
            const newId = genId(type, def.nodes.map((n) => n.id))
            applyDef(addNode(def, createNode(type, newId)))
            onSelect(newId)
        },
        [def, applyDef, onSelect],
    )

    const onNodeChange = useCallback(
        (node: WorkflowNode) => {
            if (def) applyDef(updateNode(def, node.id, node))
        },
        [def, applyDef],
    )
    const onTriggerChange = useCallback(
        (trigger: WorkflowTrigger) => {
            if (def) applyDef({ ...def, trigger })
        },
        [def, applyDef],
    )
    const onComplianceChange = useCallback(
        (compliance: ComplianceMetadata) => {
            if (def) applyDef({ ...def, compliance })
        },
        [def, applyDef],
    )
    const onDeleteNode = useCallback(
        (nodeId: string) => {
            if (!def) return
            applyDef(removeNode(def, nodeId))
            setPanelOpen(false)
            setSelectedId(null)
        },
        [def, applyDef],
    )
    const onSetEntry = useCallback(
        (nodeId: string) => {
            if (def) applyDef(setEntry(def, nodeId))
        },
        [def, applyDef],
    )

    // ---- canvas: drag-to-connect + presentational layout (never alters semantics) ----
    const onConnectNodes = useCallback(
        (sourceId: string, targetId: string, handle?: "true" | "false") => {
            if (def) applyDef(connectNodes(def, sourceId, targetId, handle))
        },
        [def, applyDef],
    )
    const onNodePositionChange = useCallback(
        (nodeId: string, position: { x: number; y: number }) => {
            if (def) applyDef(setNodePosition(def, nodeId, position))
        },
        [def, applyDef],
    )
    const onTidyLayout = useCallback(() => {
        if (def) applyDef(clearLayout(def))
    }, [def, applyDef])

    const onDiscard = useCallback(() => {
        if (!id || !serverDef.current) return
        setDef(serverDef.current)
        setName(workflow?.name ?? "")
        setDirty(false)
        localStorage.removeItem(draftKey(id))
        setPanelOpen(false)
        setSelectedId(null)
        toast.success("Reverted to the last published version")
    }, [id, workflow])

    async function runLifecycle(
        action: (wid: string) => Promise<AutomationWorkflow>,
        okMsg: string,
    ) {
        if (!id) return
        setBusy(true)
        try {
            setWorkflow(await action(id))
            toast.success(okMsg)
        } catch {
            toast.error("Action failed")
        } finally {
            setBusy(false)
        }
    }

    async function onPublish() {
        if (!id || !def) return
        // Fast client-side gate first.
        if (errorCount > 0) {
            toast.error(`Resolve ${errorCount} validation error${errorCount > 1 ? "s" : ""} before publishing`)
            return
        }
        const payload = serializeDefinition(def)
        setBusy(true)
        try {
            // Authoritative backend validation (consent/content-class + schema).
            const result = await validateDefinitionOnServer(payload)
            setBackendIssues(result.issues)
            const serverErrors = result.issues.filter((i) => i.severity === "error")
            if (serverErrors.length > 0) {
                toast.error(
                    `Resolve ${serverErrors.length} server validation error${serverErrors.length > 1 ? "s" : ""} before publishing`,
                )
                return
            }
            const updated = await updateWorkflow(id, {
                name: name.trim() || workflow?.name,
                definition: payload,
            })
            setWorkflow(updated)
            serverDef.current = def
            setDirty(false)
            localStorage.removeItem(draftKey(id))
            toast.success("Changes published")
        } catch {
            toast.error("Failed to publish — the server rejected the definition")
        } finally {
            setBusy(false)
        }
    }

    if (loading) {
        return (
            <div className="space-y-4 p-8">
                <Skeleton className="h-9 w-72" />
                <Skeleton className="h-[60vh] w-full" />
            </div>
        )
    }

    if (!workflow || !def) {
        return (
            <div className="p-8">
                <Link to="/institution-admin/campaigns" className="text-sm text-muted-foreground hover:underline">
                    ← Back to campaigns
                </Link>
                <p className="mt-6 text-sm text-muted-foreground">Workflow not found.</p>
            </div>
        )
    }

    return (
        <div className="flex h-[calc(100vh-4rem)] flex-col overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-3 border-b border-border px-4 py-2.5">
                <Button variant="ghost" size="icon" asChild className="h-8 w-8">
                    <Link to={`/institution-admin/campaigns/${workflow.id}`}>
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <Input
                    value={name}
                    disabled={readOnly}
                    onChange={(e) => {
                        setName(e.target.value)
                        setDirty(true)
                    }}
                    className="h-8 w-72 border-transparent text-base font-semibold hover:border-border focus-visible:border-input"
                />
                <span
                    className={cn(
                        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize",
                        STATUS_STYLES[workflow.status] ?? STATUS_STYLES.draft,
                    )}
                >
                    {workflow.status}
                </span>
                {dirty && <span className="text-xs text-amber-600 dark:text-amber-400">● Unsaved</span>}

                <div className="ml-auto flex items-center gap-2">
                    <Button variant="outline" size="sm" className="gap-1.5" asChild>
                        <Link to={`/institution-admin/campaigns/${workflow.id}/versions`}>
                            <History className="h-3.5 w-3.5" /> Versions
                        </Link>
                    </Button>
                    <WorkflowPublishControls
                        status={workflow.status}
                        dirty={dirty}
                        errorCount={errorCount}
                        busy={busy}
                        readinessWarning={readinessWarning}
                        onPublish={onPublish}
                        onDiscard={onDiscard}
                        onPause={() => runLifecycle(pauseWorkflow, "Campaign paused")}
                        onResume={() => runLifecycle(resumeWorkflow, "Campaign resumed")}
                        onArchive={() => runLifecycle(archiveWorkflow, "Campaign archived")}
                        onTestRun={() => setTestOpen(true)}
                    />
                </div>
            </div>

            {/* Body: palette | canvas | validation rail */}
            <div className="flex min-h-0 flex-1">
                <aside className="w-56 shrink-0 border-r border-border">
                    <WorkflowPalette
                        trigger={def.trigger}
                        onAddNode={onAddNode}
                        onEditTrigger={() => onSelect(TRIGGER_NODE_ID)}
                        disabled={readOnly}
                    />
                </aside>

                <div className="relative min-h-0 flex-1">
                    <WorkflowCanvas
                        nodes={flow.nodes}
                        edges={flow.edges}
                        selectedId={selectedId}
                        onSelect={onSelect}
                        editable={!readOnly}
                        onConnectNodes={onConnectNodes}
                        onNodePositionChange={onNodePositionChange}
                        onTidyLayout={onTidyLayout}
                    />
                </div>

                <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-l border-border p-3">
                    <ComplianceSettings
                        compliance={def.compliance}
                        onChange={onComplianceChange}
                        disabled={readOnly}
                    />
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Validation</h3>
                    <WorkflowValidationPanel
                        issues={issues}
                        backendIssues={backendIssues}
                        readiness={channelStatuses}
                        onSelectNode={onSelect}
                    />
                    {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                </aside>
            </div>

            <StepConfigPanel
                open={panelOpen}
                onOpenChange={setPanelOpen}
                def={def}
                selectedId={selectedId}
                onNodeChange={onNodeChange}
                onTriggerChange={onTriggerChange}
                onDeleteNode={onDeleteNode}
                onSetEntry={onSetEntry}
                readOnly={readOnly}
            />
            <TestRunDialog open={testOpen} onOpenChange={setTestOpen} def={def} />
        </div>
    )
}
