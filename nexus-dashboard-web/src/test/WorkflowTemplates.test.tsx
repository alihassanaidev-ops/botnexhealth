import { describe, it, expect, beforeEach, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import WorkflowTemplates from "@/pages/WorkflowTemplates"
import { listTemplates, createWorkflowFromTemplate } from "@/lib/workflow-api"
import { listAppointmentTypes, listLocations, listProviders } from "@/lib/tenant-api"
import { listOutboundVoiceProfiles } from "@/lib/outbound-voice-api"
import { listRetellSmsChatProfiles } from "@/lib/retell-sms-api"

vi.mock("@/lib/workflow-api", () => ({
    listTemplates: vi.fn(),
    createWorkflowFromTemplate: vi.fn(),
}))
vi.mock("@/lib/tenant-api", () => ({
    listLocations: vi.fn(),
    listAppointmentTypes: vi.fn(),
    listProviders: vi.fn(),
}))
vi.mock("@/lib/outbound-voice-api", () => ({
    listOutboundVoiceProfiles: vi.fn(),
}))
vi.mock("@/lib/retell-sms-api", () => ({
    listRetellSmsChatProfiles: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const list = listTemplates as ReturnType<typeof vi.fn>
const create = createWorkflowFromTemplate as ReturnType<typeof vi.fn>
const locations = listLocations as ReturnType<typeof vi.fn>
const appointmentTypes = listAppointmentTypes as ReturnType<typeof vi.fn>
const providers = listProviders as ReturnType<typeof vi.fn>
const voiceProfiles = listOutboundVoiceProfiles as ReturnType<typeof vi.fn>
const retellSmsProfiles = listRetellSmsChatProfiles as ReturnType<typeof vi.fn>

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
            default_staff_handoff_reason: "patient_asks_for_staff",
            analytics_outcome_map: { reminder_sent: "sent" },
            sample_preview_context: { patient_first_name: "Jordan" },
            setup_fields: [
                { id: "call_offset_hours_before", label: "First reminder hours before appointment", type: "number", required: true, default: 24 },
                { id: "retry_delay_1_hours", label: "Second reminder delay (hours)", type: "number", required: true, default: 12 },
                { id: "retry_delay_2_hours", label: "Final reply window (hours)", type: "number", required: true, default: 6 },
            ],
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
            { id: "call_offset_hours_before", label: "Initial call hours before appointment", type: "number", required: true, default: 24 },
            { id: "retry_delay_1_hours", label: "Delay before second attempt (hours)", type: "number", required: true, default: 5 },
            { id: "retry_delay_2_hours", label: "Delay before third attempt (hours)", type: "number", required: true, default: 5 },
            { id: "patient_voice_cooldown_hours", label: "Patient cooldown", type: "number", required: true, default: 24 },
        ],
    },
}
const POST_OP_TEMPLATE = {
    ...TEMPLATES[0],
    id: "post-op-followup-after-confirmation",
    name: "Post-Op Follow-Up After Completed Visit",
    metadata: {
        ...TEMPLATES[0].metadata,
        supported_channels: ["voice"],
        default_frequency_cap: { max_per_day: 1, max_per_rolling_7_days: 3 },
        setup_fields: [
            { id: "voice_profile_id", label: "Post-op voice profile", type: "voice_profile_select", required: true },
            { id: "post_op_reasons", label: "Eligible completed GoTracker reasons", type: "string_list", required: true },
            { id: "post_op_delay_hours", label: "Hours after completion before calling", type: "number", required: true, default: 24 },
            { id: "post_op_latest_call_hours", label: "Latest allowed post-op call", type: "number", required: true, default: 72 },
            { id: "patient_voice_cooldown_hours", label: "Patient cooldown", type: "number", required: true, default: 24 },
        ],
    },
}
const SALES_TEMPLATE = {
    ...TEMPLATES[0],
    id: "sales-qualification",
    name: "Sales Qualification",
    description: "Qualify inbound sales enquiries over SMS.",
    trigger_type: "enquiry_received",
    category: "sales",
    metadata: {
        ...TEMPLATES[0].metadata,
        category: "sales",
        supported_channels: ["sms"],
        default_audience: "Inbound enquiries",
        setup_fields: [
            { id: "retell_sms_profile_id", label: "Sales qualification SMS profile", type: "retell_sms_profile_select", required: true },
            { id: "sales_provider_id", label: "Registration and booking provider", type: "provider_select", required: true },
            { id: "sales_appointment_type_ids", label: "Bookable appointment types", type: "appointment_type_multiselect", required: true },
            { id: "sales_booking_window_days", label: "Booking window (days)", type: "number", required: true, default: 14 },
        ],
    },
}
const RECALL_TEMPLATE = {
    ...TEMPLATES[0],
    id: "recall-sms-6month",
    name: "Recall Outreach (6-Month)",
    description: "Bring overdue recall patients back onto the schedule.",
    trigger_type: "recall_scan",
    category: "recall",
    metadata: {
        ...TEMPLATES[0].metadata,
        category: "recall",
        goal: "Bring overdue recall patients back onto the schedule while excluding active treatment plans.",
        supported_channels: ["sms"],
        default_audience: "Patients due or overdue for recall",
        setup_fields: [
            { id: "recall_reenrollment_cooldown_days", label: "Recall cooldown (days)", type: "number", required: true, default: 90 },
            { id: "recall_booking_window_days", label: "Booking window (days)", type: "number", required: true, default: 30 },
        ],
    },
}
const LOCATIONS = [{ id: "loc-1", name: "Downtown", slug: "downtown" }]

beforeEach(() => {
    list.mockReset()
    create.mockReset()
    locations.mockReset()
    appointmentTypes.mockReset()
    providers.mockReset()
    voiceProfiles.mockReset()
    retellSmsProfiles.mockReset()
    locations.mockResolvedValue(LOCATIONS)
    appointmentTypes.mockResolvedValue([])
    providers.mockResolvedValue([])
    voiceProfiles.mockResolvedValue([])
    retellSmsProfiles.mockResolvedValue([])
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
        expect(screen.getByLabelText("First reminder hours before appointment")).toHaveValue(24)
        expect(screen.getByLabelText("Second reminder delay (hours)")).toHaveValue(12)
        expect(screen.getByLabelText("Final reply window (hours)")).toHaveValue(6)
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith("appointment-reminder-24h", "Appointment Reminder (24h)", {
                locationId: "loc-1",
                voiceProfileId: "",
                setupOptions: {
                    audience_source: "Upcoming appointments",
                    channel_sequence: "SMS",
                    copy_variant: "standard",
                    staff_handoff_behavior: "patient_asks_for_staff",
                    call_offset_hours_before: 24,
                    retry_delay_1_hours: 12,
                    retry_delay_2_hours: 6,
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
        await user.clear(screen.getByLabelText("Initial call hours before appointment"))
        await user.type(screen.getByLabelText("Initial call hours before appointment"), "0")
        await user.clear(screen.getByLabelText("Delay before second attempt (hours)"))
        await user.type(screen.getByLabelText("Delay before second attempt (hours)"), "4")
        await user.clear(screen.getByLabelText("Delay before third attempt (hours)"))
        await user.type(screen.getByLabelText("Delay before third attempt (hours)"), "7.5")
        await user.clear(screen.getByLabelText("Patient cooldown (hours)"))
        await user.type(screen.getByLabelText("Patient cooldown (hours)"), "12")
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
                        patient_voice_cooldown_hours: 12,
                    }),
                }),
            )
        })
    }, 10_000)

    it("configures completed-visit reasons and post-op call timing", async () => {
        list.mockResolvedValue([POST_OP_TEMPLATE])
        voiceProfiles.mockResolvedValue([
            { id: "profile-postop", display_name: "Post Appointment", purpose: "post_op" },
        ])
        create.mockResolvedValue({ id: "wf-postop", name: POST_OP_TEMPLATE.name })
        const user = userEvent.setup()
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )

        await screen.findByText(POST_OP_TEMPLATE.name)
        await user.click(screen.getByRole("button", { name: /use template/i }))

        expect(screen.getByLabelText("Eligible completed GoTracker reasons")).toBeInTheDocument()
        expect(screen.getByLabelText("Hours after completion before calling")).toHaveValue(24)
        expect(screen.getByLabelText("Latest allowed post-op call (hours after completion)")).toHaveValue(72)

        await user.click(await screen.findByRole("combobox", { name: "Voice profile" }))
        await user.click(screen.getByRole("option", { name: "Post Appointment" }))
        fireEvent.change(screen.getByLabelText("Eligible completed GoTracker reasons"), {
            target: { value: "Extraction, Implant Surgery" },
        })
        fireEvent.change(screen.getByLabelText("Hours after completion before calling"), {
            target: { value: "0" },
        })
        fireEvent.change(screen.getByLabelText("Latest allowed post-op call (hours after completion)"), {
            target: { value: "24" },
        })
        fireEvent.change(screen.getByLabelText("Patient cooldown (hours)"), {
            target: { value: "0" },
        })
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith(
                POST_OP_TEMPLATE.id,
                POST_OP_TEMPLATE.name,
                expect.objectContaining({
                    voiceProfileId: "profile-postop",
                    setupOptions: expect.objectContaining({
                        post_op_reasons: ["Extraction", "Implant Surgery"],
                        post_op_delay_hours: 0,
                        post_op_latest_call_hours: 24,
                        patient_voice_cooldown_hours: 0,
                    }),
                }),
            )
        })
    }, 10_000)

    it("configures sales qualification SMS profile provider appointment types and window", async () => {
        list.mockResolvedValue([SALES_TEMPLATE])
        retellSmsProfiles.mockResolvedValue([
            { id: "sms-profile-1", display_name: "Sales SMS", purpose: "sales", is_active: true },
        ])
        providers.mockResolvedValue([
            { source_id: "provider-1", name: "Dr Lane", is_active: true, is_hidden: false },
        ])
        appointmentTypes.mockResolvedValue([
            { source_id: "new-patient", name: "New patient exam", duration_minutes: 60, is_active: true },
        ])
        create.mockResolvedValue({ id: "wf-sales", name: SALES_TEMPLATE.name })
        const user = userEvent.setup()
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )

        await screen.findByText(SALES_TEMPLATE.name)
        await user.click(screen.getByRole("button", { name: /use template/i }))
        await user.click(await screen.findByRole("combobox", { name: "Retell SMS profile" }))
        await user.click(screen.getByRole("option", { name: "Sales SMS" }))
        await user.click(await screen.findByRole("combobox", { name: "Registration and booking provider" }))
        await user.click(screen.getByRole("option", { name: "Dr Lane" }))
        await waitFor(() => expect(appointmentTypes).toHaveBeenCalledWith("loc-1"))
        await user.click(await screen.findByText("New patient exam"))
        fireEvent.change(screen.getByLabelText("Booking window (days)"), {
            target: { value: "21" },
        })
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith(
                SALES_TEMPLATE.id,
                SALES_TEMPLATE.name,
                expect.objectContaining({
                    locationId: "loc-1",
                    voiceProfileId: "",
                    setupOptions: expect.objectContaining({
                        retell_sms_profile_id: "sms-profile-1",
                        sales_provider_id: "provider-1",
                        sales_appointment_type_ids: ["new-patient"],
                        sales_booking_window_days: 21,
                    }),
                }),
            )
        })
    }, 10_000)

    it("configures recall cooldown and booking window", async () => {
        list.mockResolvedValue([RECALL_TEMPLATE])
        create.mockResolvedValue({ id: "wf-recall", name: RECALL_TEMPLATE.name })
        const user = userEvent.setup()
        render(
            <MemoryRouter>
                <WorkflowTemplates />
            </MemoryRouter>,
        )

        await screen.findByText(RECALL_TEMPLATE.name)
        await user.click(screen.getByRole("button", { name: /use template/i }))

        expect(screen.getByLabelText("Recall cooldown (days)")).toHaveValue(90)
        expect(screen.getByLabelText("Booking window (days)")).toHaveValue(30)

        fireEvent.change(screen.getByLabelText("Recall cooldown (days)"), {
            target: { value: "120" },
        })
        fireEvent.change(screen.getByLabelText("Booking window (days)"), {
            target: { value: "45" },
        })
        await user.click(screen.getByRole("button", { name: /create & open builder/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith(
                RECALL_TEMPLATE.id,
                RECALL_TEMPLATE.name,
                expect.objectContaining({
                    locationId: "loc-1",
                    voiceProfileId: "",
                    setupOptions: expect.objectContaining({
                        recall_reenrollment_cooldown_days: 120,
                        recall_booking_window_days: 45,
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
