/**
 * Undo/redo for the workflow definition.
 *
 * The builder mutates the definition through a single `applyDef` call, so the
 * history can wrap that one seam rather than being threaded through every
 * editor. On a forty-node graph — which the templates really are — editing
 * without undo means every mistake is repaired by hand.
 *
 * Entries are whole definitions rather than diffs. A definition is a few KB of
 * JSON, and storing snapshots keeps undo exact: replaying inverse operations
 * would have to know how every node type mutates, which is precisely the kind
 * of duplicated knowledge that goes stale when a node is added.
 */
import { useCallback, useRef, useState } from "react"
import type { WorkflowDefinition } from "@/types/workflow"

/** Bounded so a long editing session cannot grow without limit. */
const MAX_HISTORY = 50

/**
 * Consecutive edits inside this window collapse into one entry, so undo steps
 * over a whole typed word rather than one character at a time.
 */
const COALESCE_MS = 600

export interface DefinitionHistory {
    canUndo: boolean
    canRedo: boolean
    /** Record a new state. Returns the value so callers can chain. */
    push: (next: WorkflowDefinition) => void
    undo: () => WorkflowDefinition | null
    redo: () => WorkflowDefinition | null
    /** Discard history and start again from `initial` (e.g. after a reload). */
    reset: (initial: WorkflowDefinition | null) => void
}

export function useDefinitionHistory(): DefinitionHistory {
    const past = useRef<WorkflowDefinition[]>([])
    const future = useRef<WorkflowDefinition[]>([])
    const present = useRef<WorkflowDefinition | null>(null)
    const lastPushAt = useRef(0)

    // The refs stay the source of truth so pushes never race with a render;
    // what the toolbar needs is only whether each button is enabled, so that
    // pair is mirrored into state. Deriving it from the refs at render time
    // would be reading a ref during render, which React does not guarantee.
    const [available, setAvailable] = useState({ canUndo: false, canRedo: false })
    const sync = useCallback(
        () =>
            setAvailable({
                canUndo: past.current.length > 0,
                canRedo: future.current.length > 0,
            }),
        [],
    )

    const push = useCallback(
        (next: WorkflowDefinition) => {
            const now = Date.now()
            const previous = present.current
            if (previous) {
                const rapid = now - lastPushAt.current < COALESCE_MS
                if (!rapid) past.current.push(previous)
                if (past.current.length > MAX_HISTORY) past.current.shift()
            }
            lastPushAt.current = now
            present.current = next
            // Any new edit invalidates the redo branch, as in every editor.
            future.current = []
            sync()
        },
        [sync],
    )

    const undo = useCallback(() => {
        const previous = past.current.pop()
        if (!previous) return null
        if (present.current) future.current.push(present.current)
        present.current = previous
        // Force the next push to start a fresh entry rather than coalescing
        // onto the state we just restored.
        lastPushAt.current = 0
        sync()
        return previous
    }, [sync])

    const redo = useCallback(() => {
        const next = future.current.pop()
        if (!next) return null
        if (present.current) past.current.push(present.current)
        present.current = next
        lastPushAt.current = 0
        sync()
        return next
    }, [sync])

    const reset = useCallback(
        (initial: WorkflowDefinition | null) => {
            past.current = []
            future.current = []
            present.current = initial
            lastPushAt.current = 0
            sync()
        },
        [sync],
    )

    return {
        canUndo: available.canUndo,
        canRedo: available.canRedo,
        push,
        undo,
        redo,
        reset,
    }
}
