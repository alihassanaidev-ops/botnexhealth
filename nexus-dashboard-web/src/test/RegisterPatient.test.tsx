import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import RegisterPatient from "@/pages/RegisterPatient"
import * as api from "@/lib/booking-link-api"

vi.mock("@/lib/booking-link-api", async () => {
    const actual = await vi.importActual<typeof api>("@/lib/booking-link-api")
    return {
        ...actual,
        fetchRegistrationDetails: vi.fn(),
        registerPatient: vi.fn(),
    }
})

const details = {
    clinic_name: "Olive Tree Dental",
    first_name: "Dana",
    last_name: "Reyes",
    email: "dana@example.com",
    phone: "+15550001111",
    already_registered: false,
}

function renderAt(token = "run-1.register.999.sig") {
    return render(
        <MemoryRouter initialEntries={[`/book/register?token=${token}`]}>
            <RegisterPatient />
        </MemoryRouter>,
    )
}

describe("RegisterPatient", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.fetchRegistrationDetails).mockResolvedValue(details)
        vi.mocked(api.registerPatient).mockResolvedValue({ status: "registered" })
    })

    it("prefills what the campaign already knew", async () => {
        renderAt()
        expect(await screen.findByDisplayValue("Dana")).toBeInTheDocument()
        expect(screen.getByDisplayValue("dana@example.com")).toBeInTheDocument()
        expect(screen.getByDisplayValue("+15550001111")).toBeInTheDocument()
    })

    it("names the clinic so the patient knows who is asking", async () => {
        renderAt()
        expect(await screen.findByText(/Olive Tree Dental/)).toBeInTheDocument()
    })

    it("submits the details the practice software needs", async () => {
        const user = userEvent.setup()
        renderAt()
        await screen.findByDisplayValue("Dana")

        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.click(screen.getByRole("button", { name: "Female" }))
        await user.click(screen.getByRole("button", { name: "Continue" }))

        await waitFor(() => expect(api.registerPatient).toHaveBeenCalledTimes(1))
        const [, body] = vi.mocked(api.registerPatient).mock.calls[0]
        expect(body.date_of_birth).toBe("1988-04-02")
        expect(body.gender).toBe("Female")
        expect(body.email).toBe("dana@example.com")
    })

    it("will not submit without a date of birth", async () => {
        const user = userEvent.setup()
        renderAt()
        await screen.findByDisplayValue("Dana")

        await user.click(screen.getByRole("button", { name: "Female" }))
        await user.click(screen.getByRole("button", { name: "Continue" }))

        expect(api.registerPatient).not.toHaveBeenCalled()
        expect(await screen.findByRole("alert")).toBeInTheDocument()
    })

    it("will not submit without a gender the practice software accepts", async () => {
        const user = userEvent.setup()
        renderAt()
        await screen.findByDisplayValue("Dana")

        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.click(screen.getByRole("button", { name: "Continue" }))

        expect(api.registerPatient).not.toHaveBeenCalled()
    })

    it("shows the done state instead of a form that would be refused", async () => {
        vi.mocked(api.fetchRegistrationDetails).mockResolvedValue({
            ...details,
            already_registered: true,
        })
        renderAt()
        expect(await screen.findByText(/You're all set/)).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument()
    })

    it("tells the patient an expired link expired", async () => {
        vi.mocked(api.fetchRegistrationDetails).mockRejectedValue({
            response: { status: 410, data: { error: "expired" } },
        })
        renderAt()
        expect(await screen.findByText(/expired/i)).toBeInTheDocument()
    })

    it("does not call a withdrawn link expired", async () => {
        vi.mocked(api.fetchRegistrationDetails).mockRejectedValue({
            response: { status: 410, data: { error: "gone" } },
        })
        renderAt()
        expect(await screen.findByText(/no longer active/i)).toBeInTheDocument()
        expect(screen.queryByText(/expired/i)).not.toBeInTheDocument()
    })

    it("never shows why the practice software refused", async () => {
        vi.mocked(api.registerPatient).mockRejectedValue({
            response: { status: 502, data: { error: "could_not_register" } },
        })
        const user = userEvent.setup()
        renderAt()
        await screen.findByDisplayValue("Dana")

        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.click(screen.getByRole("button", { name: "Female" }))
        await user.click(screen.getByRole("button", { name: "Continue" }))

        const msg = await screen.findByText(/clinic will be in touch/i)
        expect(msg).toBeInTheDocument()
        expect(screen.queryByText(/Dana/)).not.toBeInTheDocument()
    })

    it("refuses to call the API without a token", async () => {
        renderAt("")
        await waitFor(() =>
            expect(api.fetchRegistrationDetails).not.toHaveBeenCalled(),
        )
    })
})
