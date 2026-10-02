import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useDefinitionHistory } from "@/lib/workflow/use-history"
import type { WorkflowDefinition } from "@/types/workflow"

function def(entry: string): WorkflowDefinition {
    return {
        schema_version: "1.0",
        trigger: { type: "manual" },
        entry_node_id: entry,
        nodes: [{ type: "exit", id: entry, outcome: entry }],
    }
}

beforeEach(() => {
    vi.useFakeTimers()
})

afterEach(() => {
    vi.useRealTimers()
})

/** Push with enough elapsed time that entries do not coalesce. */
function pushDistinct(result: { current: ReturnType<typeof useDefinitionHistory> }, value: WorkflowDefinition) {
    act(() => {
        vi.advanceTimersByTime(1000)
        result.current.push(value)
    })
}

describe("definition history", () => {
    it("starts with nothing to undo or redo", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        expect(result.current.canUndo).toBe(false)
        expect(result.current.canRedo).toBe(false)
        expect(result.current.undo()).toBeNull()
        expect(result.current.redo()).toBeNull()
    })

    it("undoes and redoes across edits", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        pushDistinct(result, def("a"))
        pushDistinct(result, def("b"))
        pushDistinct(result, def("c"))

        expect(result.current.canUndo).toBe(true)

        let restored: WorkflowDefinition | null | undefined
        act(() => {
            restored = result.current.undo()
        })
        expect(restored?.entry_node_id).toBe("b")

        act(() => {
            restored = result.current.undo()
        })
        expect(restored?.entry_node_id).toBe("a")
        expect(result.current.canUndo).toBe(false)

        act(() => {
            restored = result.current.redo()
        })
        expect(restored?.entry_node_id).toBe("b")
    })

    it("collapses rapid edits so undo steps over a typed word, not a keystroke", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        pushDistinct(result, def("start"))
        act(() => {
            // A gap, then three keystrokes inside the coalesce window. The gap
            // matters: the first edit after a pause must record the pre-burst
            // state, and only the keystrokes after it collapse together.
            vi.advanceTimersByTime(1000)
            result.current.push(def("t"))
            result.current.push(def("ty"))
            result.current.push(def("typ"))
        })

        let restored: WorkflowDefinition | null | undefined
        act(() => {
            restored = result.current.undo()
        })
        // One undo returns to before the burst, not to "ty".
        expect(restored?.entry_node_id).toBe("start")
    })

    it("drops the redo branch once a new edit is made", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        pushDistinct(result, def("a"))
        pushDistinct(result, def("b"))
        act(() => {
            result.current.undo()
        })
        expect(result.current.canRedo).toBe(true)

        pushDistinct(result, def("c"))
        expect(result.current.canRedo).toBe(false)
    })

    it("does not coalesce the first edit after an undo", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        pushDistinct(result, def("a"))
        pushDistinct(result, def("b"))
        act(() => {
            result.current.undo()
            // Immediately after undo — would coalesce if the timer were not reset.
            result.current.push(def("c"))
        })

        let restored: WorkflowDefinition | null | undefined
        act(() => {
            restored = result.current.undo()
        })
        expect(restored?.entry_node_id).toBe("a")
    })

    it("bounds how much history it keeps", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        for (let i = 0; i < 80; i++) pushDistinct(result, def(`n${i}`))

        let steps = 0
        act(() => {
            while (result.current.undo()) {
                steps += 1
                if (steps > 200) break // guard against an infinite loop
            }
        })
        expect(steps).toBeLessThanOrEqual(50)
        expect(steps).toBeGreaterThan(0)
    })

    it("reset clears history, so loading a workflow is not undoable", () => {
        const { result } = renderHook(() => useDefinitionHistory())
        pushDistinct(result, def("a"))
        pushDistinct(result, def("b"))

        act(() => {
            result.current.reset(def("loaded"))
        })
        expect(result.current.canUndo).toBe(false)
        expect(result.current.canRedo).toBe(false)
    })
})
