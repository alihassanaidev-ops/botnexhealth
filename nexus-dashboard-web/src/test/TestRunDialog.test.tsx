import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import TestRunDialog from "@/components/workflow/TestRunDialog"
import api from "@/lib/api"
import type { WorkflowDefinition } from "@/types/workflow"

// The dialog calls the authoritative server dry-run via workflow-api -> @/lib/api.
vi.mock("@/lib/api", () => ({
    default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

const post = api.post as ReturnType<typeof vi.fn>

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "sms-1",
    nodes: [
        { type: "send_sms", id: "sms-1", body_template: "Hi", next_node_id: "exit-1" },
        { type: "exit", id: "exit-1", outcome: "sent" },
    ],
}

beforeEach(() => {
    post.mockReset()
})

describe("TestRunDialog — server-side dry-run", () => {
    it("calls the dry-run endpoint and renders the steps the server returns", async () => {
        post.mockResolvedValue({
            data: {
                steps: [
                    { node_id: "sms-1", node_type: "send_sms", summary: "Server: send welcome SMS" },
                    { node_id: "exit-1", node_type: "exit", summary: "Server: exit", detail: "sent" },
                ],
                outcome: "sent",
                truncated: false,
            },
        })

        render(<TestRunDialog open onOpenChange={() => {}} def={DEF} />)

        await waitFor(() =>
            expect(post).toHaveBeenCalledWith(
                "/automation/workflows/dry-run",
                expect.objectContaining({ definition: DEF, condition_choices: {} }),
            ),
        )
        expect(await screen.findByText("Server: send welcome SMS")).toBeInTheDocument()
        expect(screen.getByText("Server: exit")).toBeInTheDocument()
    })

    it("falls back to the client-side walker when the request fails", async () => {
        post.mockRejectedValue(new Error("network"))

        render(<TestRunDialog open onOpenChange={() => {}} def={DEF} />)

        await waitFor(() => expect(post).toHaveBeenCalled())
        // Offline walker labels the SMS step "Send SMS" and shows the fallback notice.
        expect(await screen.findByText("Send SMS")).toBeInTheDocument()
        expect(screen.getByText(/simulated locally/i)).toBeInTheDocument()
    })
})
