/**
 * Staff notes on a call — a message-style thread rendered under the triage
 * details in the call detail panel.
 *
 * Scoping is the server's job: `GET /institution/calls/{id}/notes` only ever
 * returns notes on calls the caller can already open, so every user in an
 * institution sees the same thread on a call they share, and nobody sees a
 * thread on a call outside their scope. `can_edit` / `can_delete` come back
 * resolved per-caller — do not re-derive them from the role here.
 *
 * The same thread renders in two places: inline (capped height, in the rail)
 * and in a centered dialog for reading a long thread. Both share `NotesThread`
 * and one `useCallNotes` state hook so they can never drift.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
    Loader2,
    Maximize2,
    MessageSquareText,
    Pencil,
    Send,
    Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { formatAbsoluteTime, formatRelativeTime } from "@/components/calls/format"
import {
    createCallNote,
    deleteCallNote,
    listCallNotes,
    updateCallNote,
} from "@/lib/calls-api"
import { cn } from "@/lib/utils"
import type { CallNote } from "@/types"

/** Mirrors MAX_NOTE_LENGTH in src/app/models/call_note.py — keep in step. */
const MAX_NOTE_LENGTH = 4000

/** Show the counter only once the body is close enough to matter. */
const COUNTER_VISIBLE_FROM = MAX_NOTE_LENGTH - 500

function errorMessage(e: unknown, fallback: string): string {
    return e instanceof Error ? e.message : fallback
}

/** The part of an email before "@" — the dense-rail label. Full address is
 *  kept in the title attribute so the domain is never actually lost. */
function shortEmail(email: string): string {
    const at = email.indexOf("@")
    return at > 0 ? email.slice(0, at) : email
}

function emailInitials(email: string): string {
    const local = shortEmail(email)
    const parts = local.split(/[._-]+/).filter(Boolean)
    if (parts.length === 0) return "?"
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
    return (parts[0][0] + parts[1][0]).toUpperCase()
}

// ── State ─────────────────────────────────────────────────────────────────────

/** One thread's loaded state, tagged with the call it belongs to. Storing the
 *  id alongside the items lets `loading` be *derived* rather than reset in an
 *  effect, so switching calls can't briefly show the previous thread. */
type LoadedThread = { callId: string; items: CallNote[] }

/** One thread's data + mutations. Owned by the section so the inline view and
 *  the dialog render the same list and a post from either lands in both. */
function useCallNotes(callId: string) {
    const [thread, setThread] = useState<LoadedThread | null>(null)
    // Guards against a slow response for a previous call landing after the
    // user moved on — same request-token pattern as the call detail fetch.
    const reqRef = useRef(0)

    const isCurrent = thread?.callId === callId
    const notes = isCurrent ? thread.items : []
    const loading = !isCurrent

    useEffect(() => {
        const token = ++reqRef.current
        listCallNotes(callId)
            .then((r) => {
                if (reqRef.current === token) setThread({ callId, items: r.items })
            })
            .catch((e) => {
                if (reqRef.current !== token) return
                // Settle on an empty thread so the panel leaves its loading
                // state; the toast is what tells the user it didn't load.
                setThread({ callId, items: [] })
                toast.error(errorMessage(e, "Failed to load notes"))
            })
    }, [callId])

    /** Apply a change to the loaded thread, ignoring it if the selection moved
     *  on mid-request so a stale response can't resurrect another call's list. */
    const patch = useCallback(
        (fn: (items: CallNote[]) => CallNote[]) => {
            setThread((prev) =>
                prev && prev.callId === callId ? { callId, items: fn(prev.items) } : prev,
            )
        },
        [callId],
    )

    const add = useCallback(
        async (body: string) => {
            const created = await createCallNote(callId, body)
            patch((items) => [...items, created])
        },
        [callId, patch],
    )

    const edit = useCallback(
        async (noteId: string, body: string) => {
            const updated = await updateCallNote(callId, noteId, body)
            patch((items) => items.map((n) => (n.id === noteId ? updated : n)))
        },
        [callId, patch],
    )

    const remove = useCallback(
        async (noteId: string) => {
            await deleteCallNote(callId, noteId)
            patch((items) => items.filter((n) => n.id !== noteId))
        },
        [callId, patch],
    )

    return { notes, loading, add, edit, remove }
}

// ── Pieces ────────────────────────────────────────────────────────────────────

