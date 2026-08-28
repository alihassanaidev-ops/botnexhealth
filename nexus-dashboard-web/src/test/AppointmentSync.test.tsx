import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

import AppointmentSync from "@/pages/AppointmentSync"
import { listAppointmentSyncStatus } from "@/lib/appointment-sync-api"
import { useInstitution } from "@/context/InstitutionContext"

vi.mock("@/lib/appointment-sync-api", () => ({
    listAppointmentSyncStatus: vi.fn(),
}))
vi.mock("@/context/InstitutionContext", () => ({
    useInstitution: vi.fn(),
}))
vi.mock("sonner", () => ({
    toast: { error: vi.fn() },
}))

const list = listAppointmentSyncStatus as ReturnType<typeof vi.fn>
const institution = useInstitution as ReturnType<typeof vi.fn>

beforeEach(() => {
    list.mockReset()
    institution.mockReset()
    list.mockResolvedValue({ total: 0, limit: 50, offset: 0, items: [] })
})

describe("AppointmentSync", () => {
    it("shows a generic sync view and omits GoTracker filters for NexHealth", async () => {
        institution.mockReturnValue({ pmsType: "nexhealth" })

        render(<AppointmentSync />)

        expect(await screen.findByText("Sync status")).toBeInTheDocument()
        expect(screen.queryByText("GoTracker status")).not.toBeInTheDocument()
        expect(screen.queryByText("All GoTracker statuses")).not.toBeInTheDocument()
        expect(screen.queryByText("Flags")).not.toBeInTheDocument()
        expect(screen.getByText("Appointments synchronized from your PMS will appear here.")).toBeInTheDocument()
        await waitFor(() => {
            expect(list).toHaveBeenCalledWith(expect.not.objectContaining({ gotracker_status_id: expect.anything() }))
        })
    })

    it("shows GoTracker-specific status controls for GoTracker institutions", async () => {
        institution.mockReturnValue({ pmsType: "gotracker" })

        render(<AppointmentSync />)

        expect(await screen.findByText("GoTracker status")).toBeInTheDocument()
        expect(screen.getByText("All GoTracker statuses")).toBeInTheDocument()
        expect(screen.getByText("Flags")).toBeInTheDocument()
    })
})
