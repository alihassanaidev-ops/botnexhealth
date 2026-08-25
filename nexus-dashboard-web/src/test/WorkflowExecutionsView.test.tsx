import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import WorkflowExecutionsView from "@/components/workflow/WorkflowExecutionsView"
import { getRunTimeline, listCampaignRuns } from "@/lib/automation-api"

vi.mock("@/lib/automation-api", () => ({
    listCampaignRuns: vi.fn(),
    getRunTimeline: vi.fn(),
}))

vi.mock("@/components/workflow/WorkflowCanvas", () => ({
    default: ({ nodes, onSelect }: { nodes: Array<{ id: string; data: { executionStatus?: string } }>; onSelect: (id: string) => void }) => (
        <div>
            {nodes.map((node) => (
                <button key={node.id} type="button" onClick={() => onSelect(node.id)}>
                    {node.id}:{node.data.executionStatus ?? "not-run"}
                </button>
            ))}
        </div>
    ),
}))

describe("WorkflowExecutionsView", () => {
    it("renders the published run version and recorded step snapshots", async () => {
        vi.mocked(listCampaignRuns).mockResolvedValue({
            items: [{
                id: "run-1",
                workflow_id: "wf-1",
                workflow_version_id: "version-3",
                status: "failed",
                current_step_id: "sms-1",
                current_step_type: "send_sms",
                outcome: null,
                blocked_reason: null,
                contact_id: "contact-1",
                contact_name: "Jordan Rivera",
                next_due_at: null,
                latest_event_at: "2026-08-25T10:00:02Z",
                started_at: "2026-08-25T10:00:00Z",
                completed_at: "2026-08-25T10:00:02Z",
                created_at: "2026-08-25T10:00:00Z",
            }],
            limit: 50,
            next_cursor: null,
        })
        vi.mocked(getRunTimeline).mockResolvedValue({
            run: (await listCampaignRuns("wf-1")).items[0],
            contact: { id: "contact-1", display_name: "Jordan Rivera", phone_masked: null },
            workflow_version: {
                id: "version-3",
                version_number: 3,
                published_at: "2026-08-24T00:00:00Z",
                definition: {
                    schema_version: "1.0",
                    trigger: { type: "manual" },
                    entry_node_id: "sms-1",
                    nodes: [
                        { type: "send_sms", id: "sms-1", body_template: "redacted", next_node_id: "exit-1" },
                        { type: "exit", id: "exit-1", outcome: "done" },
                    ],
                },
            },
            items: [{
                id: "attempt-1",
                kind: "step_execution",
                occurred_at: "2026-08-25T10:00:01Z",
                title: "SMS step",
                status: "failed",
                step_id: "sms-1",
                channel: "sms",
                summary: "Result: vendor_error",
                metadata: { attempt_number: 1 },
                input: { appointment_id: "appt-1" },
                output: { result_code: "vendor_error" },
                node: { type: "send_sms" },
                duration_ms: 1200,
                error_message: "Vendor rejected request",
            }],
        })

        render(<WorkflowExecutionsView workflowId="wf-1" />)

        expect(await screen.findByText("Version 3")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "sms-1:failed" })).toBeInTheDocument()
        expect(screen.getByText("Vendor rejected request")).toBeInTheDocument()
        expect(screen.getByText(/"appointment_id": "appt-1"/)).toBeInTheDocument()

        await userEvent.click(screen.getByRole("button", { name: /Output/i }))
        expect(screen.getByText(/"result_code": "vendor_error"/)).toBeInTheDocument()
    })
})
