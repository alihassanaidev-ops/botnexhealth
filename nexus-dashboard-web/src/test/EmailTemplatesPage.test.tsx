import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import EmailTemplatesPage from "@/pages/EmailTemplatesPage"

vi.mock("@/pages/EmailTemplates", () => ({ default: () => <div>staff-panel</div> }))
vi.mock("@/pages/CampaignEmailTemplates", () => ({ default: () => <div>campaign-panel</div> }))

const role = vi.hoisted(() => ({ current: "INSTITUTION_ADMIN" as string }))
vi.mock("@/context/AuthContext", () => ({
    useAuth: () => ({ user: { role: role.current } }),
}))

function renderAs(userRole: string, path = "/institution-admin/email-templates") {
    role.current = userRole
    return render(
        <MemoryRouter initialEntries={[path]}>
            <EmailTemplatesPage />
        </MemoryRouter>,
    )
}

describe("EmailTemplatesPage — staff and campaign templates on one page", () => {
    it("opens an institution admin on the staff templates", () => {
        renderAs("INSTITUTION_ADMIN")

        expect(screen.getByRole("tab", { name: "To your team" })).toBeInTheDocument()
        expect(screen.getByRole("tab", { name: "To patients" })).toBeInTheDocument()
        expect(screen.getByText("staff-panel")).toBeInTheDocument()
    })

    it("switches to the campaign templates", async () => {
        const user = userEvent.setup()
        renderAs("INSTITUTION_ADMIN")

        await user.click(screen.getByRole("tab", { name: "To patients" }))
        expect(screen.getByText("campaign-panel")).toBeInTheDocument()
    })

    it("honours ?tab=campaign so the retired campaign path lands correctly", () => {
        renderAs("INSTITUTION_ADMIN", "/institution-admin/email-templates?tab=campaign")

        expect(screen.getByText("campaign-panel")).toBeInTheDocument()
    })

    it("never shows a super admin the staff templates the merge did not grant them", () => {
        // Staff templates were INSTITUTION_ADMIN-only before the merge; sharing a
        // route must not widen that.
        renderAs("SUPER_ADMIN")

        expect(screen.queryByRole("tab", { name: "To your team" })).not.toBeInTheDocument()
        expect(screen.queryByText("staff-panel")).not.toBeInTheDocument()
        expect(screen.getByText("campaign-panel")).toBeInTheDocument()
    })

    it("keeps a super admin on campaign templates even if the URL asks for staff", () => {
        renderAs("SUPER_ADMIN", "/institution-admin/email-templates?tab=staff")

        expect(screen.queryByText("staff-panel")).not.toBeInTheDocument()
        expect(screen.getByText("campaign-panel")).toBeInTheDocument()
    })
})
