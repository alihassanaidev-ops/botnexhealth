import { describe, expect, it, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
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

const list = listCampaigns as ReturnType<typeof vi.fn>
const halt = getOutboundHaltStatus as ReturnType<typeof vi.fn>
const create = createDraftCampaign as ReturnType<typeof vi.fn>
const remove = deleteCampaign as ReturnType<typeof vi.fn>

beforeEach(() => {
    list.mockReset()
    halt.mockReset()
    create.mockReset()
    remove.mockReset()
    list.mockResolvedValue([])
    halt.mockResolvedValue({ halted: false, halted_runs: 0 })
})

describe("Campaigns page", () => {
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
            expect(create).toHaveBeenCalledWith("Untitled campaign")
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
