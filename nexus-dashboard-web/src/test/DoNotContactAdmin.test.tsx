import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import DoNotContactAdmin from "@/pages/DoNotContactAdmin"
import {
    createDoNotContact,
    listDoNotContact,
    releaseDoNotContact,
} from "@/lib/do-not-contact-api"
import { listInstitutionPortalLocations } from "@/lib/institution-portal-api"

vi.mock("@/lib/do-not-contact-api", () => ({
    createDoNotContact: vi.fn(),
    listDoNotContact: vi.fn(),
    releaseDoNotContact: vi.fn(),
}))
vi.mock("@/lib/institution-portal-api", () => ({
    listInstitutionPortalLocations: vi.fn(),
}))
vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

const list = listDoNotContact as ReturnType<typeof vi.fn>
const create = createDoNotContact as ReturnType<typeof vi.fn>
const release = releaseDoNotContact as ReturnType<typeof vi.fn>
const listLocations = listInstitutionPortalLocations as ReturnType<typeof vi.fn>

beforeEach(() => {
    list.mockReset()
    create.mockReset()
    release.mockReset()
    listLocations.mockReset()
    list.mockResolvedValue([
        {
            id: "contact-1",
            contact_id: "contact-1",
            patient_name: "Jane Patient",
            phone_masked: "+1******1234",
            email_masked: null,
            latest_opt_out_at: "2026-08-25T12:00:00Z",
            channels: [
                {
                    id: "sms-1",
                    channel: "sms",
                    record_type: "sms_suppression",
                    scope: "location",
                    source: "twilio_keyword",
                    reason: "STOP",
                    location_id: "loc-1",
                    created_at: "2026-08-25T12:00:00Z",
                },
                {
                    id: "voice-1",
                    channel: "voice",
                    record_type: "consent_record",
                    scope: "location",
                    source: "system",
                    reason: "voice_spoken_optout",
                    location_id: "loc-1",
                    created_at: "2026-08-25T11:00:00Z",
                },
            ],
        },
    ])
    release.mockResolvedValue(true)
    create.mockResolvedValue({ phone_masked: "+1******1234", scope: "institution" })
    listLocations.mockResolvedValue([{ id: "loc-1", name: "Downtown" }])
})

describe("DNC Patients", () => {
    it("shows per-channel tags and releases only the selected tag", async () => {
        render(<DoNotContactAdmin />)

        expect(await screen.findByText("Jane Patient")).toBeInTheDocument()
        expect(screen.getByText("SMS")).toBeInTheDocument()
        expect(screen.getByText("Voice")).toBeInTheDocument()
        expect(await screen.findAllByText(/Downtown/)).toHaveLength(2)

        const removeSmsButton = screen.getByRole("button", { name: "Remove SMS DNC tag" })
        expect(removeSmsButton).toHaveTextContent("Remove")
        fireEvent.click(removeSmsButton)
        fireEvent.click(screen.getByRole("button", { name: "Remove tag" }))

        await waitFor(() => {
            expect(release).toHaveBeenCalledWith("sms_suppression", "sms-1")
        })
        expect(release).not.toHaveBeenCalledWith("consent_record", "voice-1")
    })
})
