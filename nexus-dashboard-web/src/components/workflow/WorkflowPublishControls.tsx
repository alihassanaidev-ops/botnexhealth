/**
 * Draft/publish/lifecycle controls for the builder.
 *
 * "Publish changes" republishes the edited definition to the live workflow via
 * `PATCH /automation/workflows/{id}` (the backend has no draft-with-definition path —
 * findings.md §4). It is gated behind an explicit confirmation to prevent accidental
 * activation, and disabled while there are validation errors.
 */
import { useState } from "react"
import { Loader2, Pause, Play, Archive, UploadCloud, FlaskConical, RotateCcw } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"

export interface WorkflowPublishControlsProps {
    status: string
    dirty: boolean
    errorCount: number
    busy: boolean
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
