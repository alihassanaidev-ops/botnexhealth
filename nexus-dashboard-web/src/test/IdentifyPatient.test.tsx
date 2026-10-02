import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import IdentifyPatient from "@/pages/IdentifyPatient"
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
        fetchIdentityContext: vi.fn(),
        identifyPatient: vi.fn(),
    }
})

const CTX = {
    clinic_name: "Olive Tree Dental",
    arrived_by: "sms" as const,
    verified: false,
    attempts_remaining: 5,
}

function renderPage(token = "run-1.book.999.sig") {
    return render(
        <MemoryRouter initialEntries={[`/book/identify?token=${token}&next=/book/cancel`]}>
            <IdentifyPatient />
        </MemoryRouter>,
    )
}

async function openTheForm(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: /existing patient/i }))
}

describe("IdentifyPatient", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.fetchIdentityContext).mockResolvedValue(CTX)
        vi.mocked(api.identifyPatient).mockResolvedValue({ status: "verified" })
    })

    it("asks new or existing rather than guessing", async () => {
        renderPage()
        expect(await screen.findByRole("button", { name: /existing patient/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /new patient/i })).toBeInTheDocument()
    })

    it("sends a new patient to registration", async () => {
        const user = userEvent.setup()
        renderPage()
        await user.click(await screen.findByRole("button", { name: /new patient/i }))
        expect(navigate).toHaveBeenCalledWith(
            expect.stringMatching(/\/book\/register\?.*next=%2Fbook%2Fcancel/),
        )
    })

    it("prefills nothing the patient is meant to be proving", async () => {
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        expect((screen.getByLabelText("Full name") as HTMLInputElement).value).toBe("")
        expect((screen.getByLabelText("Phone number") as HTMLInputElement).value).toBe("")
    })

    it("does not search until Next is pressed", async () => {
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        await user.type(screen.getByLabelText("Full name"), "Dana Reyes")
        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        expect(api.identifyPatient).not.toHaveBeenCalled()
    })

    it("sends everything supplied in one request", async () => {
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        await user.type(screen.getByLabelText("Full name"), "Dana Reyes")
        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.type(screen.getByLabelText("Phone number"), "5054821234")
        await user.type(screen.getByLabelText("Email (optional)"), "dana@example.com")
        await user.click(screen.getByRole("button", { name: "Next" }))

        await waitFor(() => expect(api.identifyPatient).toHaveBeenCalledTimes(1))
        const [, body] = vi.mocked(api.identifyPatient).mock.calls[0]
        expect(body.full_name).toBe("Dana Reyes")
        expect(body.email).toBe("dana@example.com")
    })

    it("continues to the action once verified", async () => {
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        await user.type(screen.getByLabelText("Full name"), "Dana Reyes")
        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.type(screen.getByLabelText("Phone number"), "5054821234")
        await user.click(screen.getByRole("button", { name: "Next" }))

        await waitFor(() =>
            expect(navigate).toHaveBeenCalledWith(
                expect.stringContaining("/book/cancel"),
                expect.anything(),
            ),
        )
    })

    it("shows the server's neutral message and lets them try again", async () => {
        vi.mocked(api.identifyPatient).mockResolvedValue({
            status: "not_matched",
            message: "We couldn't match those details.",
            attempts_remaining: 3,
        })
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        await user.type(screen.getByLabelText("Full name"), "Dana Reyes")
        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.type(screen.getByLabelText("Phone number"), "5054821234")
        await user.click(screen.getByRole("button", { name: "Next" }))

        expect(await screen.findByRole("alert")).toHaveTextContent("couldn't match")
        expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument()
    })

    it("stops asking once attempts run out", async () => {
        vi.mocked(api.identifyPatient).mockResolvedValue({
            status: "locked",
            message: "Please call the clinic and we'll sort it out.",
        })
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        await user.type(screen.getByLabelText("Full name"), "Dana Reyes")
        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.type(screen.getByLabelText("Phone number"), "5054821234")
        await user.click(screen.getByRole("button", { name: "Next" }))

        expect(await screen.findByText(/call the clinic/i)).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument()
    })

    it("will not submit without a second factor", async () => {
        const user = userEvent.setup()
        renderPage()
        await openTheForm(user)
        await user.type(screen.getByLabelText("Full name"), "Dana Reyes")
        await user.type(screen.getByLabelText("Date of birth"), "1988-04-02")
        await user.click(screen.getByRole("button", { name: "Next" }))

        expect(api.identifyPatient).not.toHaveBeenCalled()
        expect(await screen.findByRole("alert")).toBeInTheDocument()
    })
})
