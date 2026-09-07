import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import { AppSidebar } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"

vi.mock("@/context/AuthContext", () => ({
    useAuth: () => ({ user: { role: "INSTITUTION_ADMIN" } }),
}))

vi.mock("@/context/InstitutionContext", () => ({
    useInstitution: () => ({ hasPms: true, pmsType: "nexhealth" }),
}))

vi.mock("@/components/location-selector", () => ({
    LocationSelector: () => (
        <div data-testid="location-selector">Olive Tree Dental</div>
    ),
}))

describe("AppSidebar collapsed layout", () => {
    it("hides the location selector instead of clipping its name", () => {
        render(
            <MemoryRouter>
                <SidebarProvider defaultOpen={false}>
                    <AppSidebar />
                </SidebarProvider>
            </MemoryRouter>,
        )

        expect(screen.getByTestId("location-selector").closest("[data-sidebar=group]"))
            .toHaveClass("group-data-[collapsible=icon]:hidden")
    })

    it("reserves a small right gutter beside collapsed navigation icons", () => {
        const { container } = render(
            <MemoryRouter>
                <SidebarProvider defaultOpen={false}>
                    <AppSidebar />
                </SidebarProvider>
            </MemoryRouter>,
        )

        expect(container.querySelector('[style*="--sidebar-width-icon"]'))
            .toHaveStyle("--sidebar-width-icon: 3.25rem")
    })
})
