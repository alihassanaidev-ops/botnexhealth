import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import TestRunDialog from "@/components/workflow/TestRunDialog"
import api from "@/lib/api"
import { listContacts } from "@/lib/contacts-api"
import type { WorkflowDefinition } from "@/types/workflow"

// The dialog calls the authoritative server dry-run via workflow-api -> @/lib/api.
vi.mock("@/lib/api", () => ({
    default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))
vi.mock("@/lib/contacts-api", () => ({ listContacts: vi.fn() }))

const post = api.post as ReturnType<typeof vi.fn>
const listContactsMock = listContacts as ReturnType<typeof vi.fn>

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
    listContactsMock.mockReset()
    listContactsMock.mockResolvedValue({ total: 0, limit: 8, offset: 0, items: [] })
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

describe("TestRunDialog — previewing against a real patient", () => {
    const SARAH = {
        id: "c-1",
        full_name: "Sarah Chen",
        first_name: "Sarah",
        last_name: "Chen",
        is_new_patient: false,
        lifecycle: "patient" as const,
        lead_status: null,
        source: null,
        email_masked: null,
        has_notes: false,
        pms_last_synced_at: null,
        phone_masked: null,
    }

    it("sends the chosen contact id and surfaces the merge fields that render blank", async () => {
        const user = userEvent.setup()
        listContactsMock.mockResolvedValue({ total: 1, limit: 8, offset: 0, items: [SARAH] })
        post.mockResolvedValue({
            data: {
                steps: [{ node_id: "sms-1", node_type: "send_sms", summary: "Send SMS" }],
                outcome: "sent",
                truncated: false,
                context_source: "contact",
                contact_name: "Sarah Chen",
                empty_fields: [{ name: "appointment_date", nodes: ["sms-1"] }],
            },
        })

        render(<TestRunDialog open onOpenChange={() => {}} def={DEF} locationId="loc-1" />)

        await user.type(screen.getByPlaceholderText(/search a real patient/i), "Sarah")
        await user.click(await screen.findByRole("button", { name: /Sarah Chen/ }))

        await waitFor(() =>
            expect(post).toHaveBeenLastCalledWith(
                "/automation/workflows/dry-run",
                expect.objectContaining({ contact_id: "c-1", location_id: "loc-1" }),
            ),
        )
        expect(await screen.findByText(/1 merge field rendered blank/i)).toBeInTheDocument()
        expect(screen.getByText("{{appointment_date}}")).toBeInTheDocument()
    })

    it("does not send a contact id while previewing with sample data", async () => {
        post.mockResolvedValue({
            data: { steps: [], outcome: null, truncated: false },
        })

        render(<TestRunDialog open onOpenChange={() => {}} def={DEF} />)

        await waitFor(() => expect(post).toHaveBeenCalled())
        expect(post.mock.calls[0][1]).not.toHaveProperty("contact_id")
        expect(screen.queryByText(/rendered blank/i)).not.toBeInTheDocument()
    })
})
