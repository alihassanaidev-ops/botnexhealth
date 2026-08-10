import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import WorkflowTemplates from "@/pages/WorkflowTemplates"
import { listTemplates, createWorkflowFromTemplate } from "@/lib/workflow-api"
import { listAppointmentTypes, listLocations } from "@/lib/tenant-api"
import { listOutboundVoiceProfiles } from "@/lib/outbound-voice-api"

vi.mock("@/lib/workflow-api", () => ({
    listTemplates: vi.fn(),
    createWorkflowFromTemplate: vi.fn(),
}))
vi.mock("@/lib/tenant-api", () => ({
    listLocations: vi.fn(),
    listAppointmentTypes: vi.fn(),
}))
vi.mock("@/lib/outbound-voice-api", () => ({
    listOutboundVoiceProfiles: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const list = listTemplates as ReturnType<typeof vi.fn>
const create = createWorkflowFromTemplate as ReturnType<typeof vi.fn>
const locations = listLocations as ReturnType<typeof vi.fn>
const appointmentTypes = listAppointmentTypes as ReturnType<typeof vi.fn>
const voiceProfiles = listOutboundVoiceProfiles as ReturnType<typeof vi.fn>

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
const UNSUPPORTED_TEMPLATE = {
    ...TEMPLATES[0],
    id: "unscheduled-treatment-followup",
    name: "Unscheduled Treatment Follow-Up",
    description: "Follow up on unscheduled treatment.",
    category: "treatment",
    metadata: {
        ...TEMPLATES[0].metadata,
        category: "treatment",
        pms_capability_requirements: ["treatment_plans"],
        pms_capability_evaluation: {
            requirements: ["treatment_plans"],
            supported: false,
            status: "unsupported",
            pms_name: "Dentrix Ascend",
            missing: ["treatment_plans"],
            partial: [],
            unknown: [],
            details: {},
            message: "Dentrix Ascend does not support: treatment_plans.",
        },
    },
}
const PRE_APPOINTMENT_TEMPLATE = {
    ...TEMPLATES[0],
    id: "surgery-pre-appointment-confirmation",
    name: "Surgery Pre-Appointment Confirmation",
    metadata: {
        ...TEMPLATES[0].metadata,
        supported_channels: ["voice"],
        default_frequency_cap: { max_per_day: 3, max_per_rolling_7_days: 3 },
        setup_fields: [
            { id: "voice_profile_id", label: "Voice profile", type: "voice_profile_select", required: true },
            { id: "appointment_reasons", label: "Eligible reasons", type: "string_list", required: true },
            { id: "call_offset_hours_before", label: "Call hours before", type: "number", required: true, default: 24 },
            { id: "retry_delay_1_hours", label: "Retry 1", type: "number", required: true, default: 5 },
            { id: "retry_delay_2_hours", label: "Retry 2", type: "number", required: true, default: 5 },
        ],
    },
}
const LOCATIONS = [{ id: "loc-1", name: "Downtown", slug: "downtown" }]

beforeEach(() => {
    list.mockReset()
    create.mockReset()
    locations.mockReset()
    appointmentTypes.mockReset()
    voiceProfiles.mockReset()
    locations.mockResolvedValue(LOCATIONS)
    appointmentTypes.mockResolvedValue([])
    voiceProfiles.mockResolvedValue([])
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
        expect(list).toHaveBeenCalledWith("loc-1")
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
        expect(screen.getByRole("combobox", { name: "Audience source" })).toBeInTheDocument()
        expect(screen.getByRole("combobox", { name: "Channel sequence" })).toBeInTheDocument()
        expect(screen.getByRole("combobox", { name: "Message copy" })).toBeInTheDocument()
        expect(screen.getByRole("combobox", { name: "Staff handoff behavior" })).toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith("appointment-reminder-24h", "Appointment Reminder (24h)", {
                locationId: "loc-1",
                voiceProfileId: "",
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

    it("configures GoTracker reasons and independent voice retry delays", async () => {
        list.mockResolvedValue([PRE_APPOINTMENT_TEMPLATE])
        voiceProfiles.mockResolvedValue([
            { id: "profile-preop", display_name: "Pre-appointment", purpose: "reminder" },
        ])
        create.mockResolvedValue({ id: "wf-preop", name: PRE_APPOINTMENT_TEMPLATE.name })
        const user = userEvent.setup()
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )

        await screen.findByText(PRE_APPOINTMENT_TEMPLATE.name)
        await user.click(screen.getByRole("button", { name: /use template/i }))
        await user.click(await screen.findByRole("combobox", { name: "Voice profile" }))
        await user.click(screen.getByRole("option", { name: "Pre-appointment" }))
        await user.type(screen.getByLabelText("Eligible GoTracker reasons"), "Bridge Prep, Implant Surgery")
        await user.clear(screen.getByLabelText("Call hours before"))
        await user.type(screen.getByLabelText("Call hours before"), "0")
        await user.clear(screen.getByLabelText("Retry 1 delay (hours)"))
        await user.type(screen.getByLabelText("Retry 1 delay (hours)"), "4")
        await user.clear(screen.getByLabelText("Retry 2 delay (hours)"))
        await user.type(screen.getByLabelText("Retry 2 delay (hours)"), "7.5")
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith(
                PRE_APPOINTMENT_TEMPLATE.id,
                PRE_APPOINTMENT_TEMPLATE.name,
                expect.objectContaining({
                    voiceProfileId: "profile-preop",
                    setupOptions: expect.objectContaining({
                        appointment_reasons: ["Bridge Prep", "Implant Surgery"],
                        call_offset_hours_before: 0,
                        retry_delay_1_hours: 4,
                        retry_delay_2_hours: 7.5,
                    }),
                }),
            )
        })
    }, 10_000)

    it("disables templates when the selected location PMS does not support them", async () => {
        list.mockResolvedValue([UNSUPPORTED_TEMPLATE])
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )

        expect(await screen.findByText("Unsupported")).toBeInTheDocument()
        expect(screen.getByText("Dentrix Ascend does not support: treatment_plans.")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /use template/i })).toBeDisabled()
    })
})
