import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import AdminUserManagement from "@/pages/AdminUserManagement"
import {
    inviteAdminUser,
    listAdminInstitutionLocations,
    listAdminUsers,
    listInstitutionsDetailed,
    updateAdminUser,
} from "@/lib/admin-api"

vi.mock("@/lib/admin-api", () => ({
    inviteAdminUser: vi.fn(),
    listAdminInstitutionLocations: vi.fn(),
    listAdminUsers: vi.fn(),
    listInstitutionsDetailed: vi.fn(),
    removeAdminUser: vi.fn(),
    reinviteAdminUser: vi.fn(),
    updateAdminUser: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn() },
}))

const listUsers = listAdminUsers as ReturnType<typeof vi.fn>
const listInstitutions = listInstitutionsDetailed as ReturnType<typeof vi.fn>
const listLocations = listAdminInstitutionLocations as ReturnType<typeof vi.fn>
const inviteUser = inviteAdminUser as ReturnType<typeof vi.fn>
const updateUser = updateAdminUser as ReturnType<typeof vi.fn>

const institution = {
    id: "institution-1",
    name: "Downtown Dental",
    slug: "downtown-dental",
    is_active: true,
    has_nexhealth_key: false,
    has_system_nexhealth_key: false,
    has_gotracker_key: false,
    has_retell_secret: false,
    user: null,
}

const location = {
    id: "location-1",
    institution_id: institution.id,
    name: "Main Street",
    slug: "main-street",
    is_active: true,
}

const institutionAdmin = {
    id: "user-1",
    email: "admin@downtown.test",
    role: "INSTITUTION_ADMIN",
    is_active: true,
    invite_status: "ACCEPTED",
    deleted_at: null,
    institution_id: institution.id,
    institution_name: institution.name,
    institution_slug: institution.slug,
    location_id: null,
    location_name: null,
    location_slug: null,
}

beforeEach(() => {
    listUsers.mockReset()
    listInstitutions.mockReset()
    listLocations.mockReset()
    inviteUser.mockReset()
    updateUser.mockReset()

    listUsers.mockResolvedValue({ items: [], total: 0, page: 1, size: 50, pages: 0 })
    listInstitutions.mockResolvedValue([institution])
    listLocations.mockResolvedValue([location])
    inviteUser.mockResolvedValue({ user_id: "new-user" })
    updateUser.mockResolvedValue({ user_id: institutionAdmin.id })
})

describe("AdminUserManagement", () => {
    it("invites a location admin with the selected institution and location", async () => {
        render(<AdminUserManagement />)

        fireEvent.click(await screen.findByRole("button", { name: "Add User" }))
        fireEvent.change(screen.getByLabelText("Email"), {
            target: { value: "location.admin@downtown.test" },
        })

        fireEvent.click(screen.getByLabelText("Role"))
        fireEvent.click(await screen.findByRole("option", { name: "Location Admin" }))

        await waitFor(() => expect(listLocations).toHaveBeenCalledWith(institution.slug))
        fireEvent.click(screen.getByLabelText("Location"))
        fireEvent.click(await screen.findByRole("option", { name: location.name }))
        fireEvent.click(screen.getByRole("button", { name: "Send Invite" }))

        await waitFor(() => {
            expect(inviteUser).toHaveBeenCalledWith({
                email: "location.admin@downtown.test",
                role: "LOCATION_ADMIN",
                institution_id: institution.id,
                location_id: location.id,
            })
        })
    })

    it("edits an existing institution admin", async () => {
        listUsers.mockResolvedValue({
            items: [institutionAdmin],
            total: 1,
            page: 1,
            size: 50,
            pages: 1,
        })
        render(<AdminUserManagement />)

        fireEvent.click(await screen.findByRole("button", { name: `Edit ${institutionAdmin.email}` }))
        fireEvent.change(screen.getByLabelText("Email"), {
            target: { value: "updated.admin@downtown.test" },
        })
        fireEvent.click(screen.getByRole("button", { name: "Save Changes" }))

        await waitFor(() => {
            expect(updateUser).toHaveBeenCalledWith(institutionAdmin.id, {
                email: "updated.admin@downtown.test",
                role: "INSTITUTION_ADMIN",
                institution_id: institution.id,
                location_id: undefined,
            })
        })
    })
})
