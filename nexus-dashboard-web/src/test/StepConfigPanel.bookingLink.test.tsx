import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import { blankDefinition, createNode } from "@/lib/workflow/graph"
import type { BookAppointmentNode, BookingLinkNode, PatientRegistrationNode, WorkflowDefinition } from "@/types/workflow"
import type { CachedAppointmentType, CachedProvider } from "@/types"

const TYPES = [
    { id: "1", source_id: "101", name: "New Patient Exam", duration_minutes: 60, source_metadata: {} },
    { id: "2", source_id: "202", name: "Cleaning", duration_minutes: 45, source_metadata: {} },
] as unknown as CachedAppointmentType[]

const PROVIDERS = [
    { source_id: "77", name: "Dr. Kadri", first_name: null, last_name: null, is_hidden: false },
    { source_id: "88", name: "Hidden Hygienist", first_name: null, last_name: null, is_hidden: true },
] as unknown as CachedProvider[]

function def(node: BookAppointmentNode | BookingLinkNode | PatientRegistrationNode): WorkflowDefinition {
    const base = blankDefinition()
    return { ...base, entry_node_id: node.id, nodes: [node] }
}

function renderPanel(node: BookAppointmentNode | BookingLinkNode | PatientRegistrationNode, opts: {
    appointmentTypes?: CachedAppointmentType[]
    providers?: CachedProvider[]
} = {}) {
    const onNodeChange = vi.fn()
    render(
        <StepConfigPanel
            open
            onOpenChange={vi.fn()}
            def={def(node)}
            selectedId={node.id}
            onNodeChange={onNodeChange}
            onDefinitionChange={vi.fn()}
            onTriggerChange={vi.fn()}
            onDeleteNode={vi.fn()}
            onSetEntry={vi.fn()}
            appointmentTypes={opts.appointmentTypes ?? TYPES}
            providers={opts.providers ?? PROVIDERS}
        />,
    )
    return onNodeChange
}

describe("Booking Link config", () => {
    it("lists appointment types by name instead of asking for ids", async () => {
        renderPanel(createNode("booking_link", "b1") as BookingLinkNode)
        expect(await screen.findByText("New Patient Exam")).toBeInTheDocument()
        expect(screen.getByText("Cleaning")).toBeInTheDocument()
    })

    it("stores the PMS source id the booking API matches on", async () => {
        const user = userEvent.setup()
        const onNodeChange = renderPanel(createNode("booking_link", "b1") as BookingLinkNode)

        await user.click(screen.getByRole("checkbox", { name: /New Patient Exam/ }))

        const calls = onNodeChange.mock.calls
        const patch = calls[calls.length - 1][0] as BookingLinkNode
        expect(patch.appointment_type_ids).toEqual(["101"])
    })

    it("falls back to typing ids when the cache is empty", async () => {
        renderPanel(createNode("booking_link", "b1") as BookingLinkNode, {
            appointmentTypes: [],
        })
        expect(
            await screen.findByPlaceholderText("Leave empty for any type"),
        ).toBeInTheDocument()
    })

    it("does not offer a provider hidden from the voice agent", async () => {
        renderPanel(createNode("booking_link", "b1") as BookingLinkNode)
        expect(screen.queryByText("Hidden Hygienist")).not.toBeInTheDocument()
    })
})

describe("Register Patient config", () => {
    it("offers providers by name so the id is never typed", async () => {
        renderPanel(createNode("patient_registration", "r1") as PatientRegistrationNode)
        expect(
            await screen.findByText(/Choose a provider|Dr. Kadri/),
        ).toBeInTheDocument()
    })

    it("still allows a typed id when no providers are cached", async () => {
        renderPanel(createNode("patient_registration", "r1") as PatientRegistrationNode, {
            providers: [],
        })
        expect(await screen.findByPlaceholderText("PMS provider id")).toBeInTheDocument()
    })
})

describe("Book Appointment config", () => {
    it("falls back to typed provider and type ids when caches are empty", async () => {
        renderPanel(createNode("book_appointment", "book-1") as BookAppointmentNode, {
            appointmentTypes: [],
            providers: [],
        })

        expect(
            await screen.findByPlaceholderText("PMS appointment type id or {{field}}"),
        ).toBeInTheDocument()
        expect(screen.getByPlaceholderText("PMS provider id or {{field}}")).toBeInTheDocument()
        expect(screen.getByPlaceholderText("{{booking_start_time}}")).toBeInTheDocument()
    })

    it("shows the three runtime outcome branches", async () => {
        renderPanel(createNode("book_appointment", "book-1") as BookAppointmentNode, {
            appointmentTypes: [],
            providers: [],
        })

        expect(await screen.findByText("Outcome branches")).toBeInTheDocument()
        expect(screen.getByText("Booked")).toBeInTheDocument()
        expect(screen.getByText("Could not book")).toBeInTheDocument()
        expect(screen.getByText("Pending")).toBeInTheDocument()
    })
})
