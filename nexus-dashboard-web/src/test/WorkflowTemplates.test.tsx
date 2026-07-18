import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import WorkflowTemplates from "@/pages/WorkflowTemplates"
import { listTemplates, createWorkflowFromTemplate } from "@/lib/workflow-api"
import { listLocations } from "@/lib/tenant-api"

vi.mock("@/lib/workflow-api", () => ({
    listTemplates: vi.fn(),
    createWorkflowFromTemplate: vi.fn(),
}))
vi.mock("@/lib/tenant-api", () => ({
    listLocations: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const list = listTemplates as ReturnType<typeof vi.fn>
const create = createWorkflowFromTemplate as ReturnType<typeof vi.fn>
const locations = listLocations as ReturnType<typeof vi.fn>

const TEMPLATES = [
    {
        id: "appointment-reminder-24h",
        name: "Appointment Reminder (24h)",
        description: "Remind patients 24h before.",
        trigger_type: "appointment_offset",
        definition: { schema_version: "1.0", trigger: { type: "manual" }, entry_node_id: "e", nodes: [] },
        tags: ["sms", "reminder"],
        category: "appointment_ops",
        metadata: {
            category: "appointment_ops",
            goal: "Reduce missed appointments.",
            outcome_labels: ["reminder_sent"],
            supported_channels: ["sms"],
            required_readiness_checks: ["location", "sms"],
            required_merge_fields: ["patient_first_name"],
            default_compliance_content_class: "transactional_care",
            default_audience: "Upcoming appointments",
            default_eligibility_rules: ["SMS consent exists"],
            default_frequency_cap: { max_per_day: 1, max_per_rolling_7_days: 3 },
            default_staff_handoff_reason: null,
            analytics_outcome_map: { reminder_sent: "sent" },
            sample_preview_context: { patient_first_name: "Jordan" },
            setup_fields: [],
            copy_variants: [{ id: "standard", label: "Standard copy" }],
            pms_capability_requirements: [],
        },
    },
]
const LOCATIONS = [{ id: "loc-1", name: "Downtown", slug: "downtown" }]

beforeEach(() => {
    list.mockReset()
    create.mockReset()
    locations.mockReset()
    locations.mockResolvedValue(LOCATIONS)
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
        expect(screen.getAllByText("Appointment ops").length).toBeGreaterThan(0)
        expect(screen.getByText(/Reduce missed appointments/i)).toBeInTheDocument()
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
        expect(await screen.findByText("Set up campaign")).toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith("appointment-reminder-24h", "Appointment Reminder (24h)", {
                locationId: "loc-1",
                voiceAgentId: "",
                setupOptions: {
                    audience_source: "Upcoming appointments",
                    channel_sequence: "SMS",
                    copy_variant: "standard",
                    staff_handoff_behavior: "Monitor campaign operations",
                },
            })
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
