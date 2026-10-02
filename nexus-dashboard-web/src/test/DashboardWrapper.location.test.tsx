import { useEffect } from "react"
import { render, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import DashboardWrapper from "@/components/DashboardWrapper"

const { activeLocation, mounted } = vi.hoisted(() => ({
    activeLocation: vi.fn(),
    mounted: vi.fn(),
}))

vi.mock("@/context/AuthContext", () => ({
    useAuth: () => ({ user: { role: "INSTITUTION_ADMIN" }, isLoading: false }),
}))
vi.mock("@/context/LocationContext", () => ({
    useSelectedLocationId: () => activeLocation(),
}))
vi.mock("@/components/app-sidebar", () => ({ AppSidebar: () => null }))
vi.mock("@/components/TopNav", () => ({ TopNav: () => null }))

function PageProbe() {
    useEffect(() => {
        mounted()
    }, [])
    return <div>Location-scoped page</div>
}

beforeEach(() => {
    activeLocation.mockReturnValue("loc-1")
    mounted.mockReset()
})

describe("DashboardWrapper active-location boundary", () => {
    it("remounts every routed page when the active location changes", async () => {
        const tree = () => (
            <MemoryRouter initialEntries={["/"]}>
                <Routes>
                    <Route element={<DashboardWrapper />}>
                        <Route path="/" element={<PageProbe />} />
                    </Route>
                </Routes>
            </MemoryRouter>
        )
        const view = render(tree())

        await waitFor(() => expect(mounted).toHaveBeenCalledTimes(1))

        activeLocation.mockReturnValue("loc-2")
        view.rerender(tree())

        await waitFor(() => expect(mounted).toHaveBeenCalledTimes(2))
    })
})
