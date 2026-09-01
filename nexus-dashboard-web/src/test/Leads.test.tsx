import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import Leads from "@/pages/Leads"
import * as api from "@/lib/enquiries-api"

vi.mock("@/lib/enquiries-api", async () => {
    const actual = await vi.importActual<typeof api>("@/lib/enquiries-api")
    return {
        ...actual,
        listEnquiries: vi.fn(),
        getEnquiry: vi.fn(),
        updateEnquiry: vi.fn(),
        createEnquiry: vi.fn(),
    }
})

const LEAD = {
    id: "e1",
    first_name: "Dana",
    last_name: "Reyes",
    phone_masked: "+*******1234",
    email_masked: "d***@example.com",
    status: "new",
    stage: "lead" as const,
    source: "typeform",
    contact_id: null,
    has_notes: false,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
}

const PHONE_ONLY = { ...LEAD, id: "e2", first_name: null, last_name: null }

const DETAIL = { ...LEAD, notes: null, attribution: null, external_ref: null, intake_key: "k", location_id: null }

describe("Leads", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.listEnquiries).mockResolvedValue({
            items: [LEAD], total: 1, limit: 50, offset: 0,
        })
        vi.mocked(api.getEnquiry).mockResolvedValue(DETAIL)
        vi.mocked(api.updateEnquiry).mockResolvedValue({ ...DETAIL, notes: "Rang twice" })
        vi.mocked(api.createEnquiry).mockResolvedValue({
            enquiry: DETAIL, created: true, matched_existing_contact: false,
        })
    })

    it("lists the leads that landed", async () => {
        render(<Leads />)
        expect(await screen.findByText("Dana Reyes")).toBeInTheDocument()
    })

    it("shows the stage a clinic cares about", async () => {
        render(<Leads />)
        expect(await screen.findByText("New")).toBeInTheDocument()
    })

    it("falls back to a phone number when a lead has no name", async () => {
        vi.mocked(api.listEnquiries).mockResolvedValue({
            items: [PHONE_ONLY], total: 1, limit: 50, offset: 0,
        })
        render(<Leads />)
        // Only a number arrived; the row must still be identifiable.
        expect(await screen.findByText("+*******1234")).toBeInTheDocument()
    })

    it("never renders an unmasked phone or email", async () => {
        render(<Leads />)
        await screen.findByText("Dana Reyes")
        expect(screen.queryByText(/5054821234/)).not.toBeInTheDocument()
        expect(screen.queryByText("dana@example.com")).not.toBeInTheDocument()
    })

    it("filters by stage", async () => {
        const user = userEvent.setup()
        render(<Leads />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: "Registered" }))

        await waitFor(() =>
            expect(api.listEnquiries).toHaveBeenLastCalledWith(
                expect.objectContaining({ stage: "registered" }),
            ),
        )
    })

    it("searches on demand rather than on every keystroke", async () => {
        const user = userEvent.setup()
        render(<Leads />)
        await screen.findByText("Dana Reyes")
        const before = vi.mocked(api.listEnquiries).mock.calls.length

        await user.type(screen.getByPlaceholderText("Name, phone or email"), "dana")
        expect(vi.mocked(api.listEnquiries).mock.calls.length).toBe(before)

        await user.click(screen.getByRole("button", { name: "Search" }))
        await waitFor(() =>
            expect(api.listEnquiries).toHaveBeenLastCalledWith(
                expect.objectContaining({ search: "dana" }),
            ),
        )
    })

    it("opens a lead and saves a note", async () => {
        const user = userEvent.setup()
        render(<Leads />)
        await user.click(await screen.findByText("Dana Reyes"))

        await screen.findByLabelText("Notes")
        await user.type(screen.getByLabelText("Notes"), "Rang twice")
        await user.click(screen.getByRole("button", { name: "Save notes" }))

        await waitFor(() =>
            expect(api.updateEnquiry).toHaveBeenCalledWith("e1", { notes: "Rang twice" }),
        )
    })

    it("says where to look once a lead has registered", async () => {
        vi.mocked(api.getEnquiry).mockResolvedValue({ ...DETAIL, contact_id: "c-1" })
        const user = userEvent.setup()
        render(<Leads />)
        await user.click(await screen.findByText("Dana Reyes"))

        expect(await screen.findByText(/patient record/i)).toBeInTheDocument()
    })

    it("survives the list failing", async () => {
        vi.mocked(api.listEnquiries).mockRejectedValue(new Error("nope"))
        render(<Leads />)
        expect(await screen.findByText(/Couldn't load/i)).toBeInTheDocument()
    })

    it("says so when there is nothing yet", async () => {
        vi.mocked(api.listEnquiries).mockResolvedValue({
            items: [], total: 0, limit: 50, offset: 0,
        })
        render(<Leads />)
        expect(await screen.findByText(/No enquiries yet/i)).toBeInTheDocument()
    })

    it("can enter an enquiry by hand", async () => {
        const user = userEvent.setup()
        render(<Leads />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: "Add enquiry" }))
        await user.type(screen.getByLabelText("Phone"), "+15054821234")
        await user.click(screen.getByRole("button", { name: "Save enquiry" }))

        await waitFor(() =>
            expect(api.createEnquiry).toHaveBeenCalledWith(
                expect.objectContaining({ phone: "+15054821234" }),
            ),
        )
    })

    it("will not save someone with no way to reach them", async () => {
        const user = userEvent.setup()
        render(<Leads />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: "Add enquiry" }))
        await user.type(screen.getByLabelText("First name"), "Dana")
        await user.click(screen.getByRole("button", { name: "Save enquiry" }))

        expect(api.createEnquiry).not.toHaveBeenCalled()
        expect(await screen.findByRole("alert")).toBeInTheDocument()
    })

    it("does not claim consent that was not ticked", async () => {
        const user = userEvent.setup()
        render(<Leads />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: "Add enquiry" }))
        await user.type(screen.getByLabelText("Phone"), "+15054821234")
        await user.click(screen.getByRole("button", { name: "Save enquiry" }))

        await waitFor(() => expect(api.createEnquiry).toHaveBeenCalled())
        const body = vi.mocked(api.createEnquiry).mock.calls[0][0]
        expect(body.consent_sms).toBe(false)
        expect(body.consent_wording).toBeUndefined()
    })

    it("says so when the person was already on the list", async () => {
        vi.mocked(api.createEnquiry).mockResolvedValue({
            enquiry: DETAIL, created: false, matched_existing_contact: false,
        })
        const user = userEvent.setup()
        render(<Leads />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: "Add enquiry" }))
        await user.type(screen.getByLabelText("Phone"), "+15054821234")
        await user.click(screen.getByRole("button", { name: "Save enquiry" }))

        expect(await screen.findByText(/already on your list/i)).toBeInTheDocument()
    })
})
