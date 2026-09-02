/**
 * Canvas keyboard shortcuts on the builder page.
 *
 * Delete is the one people reach for without being told, so its absence reads
 * as the canvas being broken. The rule that matters beyond "it deletes" is that
 * it must not fire while someone is typing — Backspace in a message field has
 * to delete a character, not the step being edited.
 */
import { describe, it, expect, beforeEach, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import WorkflowBuilder from "@/pages/WorkflowBuilder"
import { getWorkflow, validateDefinition } from "@/lib/workflow-api"
import type { AutomationWorkflow } from "@/types"

vi.mock("@/lib/workflow-api", () => ({
    getWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    pauseWorkflow: vi.fn(),
    resumeWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
    publishWorkflow: vi.fn(),
    validateDefinition: vi.fn().mockResolvedValue({ valid: true, issues: [] }),
    listNodeCapabilities: vi.fn().mockResolvedValue({ registry_version: "1.0", nodes: [] }),
    listPhoneCountryRegions: vi.fn().mockResolvedValue([]),
    listMergeFields: vi.fn().mockResolvedValue([]),
}))
vi.mock("@/lib/outbound-voice-api", () => ({
    listOutboundVoiceProfiles: vi.fn().mockResolvedValue([]),
}))
vi.mock("@/lib/retell-sms-api", () => ({
    listRetellSmsChatProfiles: vi.fn().mockResolvedValue([]),
}))
vi.mock("@/lib/tenant-api", () => ({
    listAppointmentTypes: vi.fn().mockResolvedValue([]),
    listProviders: vi.fn().mockResolvedValue([]),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const get = getWorkflow as ReturnType<typeof vi.fn>
const validate = validateDefinition as ReturnType<typeof vi.fn>

const WORKFLOW: AutomationWorkflow = {
    id: "wf-1",
    name: "My Reminder Campaign",
    status: "active",
    trigger_type: "manual",
    definition: {
        schema_version: "1.0",
        trigger: { type: "manual" },
        entry_node_id: "sms-1",
        nodes: [
            { type: "send_sms", id: "sms-1", body_template: "Hi", next_node_id: "exit-1" },
            { type: "exit", id: "exit-1", outcome: "sent" },
        ],
    } as unknown as Record<string, unknown>,
    current_version_id: "v-1",
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:00:00Z",
}

function renderBuilder() {
    return render(
        <MemoryRouter initialEntries={["/institution-admin/campaigns/wf-1/builder"]}>
            <Routes>
                <Route path="/institution-admin/campaigns/:id/builder" element={<WorkflowBuilder />} />
            </Routes>
        </MemoryRouter>,
    )
}

/**
 * The rendered card for a step, which is also what selects it when clicked.
 * Clicked with `fireEvent` rather than `userEvent`: the latter's mousedown
 * reaches React Flow's d3-drag, which then throws on a torn-down jsdom window.
 */
const card = (label: string) => screen.getAllByText(label)[0]

beforeEach(() => {
    get.mockReset()
    validate.mockReset()
    validate.mockResolvedValue({ valid: true, issues: [] })
    localStorage.clear()
    get.mockResolvedValue(WORKFLOW)
})

describe("builder keyboard shortcuts", () => {
    it("deletes the selected step on Delete", async () => {
        renderBuilder()
        await screen.findByText("Send SMS")

        fireEvent.click(card("Send SMS"))
        fireEvent.keyDown(window, { key: "Delete" })

        await waitFor(() => {
            expect(screen.queryByText("Send SMS")).not.toBeInTheDocument()
        })
        // The rest of the campaign survives. Exits render as outcome chips, so
        // the surviving node is identified by its outcome rather than "Exit".
        expect(screen.getByText("sent")).toBeInTheDocument()
    })

    it("leaves the canvas alone when nothing is selected", async () => {
        renderBuilder()
        await screen.findByText("Send SMS")

        fireEvent.keyDown(window, { key: "Delete" })

        expect(screen.getByText("Send SMS")).toBeInTheDocument()
    })

    it("does not delete while typing in a field", async () => {
        renderBuilder()
        await screen.findByText("Send SMS")
        fireEvent.click(card("Send SMS"))

        // The campaign-name input stands in for any text field on the page.
        const name = screen.getByDisplayValue("My Reminder Campaign")
        fireEvent.keyDown(name, { key: "Backspace" })

        // Selecting also opens the config panel, which names the step too, so
        // the step surviving means at least one mention remains.
        expect(screen.getAllByText("Send SMS").length).toBeGreaterThan(0)
    })
})
