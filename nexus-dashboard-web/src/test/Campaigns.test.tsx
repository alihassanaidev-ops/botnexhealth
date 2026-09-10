import { describe, expect, it, beforeEach, vi } from "vitest"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import Campaigns from "@/pages/Campaigns"
import {
    createDraftCampaign,
    deleteCampaign,
    getOutboundHaltStatus,
    listCampaigns,
} from "@/lib/automation-api"

vi.mock("@/lib/automation-api", () => ({
    activateOutboundHalt: vi.fn(),
    createDraftCampaign: vi.fn(),
    deleteCampaign: vi.fn(),
    getOutboundHaltStatus: vi.fn(),
    listCampaigns: vi.fn(),
    pauseCampaign: vi.fn(),
    releaseOutboundHalt: vi.fn(),
    resumeCampaign: vi.fn(),
}))
vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn() },
}))
const { auth, selectedLocation } = vi.hoisted(() => ({
    auth: vi.fn(),
    selectedLocation: vi.fn(),
}))
vi.mock("@/context/LocationContext", () => ({
    useSelectedLocationId: () => selectedLocation(),
}))
vi.mock("@/context/AuthContext", () => ({
    useAuth: () => auth(),
}))

const list = listCampaigns as ReturnType<typeof vi.fn>
const halt = getOutboundHaltStatus as ReturnType<typeof vi.fn>
const create = createDraftCampaign as ReturnType<typeof vi.fn>
const remove = deleteCampaign as ReturnType<typeof vi.fn>

beforeEach(() => {
    auth.mockReturnValue({ user: { role: "INSTITUTION_ADMIN" } })
    selectedLocation.mockReturnValue("loc-1")
    list.mockReset()
    halt.mockReset()
    create.mockReset()
    remove.mockReset()
    list.mockResolvedValue([])
    halt.mockResolvedValue({ halted: false, halted_runs: 0 })
})

describe("Campaigns page", () => {
    it("does not make an unscoped campaign request while location selection loads", async () => {
        selectedLocation.mockReturnValue(undefined)

        render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                </Routes>
            </MemoryRouter>,
        )

        expect(await screen.findByText("No campaigns yet")).toBeInTheDocument()
        expect(list).not.toHaveBeenCalled()
    })

    it("reloads campaigns when an institution admin switches locations", async () => {
        const view = render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                </Routes>
            </MemoryRouter>,
        )

        await waitFor(() => {
            expect(list).toHaveBeenCalledWith("loc-1")
        })

        selectedLocation.mockReturnValue("loc-2")
        view.rerender(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                </Routes>
            </MemoryRouter>,
        )

        await waitFor(() => {
            expect(list).toHaveBeenCalledWith("loc-2")
        })
    })

    it("ignores a stale campaign response from the previously selected location", async () => {
        const oldCampaigns = [{
            id: "wf-old",
            name: "Old location campaign",
            status: "active",
            trigger_type: "manual",
            definition: null,
            location_id: "loc-1",
            current_version_id: null,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z",
        }]
        const newCampaigns = [{
            ...oldCampaigns[0],
            id: "wf-new",
            name: "New location campaign",
            location_id: "loc-2",
        }]
        let resolveOld!: (campaigns: typeof oldCampaigns) => void
        const oldRequest = new Promise<typeof oldCampaigns>((resolve) => {
            resolveOld = resolve
        })
        list.mockImplementation((locationId: string) =>
            locationId === "loc-1" ? oldRequest : Promise.resolve(newCampaigns),
        )

        const view = render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                </Routes>
            </MemoryRouter>,
        )
        await waitFor(() => expect(list).toHaveBeenCalledWith("loc-1"))

        selectedLocation.mockReturnValue("loc-2")
        view.rerender(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                </Routes>
            </MemoryRouter>,
        )

        expect(await screen.findByText("New location campaign")).toBeInTheDocument()
        await act(async () => resolveOld(oldCampaigns))

        expect(screen.queryByText("Old location campaign")).not.toBeInTheDocument()
        expect(screen.getByText("New location campaign")).toBeInTheDocument()
    })

    it("loads for a location admin without requesting institution halt controls", async () => {
        auth.mockReturnValue({ user: { role: "LOCATION_ADMIN", location_id: "loc-1" } })

        render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                </Routes>
            </MemoryRouter>,
        )

        expect(await screen.findByText("No campaigns yet")).toBeInTheDocument()
        expect(halt).not.toHaveBeenCalled()
        expect(screen.queryByRole("button", { name: /outbound/i })).not.toBeInTheDocument()
    })

    it("creates a scratch campaign draft and opens the builder", async () => {
        create.mockResolvedValue({ id: "wf-scratch", status: "draft" })

        render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                    <Route
                        path="/institution-admin/campaigns/:id/builder"
                        element={<div>Builder opened</div>}
                    />
                </Routes>
            </MemoryRouter>,
        )

        await screen.findByText("No campaigns yet")
        await userEvent.click(screen.getByRole("button", { name: /create from scratch/i }))

        await waitFor(() => {
            expect(create).toHaveBeenCalledWith("Untitled campaign", "loc-1")
        })
        expect(await screen.findByText("Builder opened")).toBeInTheDocument()
    })

    it("deletes a campaign after confirmation", async () => {
        list.mockResolvedValue([
            {
                id: "wf-delete",
                name: "Delete me",
                status: "paused",
                trigger_type: "manual",
                definition: null,
                location_id: null,
                current_version_id: null,
                created_at: "2026-07-01T00:00:00Z",
                updated_at: "2026-07-01T00:00:00Z",
            },
        ])
        remove.mockResolvedValue(undefined)

        render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns" element={<Campaigns />} />
                    <Route
                        path="/institution-admin/campaigns/:id/builder"
                        element={<div>Builder opened</div>}
                    />
                </Routes>
            </MemoryRouter>,
        )

        expect(await screen.findByText("Delete me")).toBeInTheDocument()
        await userEvent.click(screen.getByRole("button", { name: "Delete Delete me" }))
        await userEvent.click(screen.getByRole("button", { name: "Delete campaign" }))

        await waitFor(() => {
            expect(remove).toHaveBeenCalledWith("wf-delete")
        })
        expect(screen.queryByText("Delete me")).not.toBeInTheDocument()
    })
})
