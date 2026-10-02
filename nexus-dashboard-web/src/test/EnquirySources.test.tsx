import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import EnquirySources from "@/pages/EnquirySources"
import * as api from "@/lib/enquiry-sources-api"

vi.mock("@/lib/enquiry-sources-api", async () => {
    const actual = await vi.importActual<typeof api>("@/lib/enquiry-sources-api")
    return {
        ...actual,
        listEnquirySources: vi.fn(),
        createEnquirySource: vi.fn(),
        updateEnquirySource: vi.fn(),
        rotateEnquirySource: vi.fn(),
    }
})

vi.mock("@/context/LocationContext", () => ({
    useLocationContext: () => ({
        locations: [{ id: "loc-1", name: "Downtown" }],
    }),
}))

const SOURCE = {
    id: "src-1",
    label: "Website form",
    location_id: "loc-1",
    source_name: "external_form",
    is_active: true,
    has_signing_secret: false,
    default_attribution: null,
    created_at: "2026-09-01T10:00:00Z",
    last_used_at: null,
}

const CREATED = {
    ...SOURCE,
    token: "tok_abc123",
    intake_url: "https://staging.example.com/api/enquiries/intake/tok_abc123",
}

describe("EnquirySources", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.listEnquirySources).mockResolvedValue([SOURCE])
        vi.mocked(api.createEnquirySource).mockResolvedValue(CREATED)
        vi.mocked(api.rotateEnquirySource).mockResolvedValue(CREATED)
        vi.mocked(api.updateEnquirySource).mockResolvedValue({ ...SOURCE, is_active: false })
    })

    it("lists the forms a clinic already has", async () => {
        render(<EnquirySources />)
        expect(await screen.findByText("Website form")).toBeInTheDocument()
    })

    it("says when a form last received an enquiry", async () => {
        render(<EnquirySources />)
        // Wording follows the contacts/patients consolidation: an enquiry lands
        // as a contact, so the card says "contact received", not "enquiry".
        expect(await screen.findByText(/Last contact received: never/)).toBeInTheDocument()
    })

    it("will not create a form without a name", async () => {
        render(<EnquirySources />)
        await screen.findByText("Website form")
        expect(screen.getByRole("button", { name: "Create" })).toBeDisabled()
    })

    it("shows the address once, and warns it cannot be shown again", async () => {
        const user = userEvent.setup()
        render(<EnquirySources />)
        await screen.findByText("Website form")

        await user.type(screen.getByLabelText("Name it"), "Typeform")
        await user.click(screen.getByRole("button", { name: "Create" }))

        expect(await screen.findByText(CREATED.intake_url)).toBeInTheDocument()
        expect(screen.getByText(/only time you'll see it/i)).toBeInTheDocument()
    })

    it("never renders the bare token on its own", async () => {
        const user = userEvent.setup()
        render(<EnquirySources />)
        await screen.findByText("Website form")
        await user.type(screen.getByLabelText("Name it"), "Typeform")
        await user.click(screen.getByRole("button", { name: "Create" }))

        await screen.findByText(CREATED.intake_url)
        // The full URL is shown; the token must not also appear loose in a
        // field a screenshot or a copy-paste could scatter.
        const loose = screen.queryAllByText("tok_abc123")
        expect(loose).toHaveLength(0)
    })

    it("switching a form off does not delete it", async () => {
        const user = userEvent.setup()
        render(<EnquirySources />)
        await screen.findByText("Website form")

        await user.click(screen.getByRole("button", { name: "Switch off" }))

        await waitFor(() =>
            expect(api.updateEnquirySource).toHaveBeenCalledWith("src-1", {
                is_active: false,
            }),
        )
    })

    it("rotating reveals a fresh address", async () => {
        const user = userEvent.setup()
        render(<EnquirySources />)
        await screen.findByText("Website form")

        await user.click(screen.getByRole("button", { name: "New address" }))

        await waitFor(() => expect(api.rotateEnquirySource).toHaveBeenCalledWith("src-1"))
        expect(await screen.findByText(CREATED.intake_url)).toBeInTheDocument()
    })

    it("survives the list failing to load", async () => {
        vi.mocked(api.listEnquirySources).mockRejectedValue(new Error("nope"))
        render(<EnquirySources />)
        expect(await screen.findByText(/Couldn't load/i)).toBeInTheDocument()
    })
})