function NoteRow({
    note,
    onEdit,
    onDelete,
}: {
    note: CallNote
    onEdit: (noteId: string, body: string) => Promise<void>
    onDelete: (noteId: string) => Promise<void>
}) {
    const [editing, setEditing] = useState(false)
    const [draft, setDraft] = useState(note.body)
    const [busy, setBusy] = useState(false)

    async function save() {
        const trimmed = draft.trim()
        if (!trimmed || trimmed === note.body) {
            setEditing(false)
            setDraft(note.body)
            return
        }
        setBusy(true)
        try {
            await onEdit(note.id, trimmed)
            setEditing(false)
        } catch (e) {
            toast.error(errorMessage(e, "Failed to save note"))
        } finally {
            setBusy(false)
        }
    }

    async function remove() {
        if (!window.confirm("Delete this note? Everyone in your team will stop seeing it.")) return
        setBusy(true)
        try {
            await onDelete(note.id)
            toast.success("Note deleted")
        } catch (e) {
            toast.error(errorMessage(e, "Failed to delete note"))
            setBusy(false)
        }
    }

    return (
        <div className="group flex gap-2.5">
            <div
                className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary"
                aria-hidden
            >
                {emailInitials(note.author_email)}
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                    <span
                        className="truncate text-xs font-medium text-foreground"
                        title={note.author_email}
                    >
                        {note.author_email}
                    </span>
                    <span
                        className="shrink-0 text-[11px] text-muted-foreground"
                        title={formatAbsoluteTime(note.created_at)}
                    >
                        {formatRelativeTime(note.created_at)}
                    </span>
                    {note.edited_at && (
                        <span
                            className="shrink-0 text-[11px] italic text-muted-foreground/70"
                            title={`Edited ${formatAbsoluteTime(note.edited_at)}`}
                        >
                            edited
                        </span>
                    )}
                    {(note.can_edit || note.can_delete) && !editing && (
                        // Revealed on hover to keep the dense rail quiet, but
                        // focus-within keeps them reachable by keyboard.
                        <span className="ml-auto flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                            {note.can_edit && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                    onClick={() => {
                                        setDraft(note.body)
                                        setEditing(true)
                                    }}
                                    aria-label="Edit note"
                                >
                                    <Pencil className="h-3 w-3" />
                                </Button>
                            )}
                            {note.can_delete && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6 text-muted-foreground hover:text-destructive"
                                    onClick={remove}
                                    disabled={busy}
                                    aria-label="Delete note"
                                >
                                    <Trash2 className="h-3 w-3" />
                                </Button>
                            )}
                        </span>
                    )}
                </div>

                {editing ? (
                    <div className="mt-1.5 space-y-1.5">
                        <Textarea
                            value={draft}
                            onChange={(e) => setDraft(e.target.value.slice(0, MAX_NOTE_LENGTH))}
                            className="min-h-[64px] text-xs"
                            autoFocus
                        />
                        <div className="flex items-center gap-1.5">
                            <Button
                                type="button"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={save}
                                disabled={busy || !draft.trim()}
                            >
                                {busy && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                                Save
                            </Button>
                            <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                className="h-7 text-xs"
                                onClick={() => {
                                    setEditing(false)
                                    setDraft(note.body)
                                }}
                                disabled={busy}
                            >
                                Cancel
                            </Button>
                        </div>
                    </div>
                ) : (
                    <p className="mt-0.5 whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground/90">
                        {note.body}
                    </p>
                )}
            </div>
        </div>
    )
}

