/**
 * Dry-run simulation dialog. Walks the workflow from the entry node WITHOUT
 * dispatching anything (`/enroll` runs for real). Lets the tester flip condition
 * branches to explore paths.
 *
 * The server dry-run is the only source of a result. There was a client-side
 * walker standing in whenever the request failed, which meant an outage looked
 * like a working preview and the broken endpoint went unnoticed — and it knew
 * nothing about real contacts, so it silently discarded the blank-merge-field
 * reporting that is the reason to open this dialog at all. A failure is now
 * shown as a failure.
 *
 * Two preview modes. Sample data renders every merge field from the catalog, which
 * makes every message look finished — useful for reading the copy, useless for
 * spotting a field the clinic cannot actually fill. Picking a real patient resolves
 * the merge fields from that record instead, so the blanks show up as blanks and
 * are listed explicitly.
 */
import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, FlaskConical, RefreshCw, Search, User, X } from "lucide-react"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { NODE_META } from "@/lib/workflow/catalog"
import { dryRun } from "@/lib/workflow-api"
import { listContacts, type ContactListItem } from "@/lib/contacts-api"
import type { TestRunResult, WorkflowDefinition } from "@/types/workflow"

export interface TestRunDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    def: WorkflowDefinition
    /** Supplies the clinic-side merge fields (name, phone, address) for a contact preview. */
    locationId?: string | null
}

