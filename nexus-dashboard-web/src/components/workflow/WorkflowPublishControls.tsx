/**
 * Draft/publish/lifecycle controls for the builder.
 *
 * "Publish changes" republishes the edited definition to the live workflow via
 * `PATCH /automation/workflows/{id}` (the backend has no draft-with-definition path —
 * findings.md §4). It is gated behind an explicit confirmation to prevent accidental
 * activation, and disabled while there are validation errors.
 */
import { useState } from "react"
import { Loader2, Pause, Play, Archive, UploadCloud, FlaskConical, RotateCcw, AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import LaunchChecklistPanel from "@/components/workflow/LaunchChecklistPanel"
import type { LaunchChecklist } from "@/types/workflow"

export interface WorkflowPublishControlsProps {
    status: string
    dirty: boolean
    errorCount: number
    busy: boolean
    /**
     * Advisory notice shown in the publish-confirm dialog when the workflow uses a
     * channel that isn't set up for its location (Plan 02 B6). Does NOT block publish.
     */
    readinessWarning?: string | null
    launchChecklist?: LaunchChecklist | null
    launchChecklistLoading?: boolean
    onPublish: () => void
    onDiscard: () => void
    onPause: () => void
    onResume: () => void
    onArchive: () => void
    onTestRun: () => void
}

export default function WorkflowPublishControls(props: WorkflowPublishControlsProps) {
    const { status, dirty, errorCount, busy } = props
    const [confirmPublish, setConfirmPublish] = useState(false)
    const [confirmArchive, setConfirmArchive] = useState(false)
    const canPublish = dirty && errorCount === 0 && !busy

    return (
        <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={props.onTestRun}>
                <FlaskConical className="h-3.5 w-3.5" /> Test run
            </Button>

            {dirty && (
                <Button variant="ghost" size="sm" className="gap-1.5" disabled={busy} onClick={props.onDiscard}>
                    <RotateCcw className="h-3.5 w-3.5" /> Discard
                </Button>
            )}

            {status === "active" && (
                <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={props.onPause}>
                    <Pause className="h-3.5 w-3.5" /> Pause
                </Button>
            )}
            {status === "paused" && (
                <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={props.onResume}>
                    <Play className="h-3.5 w-3.5" /> Resume
                </Button>
            )}
            {status !== "archived" && (
                <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={() => setConfirmArchive(true)}>
                    <Archive className="h-3.5 w-3.5" /> Archive
                </Button>
            )}

            <Button
                size="sm"
                className="gap-1.5"
                disabled={!canPublish}
                title={errorCount > 0 ? "Resolve validation errors first" : undefined}
                onClick={() => setConfirmPublish(true)}
            >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
                Publish changes
            </Button>

            <Dialog open={confirmPublish} onOpenChange={setConfirmPublish}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Publish changes?</DialogTitle>
                        <DialogDescription>
                            This publishes a new active version. New enrollments use it immediately; contacts
                            already in a sequence continue on the version they enrolled under.
                        </DialogDescription>
                    </DialogHeader>
                    {props.readinessWarning && (
                        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>{props.readinessWarning}</span>
                        </div>
                    )}
                    <LaunchChecklistPanel
                        checklist={props.launchChecklist ?? null}
                        loading={props.launchChecklistLoading}
                        compact
                    />
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setConfirmPublish(false)}>Cancel</Button>
                        <Button
                            onClick={() => {
                                setConfirmPublish(false)
                                props.onPublish()
                            }}
                        >
                            Publish
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={confirmArchive} onOpenChange={setConfirmArchive}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Archive this campaign?</DialogTitle>
                        <DialogDescription>
                            It will stop accepting new enrollments. In-flight runs are unaffected.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setConfirmArchive(false)}>Cancel</Button>
                        <Button
                            variant="destructive"
                            onClick={() => {
                                setConfirmArchive(false)
                                props.onArchive()
                            }}
                        >
                            Archive
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
