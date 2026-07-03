import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import WorkflowTemplates from "@/pages/WorkflowTemplates"
import { listTemplates, createWorkflowFromTemplate } from "@/lib/workflow-api"

vi.mock("@/lib/workflow-api", () => ({
    listTemplates: vi.fn(),
    createWorkflowFromTemplate: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const list = listTemplates as ReturnType<typeof vi.fn>
const create = createWorkflowFromTemplate as ReturnType<typeof vi.fn>

const TEMPLATES = [
    {
        id: "appointment-reminder-24h",
        name: "Appointment Reminder (24h)",
        description: "Remind patients 24h before.",
        trigger_type: "appointment_offset",
        definition: { schema_version: "1.0", trigger: { type: "manual" }, entry_node_id: "e", nodes: [] },
        tags: ["sms", "reminder"],
    },
]

beforeEach(() => {
    list.mockReset()
    create.mockReset()
})

describe("WorkflowTemplates page", () => {
    it("renders template cards from the API", async () => {
        list.mockResolvedValue(TEMPLATES)
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )
        expect(await screen.findByText("Appointment Reminder (24h)")).toBeInTheDocument()
        expect(screen.getByText("Remind patients 24h before.")).toBeInTheDocument()
        expect(screen.getByText("reminder")).toBeInTheDocument()
    })

    it("clones the selected template with the entered name", async () => {
        list.mockResolvedValue(TEMPLATES)
        create.mockResolvedValue({ id: "wf-1", name: "My Reminder" })
        const user = userEvent.setup()
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )
        await screen.findByText("Appointment Reminder (24h)")
        await user.click(screen.getByRole("button", { name: /use template/i }))

        // Naming dialog opens, pre-filled with the template name.
        expect(await screen.findByText("Name your campaign")).toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith("appointment-reminder-24h", "Appointment Reminder (24h)")
        })
    })

    it("shows an empty state when there are no templates", async () => {
        list.mockResolvedValue([])
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )
        expect(await screen.findByText("No templates available")).toBeInTheDocument()
    })
})