function contactLabel(contact: ContactListItem): string {
    return (
        contact.full_name ||
        [contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
        "Unnamed contact"
    )
}

export default function TestRunDialog({
    open,
    onOpenChange,
    def,
    locationId,
}: TestRunDialogProps) {
    const [choices, setChoices] = useState<Record<string, boolean>>({})
    const [result, setResult] = useState<TestRunResult | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [attempt, setAttempt] = useState(0)
    const [contact, setContact] = useState<ContactListItem | null>(null)
    const [search, setSearch] = useState("")
    const [matches, setMatches] = useState<ContactListItem[]>([])
    const [searching, setSearching] = useState(false)

    const conditions = useMemo(() => def.nodes.filter((n) => n.type === "condition"), [def.nodes])

    // Contact search. Debounced so typing a name is not one request per keystroke.
    useEffect(() => {
        if (!open) return
        const term = search.trim()
        if (term.length < 2) {
            setMatches([])
            return
        }
        let cancelled = false
        setSearching(true)
        const timer = setTimeout(() => {
            listContacts({ search: term, limit: 8, locationId: locationId ?? undefined })
                .then((page) => {
                    if (!cancelled) setMatches(page.items)
                })
                .catch(() => {
                    if (!cancelled) setMatches([])
                })
                .finally(() => {
                    if (!cancelled) setSearching(false)
                })
        }, 250)
        return () => {
            cancelled = true
            clearTimeout(timer)
        }
    }, [locationId, open, search])

    useEffect(() => {
        if (!open) return
        let cancelled = false
        setError(null)
        dryRun(def, {
            conditionChoices: choices,
            contactId: contact?.id,
            locationId: locationId ?? undefined,
        })
            .then((r) => {
                if (cancelled) return
                setResult(r)
            })
            .catch(() => {
                if (cancelled) return
                setResult(null)
                setError("The simulation could not be run. Nothing was sent.")
            })
        return () => {
            cancelled = true
        }
    }, [open, def, choices, contact, locationId, attempt])

    const emptyFields = result?.empty_fields ?? []
    const previewingContact = result?.context_source === "contact"

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <FlaskConical className="h-4 w-4" /> Test run (simulation)
                    </DialogTitle>
                    <DialogDescription>
                        {previewingContact
                            ? `Simulated against ${result?.contact_name ?? "the selected patient"}'s real record. Sends nothing.`
                            : "Simulates the path a sample contact would take. Uses sample merge data and sends nothing."}
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-2 rounded-md border border-border p-3">
                    <p className="text-xs font-medium text-muted-foreground">Preview with</p>
                    {contact ? (
                        <div className="flex items-center justify-between gap-2 rounded-md bg-muted/60 px-2.5 py-1.5">
                            <span className="flex min-w-0 items-center gap-2 text-sm">
                                <User className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                <span className="truncate font-medium">{contactLabel(contact)}</span>
                            </span>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 shrink-0 px-1.5 text-xs"
                                onClick={() => {
                                    setContact(null)
                                    setSearch("")
                                }}
                            >
                                <X className="h-3.5 w-3.5" />
                                <span className="sr-only">Use sample data instead</span>
                            </Button>
                        </div>
                    ) : (
                        <>
                            <div className="relative">
                                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                                <Input
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                    placeholder="Search a real patient by name…"
                                    className="h-8 pl-8 text-sm"
                                />
                            </div>
                            {search.trim().length >= 2 && (
                                <div className="max-h-40 overflow-y-auto rounded-md border border-border">
                                    {searching && matches.length === 0 ? (
                                        <p className="px-2.5 py-2 text-xs text-muted-foreground">Searching…</p>
                                    ) : matches.length === 0 ? (
                                        <p className="px-2.5 py-2 text-xs text-muted-foreground">
                                            No matching patients.
                                        </p>
                                    ) : (
                                        matches.map((item) => (
                                            <button
                                                key={item.id}
                                                type="button"
                                                onClick={() => setContact(item)}
                                                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-muted"
                                            >
                                                <User className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                                <span className="truncate">{contactLabel(item)}</span>
                                            </button>
                                        ))
                                    )}
                                </div>
                            )}
                            <p className="text-[11px] text-muted-foreground">
                                Sample data fills every field, so messages always look complete. Pick a
                                real patient to see what would actually send.
                            </p>
                        </>
                    )}
                </div>

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

                {emptyFields.length > 0 && (
                    <div className="space-y-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300">
                        <div className="flex items-center gap-2 font-medium">
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                            {emptyFields.length} merge field{emptyFields.length === 1 ? "" : "s"} rendered
                            blank for this patient
                        </div>
                        <ul className="space-y-0.5 pl-5">
                            {emptyFields.map((f) => (
                                <li key={f.name}>
                                    <code className="font-mono">{`{{${f.name}}}`}</code>
                                    <span className="text-amber-700 dark:text-amber-400">
                                        {" "}
                                        — used in {f.nodes.join(", ")}
                                    </span>
                                </li>
                            ))}
                        </ul>
                        <p className="text-amber-700 dark:text-amber-400">
                            These send as empty text. Reword the message or pick a trigger that carries
                            this data.
                        </p>
                    </div>
                )}

                {error && (
                    <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2.5">
                        <div className="flex items-center gap-2 text-sm font-medium text-destructive">
                            <AlertTriangle className="h-4 w-4 shrink-0" />
                            {error}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            No preview is shown rather than an approximate one, so this cannot be
                            mistaken for a working workflow.
                        </p>
                        <Button
                            variant="outline"
                            size="sm"
                            className="h-7 text-xs"
                            onClick={() => setAttempt((n) => n + 1)}
                        >
                            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                            Try again
                        </Button>
                    </div>
                )}

                {!result && !error && (
                    <p role="status" className="py-6 text-center text-sm text-muted-foreground">
                        Running simulation…
                    </p>
                )}

                <ol className="space-y-2">
                    {(result?.steps ?? []).map((step, i) => {
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

                {result?.truncated && (
                    <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        Simulation stopped after 50 steps — the workflow may contain a loop.
                    </div>
                )}

                {result && (
                    <div className="rounded-md bg-muted/50 px-3 py-2 text-sm">
                        <span className="text-muted-foreground">Final outcome: </span>
                        <span className="font-medium">{result.outcome ?? "— (no exit reached)"}</span>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    )
}
