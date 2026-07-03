/**
 * Campaign template picker + clone flow. Lists the launch-campaign templates and
 * clones the chosen one into a new workflow (via `createWorkflowFromTemplate`, which
 * uses the working create endpoint — findings.md §4), then opens the builder.
 */
import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft, LayoutTemplate, Loader2, Sparkles } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { createWorkflowFromTemplate, listTemplates, type CampaignTemplate } from "@/lib/workflow-api"
import { triggerTypeLabel } from "@/lib/workflow/catalog"
import type { TriggerType } from "@/types/workflow"

export default function WorkflowTemplates() {
    const navigate = useNavigate()
    const [templates, setTemplates] = useState<CampaignTemplate[]>([])
    const [loading, setLoading] = useState(true)
    const [picked, setPicked] = useState<CampaignTemplate | null>(null)
    const [name, setName] = useState("")
    const [creating, setCreating] = useState(false)

    useEffect(() => {
        ;(async () => {
            setLoading(true)
            try {
                setTemplates(await listTemplates())
            } catch {
                toast.error("Failed to load templates")
            } finally {
                setLoading(false)
            }
        })()
    }, [])

    function openPicker(t: CampaignTemplate) {
        setPicked(t)
        setName(t.name)
    }

    async function handleCreate() {
        if (!picked) return
        setCreating(true)
        try {
            const wf = await createWorkflowFromTemplate(picked.id, name)
            toast.success(`Created "${wf.name}"`)
            navigate(`/institution-admin/campaigns/${wf.id}/builder`)
        } catch {
            toast.error("Failed to create campaign from template")
            setCreating(false)
        }
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-32 -right-32 h-[420px] w-[420px] rounded-full bg-transparent blur-[100px] dark:bg-violet-700/20" />
            </div>

            <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" asChild className="h-8 w-8">
                    <Link to="/institution-admin/campaigns">
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <span className="text-sm text-muted-foreground">Campaigns</span>
            </div>

            <div>
                <h2 className="flex items-center gap-2 text-3xl font-bold tracking-tight">
                    <LayoutTemplate className="h-7 w-7" />
                    Start from a template
                </h2>
                <p className="mt-1 text-muted-foreground">
                    Pre-built launch campaigns you can customize in the visual builder.
                </p>
            </div>

            {loading ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-40 w-full" />
                    ))}
                </div>
            ) : templates.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
                    <div className="grid size-12 place-items-center rounded-full bg-muted">
                        <LayoutTemplate className="h-6 w-6 opacity-40" />
                    </div>
                    <p className="text-sm font-medium text-foreground/70">No templates available</p>
                </div>
            ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {templates.map((t) => (
                        <Card key={t.id} className="flex flex-col">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-base font-semibold">{t.name}</CardTitle>
                                <span className="text-xs text-muted-foreground">
                                    {triggerTypeLabel(t.trigger_type as TriggerType)}
                                </span>
                            </CardHeader>
                            <CardContent className="flex flex-1 flex-col gap-3">
                                <p className="flex-1 text-sm text-muted-foreground">{t.description}</p>
                                {t.tags.length > 0 && (
                                    <div className="flex flex-wrap gap-1">
                                        {t.tags.map((tag) => (
                                            <span
                                                key={tag}
                                                className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                                            >
                                                {tag}
                                            </span>
                                        ))}
                                    </div>
                                )}
                                <Button size="sm" className="gap-1.5" onClick={() => openPicker(t)}>
                                    <Sparkles className="h-3.5 w-3.5" /> Use template
                                </Button>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            <Dialog open={picked !== null} onOpenChange={(o) => !o && !creating && setPicked(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Name your campaign</DialogTitle>
                        <DialogDescription>
                            A new workflow will be created from “{picked?.name}”. You can edit everything in the
                            builder next.
                        </DialogDescription>
                    </DialogHeader>
                    <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Campaign name" />
                    <DialogFooter>
                        <Button variant="outline" disabled={creating} onClick={() => setPicked(null)}>
                            Cancel
                        </Button>
                        <Button disabled={creating || !name.trim()} onClick={handleCreate} className="gap-1.5">
                            {creating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                            Create &amp; open builder
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