function NoteComposer({
    onSubmit,
    autoFocus = false,
}: {
    onSubmit: (body: string) => Promise<void>
    autoFocus?: boolean
}) {
    const [draft, setDraft] = useState("")
    const [busy, setBusy] = useState(false)

    async function submit() {
        const trimmed = draft.trim()
        if (!trimmed || busy) return
        setBusy(true)
        try {
            await onSubmit(trimmed)
            setDraft("")
        } catch (e) {
            // Keep the draft on failure — retyping a lost note is the worst
            // possible outcome here.
            toast.error(errorMessage(e, "Failed to add note"))
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="space-y-1.5">
            <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value.slice(0, MAX_NOTE_LENGTH))}
                onKeyDown={(e) => {
                    // Enter posts; Shift+Enter is a newline — the thread reads
                    // as messages, so posting should be the one-key path.
                    if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault()
                        void submit()
                    }
                }}
                placeholder="Add a note for your team…"
                className="min-h-[60px] resize-none text-xs"
                disabled={busy}
                autoFocus={autoFocus}
                aria-label="Add a note"
            />
            <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-muted-foreground">
                    {draft.length >= COUNTER_VISIBLE_FROM
                        ? `${draft.length} / ${MAX_NOTE_LENGTH}`
                        : "Enter to post · Shift+Enter for a new line"}
                </span>
                <Button
                    type="button"
                    size="sm"
                    className="h-7 gap-1.5 text-xs"
                    onClick={submit}
                    disabled={busy || !draft.trim()}
                >
                    {busy ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                        <Send className="h-3 w-3" />
                    )}
                    Post
                </Button>
            </div>
        </div>
    )
}

function NotesThread({
    notes,
    loading,
    onEdit,
    onDelete,
    className,
}: {
    notes: CallNote[]
    loading: boolean
    onEdit: (noteId: string, body: string) => Promise<void>
    onDelete: (noteId: string) => Promise<void>
    className?: string
}) {
    if (loading) {
        return (
            <div className={cn("flex items-center gap-2 py-3 text-xs text-muted-foreground", className)}>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading notes…
            </div>
        )
    }

    if (notes.length === 0) {
        return (
            <div className={cn("py-3 text-center", className)}>
                <p className="text-xs text-muted-foreground">No notes yet.</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground/70">
                    Anything you add here is visible to your whole team.
                </p>
            </div>
        )
    }

    return (
        <div className={cn("space-y-3.5", className)}>
            {notes.map((n) => (
                <NoteRow key={n.id} note={n} onEdit={onEdit} onDelete={onDelete} />
            ))}
        </div>
    )
}

// ── Section ───────────────────────────────────────────────────────────────────

/** Notes panel for one call. Renders inline; the expand button opens the same
 *  thread in a centered dialog for long threads. */
export function CallNotesSection({ callId }: { callId: string }) {
    const { notes, loading, add, edit, remove } = useCallNotes(callId)
    // Which call the dialog was opened for, not a plain boolean: selecting a
    // different call implicitly closes it, so one call's thread can never
    // linger over another call's details.
    const [expandedFor, setExpandedFor] = useState<string | null>(null)
    const expanded = expandedFor === callId

    return (
        <div className="space-y-2 rounded-lg border bg-muted p-3">
            <div className="flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    <MessageSquareText className="h-3.5 w-3.5" />
                    Notes
                    {!loading && notes.length > 0 && (
                        <span className="rounded-full bg-primary/10 px-1.5 text-[10px] font-semibold text-primary">
                            {notes.length}
                        </span>
                    )}
                </p>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-muted-foreground hover:text-foreground"
                    onClick={() => setExpandedFor(callId)}
                    aria-label="Open notes in a larger view"
                    title="Open notes in a larger view"
                >
                    <Maximize2 className="h-3.5 w-3.5" />
                </Button>
            </div>

            {/* Capped so a long thread never pushes the rest of the rail out of
                reach — the dialog is the place to read all of it. */}
            <div className="max-h-56 overflow-y-auto pr-1">
                <NotesThread notes={notes} loading={loading} onEdit={edit} onDelete={remove} />
            </div>

            <NoteComposer onSubmit={add} />

            <Dialog open={expanded} onOpenChange={(o) => setExpandedFor(o ? callId : null)}>
                <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 p-0">
                    {/* pr-12 keeps the header clear of the dialog's absolutely-positioned
                        close button, which p-0 on the content no longer insets. */}
                    <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12">
                        <DialogTitle className="flex items-center gap-2 text-base">
                            <MessageSquareText className="h-4 w-4" />
                            Notes
                            {notes.length > 0 && (
                                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
                                    {notes.length}
                                </span>
                            )}
                        </DialogTitle>
                        <DialogDescription className="text-xs">
                            Notes on this call, visible to everyone on your team who can see it.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                        <NotesThread
                            notes={notes}
                            loading={loading}
                            onEdit={edit}
                            onDelete={remove}
                        />
                    </div>

                    <div className="shrink-0 border-t bg-muted/40 px-5 py-3">
                        <NoteComposer onSubmit={add} autoFocus />
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
