import { beforeEach, describe, expect, it, vi } from "vitest"
import type { WorkflowDefinition } from "@/types/workflow"

const layoutMock = vi.fn()

vi.mock("elkjs/lib/elk.bundled.js", () => ({
    default: vi.fn().mockImplementation(function ElkMock() {
        return { layout: layoutMock }
    }),
}))

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "sms-1",
    nodes: [
        { type: "send_sms", id: "sms-1", body_template: "Hi", next_node_id: "exit-1" },
        { type: "exit", id: "exit-1", outcome: "sent" },
    ],
}

describe("ELK workflow auto-layout", () => {
    beforeEach(() => {
        layoutMock.mockReset()
    })

    it("persists positions returned by ELK", async () => {
        layoutMock.mockResolvedValue({
            id: "workflow",
            children: [
                { id: "__trigger__", x: 10, y: 20 },
                { id: "sms-1", x: 200, y: 20 },
                { id: "exit-1", x: 400, y: 20 },
            ],
        })
        const { elkAutoLayoutDefinition } = await import("@/lib/workflow/elk-layout")
        const next = await elkAutoLayoutDefinition(DEF)
        expect(layoutMock).toHaveBeenCalledTimes(1)
        expect(next.layout).toEqual({
            "__trigger__": { x: 10, y: 20 },
            "sms-1": { x: 200, y: 20 },
            "exit-1": { x: 400, y: 20 },
        })
    })

    it("falls back when ELK fails", async () => {
        layoutMock.mockRejectedValue(new Error("layout failed"))
        const { elkAutoLayoutDefinition } = await import("@/lib/workflow/elk-layout")
        const next = await elkAutoLayoutDefinition(DEF)
        expect(next.layout?.["__trigger__"]).toBeTruthy()
        expect(next.layout?.["sms-1"]).toBeTruthy()
        expect(next.layout?.["exit-1"]).toBeTruthy()
    })
})
