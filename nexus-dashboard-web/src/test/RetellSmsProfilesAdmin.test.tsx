import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { RetellSmsProfilesAdmin } from "@/components/tenants/RetellSmsProfilesAdmin"
import { listRetellAgents, verifyRetellAgent } from "@/lib/admin-api"
import {
    createRetellSmsChatProfile,
    deleteRetellSmsChatProfile,
    listRetellSmsChatProfiles,
    updateRetellSmsChatProfile,
} from "@/lib/retell-sms-api"

vi.mock("@/lib/admin-api", () => ({
    listRetellAgents: vi.fn(),
    verifyRetellAgent: vi.fn(),
}))

vi.mock("@/lib/retell-sms-api", () => ({
    createRetellSmsChatProfile: vi.fn(),
    deleteRetellSmsChatProfile: vi.fn(),
    listRetellSmsChatProfiles: vi.fn(),
    updateRetellSmsChatProfile: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn() },
}))

const listProfiles = listRetellSmsChatProfiles as ReturnType<typeof vi.fn>
const createProfile = createRetellSmsChatProfile as ReturnType<typeof vi.fn>
const updateProfile = updateRetellSmsChatProfile as ReturnType<typeof vi.fn>
const deleteProfile = deleteRetellSmsChatProfile as ReturnType<typeof vi.fn>
const listAgents = listRetellAgents as ReturnType<typeof vi.fn>
const verifyAgent = verifyRetellAgent as ReturnType<typeof vi.fn>

const profile = {
    id: "profile-1",
    institution_id: "institution-1",
    location_id: "location-1",
    retell_agent_id: "agent_existing",
    agent_version: 3,
    display_name: "Appointment helper",
    purpose: "appointment_followup",
    allowed_tools: [],
    is_active: true,
    config: null,
    created_at: "2026-08-25T12:00:00Z",
    updated_at: "2026-08-25T12:00:00Z",
}

beforeEach(() => {
    listProfiles.mockReset()
    createProfile.mockReset()
    updateProfile.mockReset()
    deleteProfile.mockReset()
    listAgents.mockReset()
    verifyAgent.mockReset()

    listProfiles.mockResolvedValue([profile])
    listAgents.mockResolvedValue([
        {
            agent_id: "agent_chat",
            agent_name: "SMS response generator",
            channel: "chat",
            version: 4,
            is_published: true,
        },
    ])
    createProfile.mockResolvedValue(profile)
    updateProfile.mockResolvedValue(profile)
    deleteProfile.mockResolvedValue(undefined)
    verifyAgent.mockResolvedValue({ agent_id: "agent_chat" })
})

describe("RetellSmsProfilesAdmin", () => {
    it("lists a location's profiles and can deactivate one", async () => {
        render(<RetellSmsProfilesAdmin locationId="location-1" />)

        expect(await screen.findByText("Appointment helper")).toBeInTheDocument()
        expect(listProfiles).toHaveBeenCalledWith({ locationId: "location-1" })

        fireEvent.click(screen.getByRole("button", { name: "Deactivate Appointment helper" }))

        await waitFor(() => {
            expect(updateProfile).toHaveBeenCalledWith("profile-1", { is_active: false })
        })
    })

    it("creates a profile with an optional pinned version", async () => {
        listProfiles.mockResolvedValue([])
        render(<RetellSmsProfilesAdmin locationId="location-1" />)

        await screen.findByText("No Retell SMS profiles configured for this location.")
        fireEvent.click(screen.getByRole("button", { name: "Add profile" }))

        fireEvent.change(screen.getByLabelText("Display name"), {
            target: { value: "Recall assistant" },
        })
        fireEvent.change(screen.getByLabelText("Purpose"), {
            target: { value: "recall" },
        })
        fireEvent.change(screen.getByLabelText("Retell agent ID"), {
            target: { value: "agent_chat" },
        })
        fireEvent.change(screen.getByLabelText("Pinned agent version"), {
            target: { value: "4" },
        })

        fireEvent.click(screen.getByRole("button", { name: "Verify" }))
        await waitFor(() => expect(verifyAgent).toHaveBeenCalledWith("agent_chat"))

        fireEvent.click(screen.getByRole("button", { name: "Create profile" }))

        await waitFor(() => {
            expect(createProfile).toHaveBeenCalledWith({
                location_id: "location-1",
                retell_agent_id: "agent_chat",
                agent_version: 4,
                display_name: "Recall assistant",
                purpose: "recall",
                is_active: true,
            })
        })
    })
})
