import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import Contacts from "@/pages/Contacts"
import Patients from "@/pages/Patients"
import * as api from "@/lib/contacts-api"

vi.mock("@/lib/contacts-api", async () => {
    const actual = await vi.importActual<typeof api>("@/lib/contacts-api")
    return {
        ...actual,
        listContacts: vi.fn(),
        listLivePatients: vi.fn(),
        getContact: vi.fn(),
        createContact: vi.fn(),
        updateContact: vi.fn(),
        mergeContact: vi.fn(),
        unmergeContact: vi.fn(),
    }
})

vi.mock("@/context/AuthContext", () => ({
    useAuth: () => ({ user: { role: "INSTITUTION_ADMIN" } }),
}))

vi.mock("@/context/InstitutionContext", () => ({
    useInstitution: () => ({ pmsType: "nexhealth", hasPms: true, isLoading: false }),
}))

vi.mock("@/context/LocationContext", () => ({
    useSelectedLocationId: () => "loc-1",
}))

const CONTACT: api.ContactListItem = {
    id: "contact-1",
    full_name: "Dana Reyes",
    first_name: "Dana",
    last_name: "Reyes",
    is_new_patient: true,
    lifecycle: "lead",
    lead_status: "new",
    source: "manual",
    email_masked: "d***@example.com",
    has_notes: false,
    pms_last_synced_at: null,
    phone_masked: "+*******1234",
    phone_reveal_available: false,
    call_count: 0,
    last_call_at: null,
    alias_count: 0,
    created_at: "2026-09-01T10:00:00Z",
}

const DETAIL: api.ContactDetail = {
    ...CONTACT,
    notes: null,
    aliases: [],
    calls: [],
}

describe("Contacts and Patients directories", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.listContacts).mockResolvedValue({
            total: 1,
            limit: 25,
            offset: 0,
            items: [CONTACT],
        })
        vi.mocked(api.listLivePatients).mockResolvedValue({
            source: "nexhealth",
            fetched_at: "2026-09-01T10:00:00Z",
            total: 1,
            returned: 1,
            items: [{
                pms_patient_id: "nh-42",
                source: "nexhealth",
                first_name: "Dana",
                last_name: "Reyes",
                full_name: "Dana Reyes",
                inactive: false,
                email: null,
                phone: null,
                email_masked: "d***@example.com",
                phone_masked: "+*******1234",
                contact_details_masked: true,
                can_reveal_contact_details: true,
                pms_updated_at: "2026-09-01T09:55:00Z",
                pms_last_sync_time: "2026-09-01T09:55:00Z",
                contact_id: "contact-1",
            }],
            next_cursor: null,
            previous_cursor: null,
            has_next_page: false,
            has_previous_page: false,
        })
        vi.mocked(api.getContact).mockResolvedValue(DETAIL)
        vi.mocked(api.createContact).mockResolvedValue({
            contact: DETAIL,
            created: true,
            matched_existing_patient: false,
        })
    })

    it("uses the non-PMS projection for Contacts", async () => {
        render(<Contacts />)
        expect(await screen.findByText("Dana Reyes")).toBeInTheDocument()
        await waitFor(() => expect(api.listContacts).toHaveBeenCalledWith(
            expect.objectContaining({ directory: "contacts" }),
        ))
        expect(screen.getByText("Lead")).toBeInTheDocument()
    })

    it("reads the Patients directory directly from the selected PMS location", async () => {
        render(<Patients />)
        await screen.findByText("Dana Reyes")
        await waitFor(() => expect(api.listLivePatients).toHaveBeenCalledWith(
            expect.objectContaining({
                locationId: "loc-1",
                pageSize: 25,
                patientStatus: "active",
            }),
        ))
        expect(screen.getByText(/read securely from nexhealth/i)).toBeInTheDocument()
        expect(screen.getAllByText("Active")).toHaveLength(2)
        expect(screen.getByRole("button", { name: /reveal/i })).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: /add contact/i })).not.toBeInTheDocument()
    })

    it("reveals one institution-admin patient through a bounded page read", async () => {
        const user = userEvent.setup()
        render(<Patients />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: /reveal/i }))

        await waitFor(() => expect(api.listLivePatients).toHaveBeenLastCalledWith(
            expect.objectContaining({
                locationId: "loc-1",
                revealPatientId: "nh-42",
            }),
        ))
    })

    it("creates a contact in the selected location with explicit consent", async () => {
        const user = userEvent.setup()
        render(<Contacts />)
        await screen.findByText("Dana Reyes")

        await user.click(screen.getByRole("button", { name: /add contact/i }))
        await user.type(screen.getByLabelText("Phone"), "+15054821234")
        await user.click(screen.getByLabelText(/receive text messages/i))
        await user.click(screen.getByLabelText(/receive email/i))
        await user.click(screen.getByRole("button", { name: "Add contact" }))

        await waitFor(() => expect(api.createContact).toHaveBeenCalledWith(
            expect.objectContaining({
                phone: "+15054821234",
                location_id: "loc-1",
                consent_sms: true,
                consent_email: true,
            }),
        ))
    })
})
