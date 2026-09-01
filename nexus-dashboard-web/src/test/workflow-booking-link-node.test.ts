import { describe, expect, it } from "vitest"

import { connectNodes, createNode, outgoing, removeNode } from "@/lib/workflow/graph"
import { NODE_META } from "@/lib/workflow/catalog"
import type { BookAppointmentNode, BookingLinkNode, PatientRegistrationNode, WorkflowDefinition } from "@/types/workflow"

describe("booking_link node", () => {
    it("starts unrestricted so dropping it in changes nothing", () => {
        const node = createNode("booking_link", "b1") as BookingLinkNode
        expect(node.actions).toEqual(["book"])
        expect(node.appointment_type_ids).toEqual([])
        expect(node.window_days).toBe(7)
    })

    it("has a single exit like the other action steps", () => {
        const node = createNode("booking_link", "b1") as BookingLinkNode
        node.next_node_id = "n2"
        expect(outgoing(node)).toEqual([{ targetId: "n2" }])
    })

    it("is offered in the builder's action group", () => {
        expect(NODE_META.booking_link.group).toBe("action")
        expect(NODE_META.booking_link.label).toBe("Booking Link")
    })
})

describe("patient_registration node", () => {
    it("starts with no provider so the author must choose one", () => {
        const node = createNode("patient_registration", "r1") as PatientRegistrationNode
        expect(node.provider_id).toBe("")
    })

    it("has a single exit", () => {
        const node = createNode("patient_registration", "r1") as PatientRegistrationNode
        node.next_node_id = "n2"
        expect(outgoing(node)).toEqual([{ targetId: "n2" }])
    })

    it("is offered in the builder's action group", () => {
        expect(NODE_META.patient_registration.group).toBe("action")
    })
})

describe("book_appointment node", () => {
    it("starts with required scheduling fields empty and explicit outcome branches", () => {
        const node = createNode("book_appointment", "book-1") as BookAppointmentNode
        expect(node.appointment_type_id).toBe("")
        expect(node.provider_id).toBe("")
        expect(node.start_time).toBe("{{booking_start_time}}")
        expect(node.booked_next_node_id).toBe("")
        expect(node.could_not_book_next_node_id).toBe("")
        expect(node.pending_next_node_id).toBe("")
    })

    it("has three named branch ports", () => {
        const node = createNode("book_appointment", "book-1") as BookAppointmentNode
        node.booked_next_node_id = "booked"
        node.could_not_book_next_node_id = "no-slot"
        node.pending_next_node_id = "pending"

        expect(outgoing(node)).toEqual([
            { targetId: "booked", handle: "booked", label: "Booked" },
            { targetId: "no-slot", handle: "could_not_book", label: "Could not book" },
            { targetId: "pending", handle: "pending", label: "Pending" },
        ])
    })

    it("is offered in the builder's action group", () => {
        expect(NODE_META.book_appointment.group).toBe("action")
        expect(NODE_META.book_appointment.label).toBe("Book Appointment")
    })
})

describe("graph edits reach the new nodes", () => {
    function def(): WorkflowDefinition {
        return {
            entry_node_id: "b1",
            nodes: [
                createNode("booking_link", "b1"),
                createNode("patient_registration", "r1"),
                createNode("exit", "e1"),
            ],
        } as unknown as WorkflowDefinition
    }

    it("connects a booking_link to the next step", () => {
        const next = connectNodes(def(), "b1", "e1")
        const node = next.nodes.find((n) => n.id === "b1") as BookingLinkNode
        expect(node.next_node_id).toBe("e1")
    })

    it("repoints around a deleted node instead of dangling", () => {
        let d = connectNodes(def(), "b1", "r1")
        d = connectNodes(d, "r1", "e1")
        const after = removeNode(d, "r1")
        const node = after.nodes.find((n) => n.id === "b1") as BookingLinkNode
        // b1 pointed at r1; with r1 gone it must follow through to e1.
        expect(node.next_node_id).toBe("e1")
    })

    it("connects each book_appointment outcome independently", () => {
        let d: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "book-1",
            nodes: [
                createNode("book_appointment", "book-1"),
                createNode("exit", "booked"),
                createNode("exit", "no-slot"),
                createNode("exit", "pending"),
            ],
        }

        d = connectNodes(d, "book-1", "booked", "booked")
        d = connectNodes(d, "book-1", "no-slot", "could_not_book")
        d = connectNodes(d, "book-1", "pending", "pending")

        const node = d.nodes.find((n) => n.id === "book-1") as BookAppointmentNode
        expect(node.booked_next_node_id).toBe("booked")
        expect(node.could_not_book_next_node_id).toBe("no-slot")
        expect(node.pending_next_node_id).toBe("pending")
    })
})
