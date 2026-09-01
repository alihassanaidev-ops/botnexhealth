import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import BookingLink from "@/pages/BookingLink"
import * as api from "@/lib/booking-link-api"

const navigate = vi.fn()
vi.mock("react-router-dom", async () => {
    const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom")
    return { ...actual, useNavigate: () => navigate }
})

vi.mock("@/lib/booking-link-api", async () => {
    const actual = await vi.importActual<typeof api>("@/lib/booking-link-api")
    return {
        ...actual,
        fetchAppointmentTypes: vi.fn(),
        fetchSlots: vi.fn(),
        bookSlot: vi.fn(),
    }
})

const context: api.BookingPageContext = {
    appointment_types: [
        { id: "exam", name: "New patient exam", duration_minutes: 60 },
        { id: "cleaning", name: "Cleaning", duration_minutes: 30 },
    ],
    selection_required: true,
    patient_resolution_required: false,
    registration_available: true,
    identity_required: false,
    clinic_name: "Olive Tree Dental",
    window_days: 14,
}

function renderPage() {
    return render(
        <MemoryRouter initialEntries={["/book/book?token=run-1.book.999.sig"]}>
            <Routes>
                <Route path="/book/:action" element={<BookingLink />} />
            </Routes>
        </MemoryRouter>,
    )
}

describe("BookingLink", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.fetchAppointmentTypes).mockResolvedValue(context)
        vi.mocked(api.fetchSlots).mockResolvedValue({
            slots: [],
            clinic_name: "Olive Tree Dental",
            timezone: "America/Toronto",
            already_booked: false,
        })
    })

    it("does not turn configured appointment types into Any", async () => {
        const user = userEvent.setup()
        renderPage()

        const picker = await screen.findByRole("button", { name: "Appointment type" })
        expect(picker).toHaveTextContent("Choose appointment type")
        expect(api.fetchSlots).not.toHaveBeenCalled()

        await user.click(picker)
        expect(screen.queryByText("Any appointment type")).not.toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: /New patient exam/ }))

        await waitFor(() =>
            expect(api.fetchSlots).toHaveBeenCalledWith(
                "book",
                expect.any(String),
                expect.objectContaining({ appointmentTypeId: "exam" }),
            ),
        )
    })

    it("asks an unresolved contact whether they are new or existing before loading slots", async () => {
        vi.mocked(api.fetchAppointmentTypes).mockResolvedValue({
            ...context,
            patient_resolution_required: true,
        })
        renderPage()

        expect(await screen.findByRole("button", { name: /existing patient/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /new patient/i })).toBeInTheDocument()
        expect(api.fetchSlots).not.toHaveBeenCalled()
    })

    it("sends a new patient through registration and back to booking", async () => {
        vi.mocked(api.fetchAppointmentTypes).mockResolvedValue({
            ...context,
            patient_resolution_required: true,
        })
        const user = userEvent.setup()
        renderPage()

        await user.click(await screen.findByRole("button", { name: /new patient/i }))

        expect(navigate).toHaveBeenCalledWith(
            expect.stringMatching(/\/book\/register\?.*next=%2Fbook%2Fbook/),
        )
    })
})
