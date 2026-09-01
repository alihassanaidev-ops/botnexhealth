/**
 * Visual Workflow Builder page — the flagship canvas.
 *
 * Loads a workflow, derives the React Flow graph, and lets an INSTITUTION_ADMIN edit
 * the definition via the palette + typed config panel, see live node-linked validation,
 * dry-run, and publish. The editing buffer is a client-side draft (state + localStorage
 * autosave) because the backend has no draft-with-definition path (findings.md §4);
 * publishing snapshots a new active version.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { Activity, ArrowLeft, History, Loader2, Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import {
    deleteWorkflow,
    getWorkflow,
    listNodeCapabilities,
    pauseWorkflow,
    publishWorkflow,
    resumeWorkflow,
    validateDefinition as validateWorkflowDefinition,
} from "@/lib/workflow-api"
import { listOutboundVoiceProfiles } from "@/lib/outbound-voice-api"
import { listRetellSmsChatProfiles } from "@/lib/retell-sms-api"
import { listAppointmentTypes, listProviders } from "@/lib/tenant-api"
import {
    addNode,
    blankDefinition,
    connectNodes,
    createNode,
    definitionToFlow,
    genId,
    normalizeDefinition,
    removeNode,
    serializeDefinition,
    setEntry,
    setNodePosition,
    TRIGGER_NODE_ID,
    updateNode,
    type FlowNode,
} from "@/lib/workflow/graph"
import { validateDefinition as validateDefinitionLocally } from "@/lib/workflow/validation"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import WorkflowPalette from "@/components/workflow/WorkflowPalette"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import WorkflowValidationPanel from "@/components/workflow/WorkflowValidationPanel"
import WorkflowPublishControls from "@/components/workflow/WorkflowPublishControls"
import WorkflowExecutionsView from "@/components/workflow/WorkflowExecutionsView"
import TestRunDialog from "@/components/workflow/TestRunDialog"
import type { AutomationWorkflow, RetellSmsChatProfile } from "@/types"
import type { OutboundVoiceProfile } from "@/types"
import type { CachedAppointmentType, CachedProvider } from "@/types"
import type {
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
    const navigate = useNavigate()
    const [searchParams, setSearchParams] = useSearchParams()

    const [workflow, setWorkflow] = useState<AutomationWorkflow | null>(null)
    const [def, setDef] = useState<WorkflowDefinition | null>(null)
    const [name, setName] = useState("")
    const [dirty, setDirty] = useState(false)
    const [loading, setLoading] = useState(true)
    const [busy, setBusy] = useState(false)
    const [layouting, setLayouting] = useState(false)
    const [selectedId, setSelectedId] = useState<string | null>(null)
    const [panelOpen, setPanelOpen] = useState(false)
    const [testOpen, setTestOpen] = useState(false)
    const [backendIssues, setBackendIssues] = useState<ValidationIssue[]>([])
    const [backendValidating, setBackendValidating] = useState(false)
    const [supportedNodeTypes, setSupportedNodeTypes] = useState<Set<string> | undefined>()
    const [voiceProfiles, setVoiceProfiles] = useState<OutboundVoiceProfile[]>([])
    // Booking Link and Register Patient are configured with PMS ids. Typing
    // those by hand is how a clinic ends up with a step that fails every run —
    // NexHealth types provider_id as an integer, so free text is refused.
    const [appointmentTypes, setAppointmentTypes] = useState<CachedAppointmentType[]>([])
    const [providers, setProviders] = useState<CachedProvider[]>([])
    const [retellSmsProfiles, setRetellSmsProfiles] = useState<RetellSmsChatProfile[]>([])
    const serverDef = useRef<WorkflowDefinition | null>(null)
    const validationRequest = useRef(0)

    const readOnly = workflow?.status === "archived"
    const view = searchParams.get("view") === "executions" ? "executions" : "build"
    const selectedExecutionRunId = searchParams.get("run")

    const changeView = useCallback((nextView: "build" | "executions") => {
        const nextParams = new URLSearchParams(searchParams)
        if (nextView === "executions") {
            nextParams.set("view", "executions")
        } else {
            nextParams.delete("view")
            nextParams.delete("run")
        }
        setSearchParams(nextParams, { replace: true })
    }, [searchParams, setSearchParams])

    const selectExecutionRun = useCallback((runId: string) => {
        const nextParams = new URLSearchParams(searchParams)
        nextParams.set("view", "executions")
        nextParams.set("run", runId)
        setSearchParams(nextParams, { replace: true })
    }, [searchParams, setSearchParams])

    const load = useCallback(async () => {
        if (!id) return
        setLoading(true)
        try {
            const [wf, capabilities] = await Promise.all([
                getWorkflow(id),
                listNodeCapabilities().catch(() => null),
            ])
            setWorkflow(wf)
            if (capabilities) {
                setSupportedNodeTypes(new Set(
                    capabilities.nodes
                        .filter((node) => node.authorable && node.runtime_supported && node.dry_run_supported)
                        .map((node) => node.node_type),
                ))
            }
            setName(wf.name)
            const base = wf.definition
                ? normalizeDefinition(wf.definition as unknown as WorkflowDefinition)
                : blankDefinition()
            serverDef.current = base
            // Restore a local unsaved draft if present (survives refresh).
            const raw = localStorage.getItem(draftKey(id))
            if (raw) {
                try {
                    setDef(normalizeDefinition(JSON.parse(raw) as WorkflowDefinition))
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
            setVoiceProfiles([])
            setRetellSmsProfiles([])
            setAppointmentTypes([])
            setProviders([])
            return
        }
        let cancelled = false
        setVoiceProfiles([])
        setRetellSmsProfiles([])
        setAppointmentTypes([])
        setProviders([])

        void listOutboundVoiceProfiles({ locationId, isActive: true })
            .then((profiles) => {
                if (!cancelled) setVoiceProfiles(Array.isArray(profiles) ? profiles : [])
            })
            .catch(() => {
                if (!cancelled) setVoiceProfiles([])
            })
        void listRetellSmsChatProfiles({ locationId, isActive: true })
            .then((profiles) => {
                if (!cancelled) setRetellSmsProfiles(Array.isArray(profiles) ? profiles : [])
            })
            .catch(() => {
                if (!cancelled) setRetellSmsProfiles([])
            })
        void listAppointmentTypes(locationId)
            .then((types) => {
                if (!cancelled) setAppointmentTypes(Array.isArray(types) ? types : [])
            })
            .catch(() => {
                // Falls back to typing ids by hand rather than leaving the step
                // unconfigurable when the cache is unavailable.
                if (!cancelled) setAppointmentTypes([])
            })
        void listProviders(locationId)
            .then((rows) => {
                if (!cancelled) setProviders(Array.isArray(rows) ? rows : [])
            })
            .catch(() => {
                if (!cancelled) setProviders([])
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
            validationRequest.current += 1
            setBackendIssues([])
            if (id) localStorage.setItem(draftKey(id), JSON.stringify(next))
        },
        [id],
    )

    const issues = useMemo(() => (def ? validateDefinitionLocally(def) : []), [def])
    const errorCount = [...issues, ...backendIssues].filter((i) => i.severity === "error").length

    useEffect(() => {
        if (!def) return
        const requestId = ++validationRequest.current
        const timer = window.setTimeout(() => {
            setBackendValidating(true)
            void validateWorkflowDefinition(serializeDefinition(def))
                .then((result) => {
                    if (requestId === validationRequest.current) setBackendIssues(result.issues)
                })
                .catch(() => {
                    if (requestId === validationRequest.current) {
                        setBackendIssues([{
                            node_id: null,
                            severity: "warning",
                            message: "Server validation is temporarily unavailable.",
                            code: "server_validation_unavailable",
                            fix: "Try validation again before publishing.",
                        }])
                    }
                })
                .finally(() => {
                    if (requestId === validationRequest.current) setBackendValidating(false)
                })
        }, 350)
        return () => window.clearTimeout(timer)
    }, [def])

    const flow = useMemo(() => {
        if (!def) return { nodes: [] as FlowNode[], edges: [] }
        const f = definitionToFlow(def)
        const level = new Map<string, "error" | "warning">()
        for (const iss of [...issues, ...backendIssues]) {
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
    }, [def, issues, backendIssues])

    const onSelect = useCallback((sel: string | null) => {
        setSelectedId(sel)
        setPanelOpen(sel !== null)
    }, [])

    // Drag-drop from the palette: add the node and pin it to the drop position so it
    // lands under the cursor instead of the auto-layout's trailing column. We do NOT
    // auto-select it — dropping should not pop the config panel open.
    const onAddNodeAt = useCallback(
        (type: NodeType, position: { x: number; y: number }) => {
            if (!def) return
            const newId = genId(type, def.nodes.map((n) => n.id))
            const withNode = addNode(def, createNode(type, newId))
            applyDef(setNodePosition(withNode, newId, position))
        },
        [def, applyDef],
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
    // Compliance classification is currently managed by Retell, not this builder.
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
    const onAutoLayout = useCallback(async () => {
        if (!def) return
        setLayouting(true)
        try {
            const { elkAutoLayoutDefinition } = await import("@/lib/workflow/elk-layout")
            applyDef(await elkAutoLayoutDefinition(def))
        } finally {
            setLayouting(false)
        }
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

    async function onDelete() {
        if (!id) return
        setBusy(true)
        try {
            await deleteWorkflow(id)
            localStorage.removeItem(draftKey(id))
            toast.success("Campaign deleted")
            navigate("/institution-admin/campaigns")
        } catch {
            toast.error("Failed to delete campaign")
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
            const validation = await validateWorkflowDefinition(payload)
            setBackendIssues(validation.issues)
            if (!validation.valid) {
                const validationErrors = validation.issues.filter((issue) => issue.severity === "error")
                toast.error(`Resolve ${validationErrors.length} server validation error${validationErrors.length === 1 ? "" : "s"} before publishing`)
                return
            }
            // Publish through the explicit command endpoint. The backend validates
            // and snapshots this definition as one atomic operation.
            const updated = await publishWorkflow(id, {
                name: name.trim() || workflow?.name,
                definition: payload,
            })
            if (
                updated.id !== id
                || !updated.current_version_id
                || updated.current_version_id === workflow?.current_version_id
            ) {
                throw new Error("The server did not confirm a new published version.")
            }
            setWorkflow(updated)
            serverDef.current = def
            setDirty(false)
            localStorage.removeItem(draftKey(id))
            toast.success("Changes published")
        } catch (err) {
            // Surface the real server reason (e.g. a status conflict or validation
            // detail) instead of a generic "rejected the definition" message.
            const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            const message = detail ?? (err instanceof Error ? err.message : null)
            toast.error(message ? `Couldn't publish: ${message}` : "Failed to publish — please try again")
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
                    <Link to={`/institution-admin/campaigns/${id}`}>
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <Input
                    value={name}
                    disabled={readOnly || view === "executions"}
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

                <div className="ml-3 flex rounded-md border border-border bg-muted/40 p-0.5">
                    <button type="button" onClick={() => changeView("build")} className={cn("inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-xs font-medium", view === "build" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground")}>
                        <Pencil className="h-3.5 w-3.5" /> Build
                    </button>
                    <button type="button" onClick={() => changeView("executions")} className={cn("inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-xs font-medium", view === "executions" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground")}>
                        <Activity className="h-3.5 w-3.5" /> Executions
                    </button>
                </div>

                <div className="ml-auto flex items-center gap-2">
                    <Button variant="outline" size="sm" className="gap-1.5" asChild>
                        <Link to={`/institution-admin/campaigns/${id}/versions`}>
                            <History className="h-3.5 w-3.5" /> Versions
                        </Link>
                    </Button>
                    {view === "build" && <WorkflowPublishControls
                        status={workflow.status}
                        dirty={dirty}
                        errorCount={errorCount}
                        busy={busy}
                        onPublish={onPublish}
                        onDiscard={onDiscard}
                        onPause={() => runLifecycle(pauseWorkflow, "Campaign paused")}
                        onResume={() => runLifecycle(resumeWorkflow, "Campaign resumed")}
                        onDelete={onDelete}
                        onTestRun={() => setTestOpen(true)}
                    />}
                </div>
            </div>

            {/* Body: palette | canvas | validation rail */}
            {view === "executions" ? (
                <WorkflowExecutionsView
                    key={selectedExecutionRunId ?? "latest"}
                    workflowId={id as string}
                    initialRunId={selectedExecutionRunId}
                    onRunSelect={selectExecutionRun}
                />
            ) : <div className="flex min-h-0 flex-1">
                <aside className="w-56 shrink-0 border-r border-border">
                    <WorkflowPalette
                        trigger={def.trigger}
                        onEditTrigger={() => onSelect(TRIGGER_NODE_ID)}
                        disabled={readOnly}
                        supportedNodeTypes={supportedNodeTypes}
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
                        onAutoLayout={onAutoLayout}
                        autoLayoutBusy={layouting}
                        onAddNodeAt={onAddNodeAt}
                    />
                </div>

                <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-l border-border p-3">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Validation</h3>
                    <WorkflowValidationPanel
                        issues={issues}
                        backendIssues={backendIssues}
                        onSelectNode={onSelect}
                    />
                    {(busy || backendValidating) && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                </aside>
            </div>}

            {view === "build" && <StepConfigPanel
                open={panelOpen}
                onOpenChange={setPanelOpen}
                def={def}
                selectedId={selectedId}
                onNodeChange={onNodeChange}
                onDefinitionChange={applyDef}
                onTriggerChange={onTriggerChange}
                onDeleteNode={onDeleteNode}
                onSetEntry={onSetEntry}
                locationId={locationId}
                voiceProfiles={voiceProfiles}
                appointmentTypes={appointmentTypes}
                providers={providers}
                retellSmsProfiles={retellSmsProfiles}
                readOnly={readOnly}
            />}
            <TestRunDialog open={testOpen} onOpenChange={setTestOpen} def={def} />
        </div>
    )
}
