import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import LeadCapture from "@/pages/LeadCapture"

// Both intake routes are merged onto this page; the panels themselves are
// covered by their own tests, so they are stubbed to keep this about the merge.
vi.mock("@/pages/EnquirySources", () => ({
    default: () => <div>direct-panel</div>,
}))
vi.mock("@/pages/FormIntegrations", () => ({
    default: () => <div>connected-panel</div>,
}))

function renderAt(path: string) {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <LeadCapture />
        </MemoryRouter>,
    )
}

describe("LeadCapture — one page for both intake routes", () => {
    it("opens on connected apps and offers both routes", () => {
        renderAt("/institution-admin/lead-forms")

        expect(screen.getByRole("tab", { name: "Connected apps" })).toBeInTheDocument()
        expect(screen.getByRole("tab", { name: "Your own forms" })).toBeInTheDocument()
        expect(screen.getByText("connected-panel")).toBeInTheDocument()
    })

    it("honours ?tab=direct so the retired Contact forms link lands correctly", () => {
        renderAt("/institution-admin/lead-forms?tab=direct")

        expect(screen.getByText("direct-panel")).toBeInTheDocument()
    })

    it("falls back to connected apps for an unknown tab value", () => {
        renderAt("/institution-admin/lead-forms?tab=nonsense")

        expect(screen.getByText("connected-panel")).toBeInTheDocument()
    })

    it("names the trigger each route fires, so a workflow is not built on the wrong one", async () => {
        renderAt("/institution-admin/lead-forms")

        expect(screen.getByText(/Form submitted/)).toBeInTheDocument()

        await userEvent.click(screen.getByRole("tab", { name: "Your own forms" }))
        expect(screen.getByText(/Enquiry received/)).toBeInTheDocument()
    })
})
