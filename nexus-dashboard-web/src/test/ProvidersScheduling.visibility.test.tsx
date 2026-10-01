/**
 * Pins the "Voice Agent Visibility" control on Providers & Scheduling.
 *
 * This exists because the control was lost once already: the commit that added
 * `is_hidden` was cherry-picked to staging with only its dropdown label, so the
 * backend accepted the flag while no UI could set it. The assertions below are
 * deliberately about the PATCH payload rather than the markup — a checkbox that
 * renders but never reaches `updateProvider` is the exact regression that shipped.
 */

import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import ProvidersScheduling from "@/pages/ProvidersScheduling"
import { AuthProvider } from "@/context/AuthContext"
import { LocationProvider } from "@/context/LocationContext"
import api from "@/lib/api"
import type { User } from "@/types"

vi.mock("@/lib/api", () => ({
    refreshBackendToken: vi.fn(),
    default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock("@/lib/token-manager", () => ({
    getAccessToken: () => "fake-token",
    setAccessToken: vi.fn(),
    clearAccessToken: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn() },
    Toaster: () => null,
}))

const LOCATION = { id: "11111111-1111-1111-1111-111111111111", name: "Downtown", slug: "downtown" }
const APPT_TYPE = { id: "at-uuid", source_id: "t1", name: "Cleaning", duration_minutes: 30 }
const OPERATORY = { id: "op-uuid", source_id: "op1", name: "Room 1" }

const USER: User = {
    id: "u",
    email: "admin@clinic.com",
    full_name: "Admin",
    role: "INSTITUTION_ADMIN",
    institution_id: "inst",
    location_id: undefined,
    is_active: true,
    is_email_verified: true,
    provisional_password_set: false,
    mfa_enrolled: false,
} as User

function provider(isHidden: boolean) {
    return {
        id: "prov-uuid",
        source_id: "nh-1",
        name: "Dr Kadri",
        first_name: "A",
        last_name: "Kadri",
        is_active: true,
        is_hidden: isHidden,
        buffer_minutes: 0,
        same_day_cutoff_time: null,
        min_age: null,
        max_age: null,
    }
}

function mountWith(isHidden: boolean) {
    const apiGet = api.get as ReturnType<typeof vi.fn>
    apiGet.mockImplementation((url: string) => {
        if (url === "/auth/users/me") return Promise.resolve({ data: USER })
        if (url.startsWith("/institution/setup/locations")) return Promise.resolve({ data: [LOCATION] })
        if (url.startsWith("/institution/setup/providers")) return Promise.resolve({ data: [provider(isHidden)] })
        if (url.startsWith("/institution/setup/appointment-types")) return Promise.resolve({ data: [APPT_TYPE] })
        if (url.startsWith("/institution/setup/operatories")) return Promise.resolve({ data: [OPERATORY] })
        if (url.startsWith("/institution/setup/overview"))
            return Promise.resolve({
                data: {
                    can_link_availability: true,
                    can_create_work_windows: true,
                    can_clear_working_window_override: false,
                },
            })
        return Promise.resolve({ data: [] })
    })

    return render(
        <MemoryRouter>
            <AuthProvider>
                <LocationProvider>
                    <ProvidersScheduling />
                </LocationProvider>
            </AuthProvider>
        </MemoryRouter>
    )
}

const checkbox = () => screen.getByRole("checkbox", { name: /hide this provider from the voice agent/i })
const saveButton = () => screen.getByRole("button", { name: /save settings/i })

describe("Providers & Scheduling — Voice Agent Visibility", () => {
    beforeAll(() => {
        // Radix primitives need pointer APIs jsdom lacks.
        if (!Element.prototype.hasPointerCapture) {
            Element.prototype.hasPointerCapture = () => false
            Element.prototype.setPointerCapture = () => {}
            Element.prototype.releasePointerCapture = () => {}
        }
        if (!Element.prototype.scrollIntoView) {
            Element.prototype.scrollIntoView = () => {}
        }
    })

    beforeEach(() => {
        vi.clearAllMocks()
        ;(api.patch as ReturnType<typeof vi.fn>).mockImplementation((_url: string, body: unknown) =>
            Promise.resolve({ data: { ...provider(false), ...(body as object) } })
        )
    })

    it("renders the control unchecked for a visible provider", async () => {
        mountWith(false)
        await waitFor(() => expect(checkbox()).toBeTruthy())
        expect(checkbox()).not.toBeChecked()
    })

    it("reflects a provider that is already hidden", async () => {
        mountWith(true)
        await waitFor(() => expect(checkbox()).toBeChecked())
    })

    it("sends is_hidden in the PATCH when the box is ticked", async () => {
        const user = userEvent.setup()
        mountWith(false)
        await waitFor(() => expect(checkbox()).toBeTruthy())

        // Save stays inert until something actually changes.
        expect(saveButton()).toBeDisabled()

        await user.click(checkbox())
        await waitFor(() => expect(saveButton()).toBeEnabled())
        await user.click(saveButton())

        const patch = api.patch as ReturnType<typeof vi.fn>
        await waitFor(() => expect(patch).toHaveBeenCalled())
        const [url, body] = patch.mock.calls[0]
        expect(String(url)).toContain("/institution/setup/providers/prov-uuid")
        expect(body).toMatchObject({ is_hidden: true })
    })

    it("can un-hide a provider that was hidden", async () => {
        const user = userEvent.setup()
        mountWith(true)
        await waitFor(() => expect(checkbox()).toBeChecked())

        await user.click(checkbox())
        await waitFor(() => expect(saveButton()).toBeEnabled())
        await user.click(saveButton())

        const patch = api.patch as ReturnType<typeof vi.fn>
        await waitFor(() => expect(patch).toHaveBeenCalled())
        expect(patch.mock.calls[0][1]).toMatchObject({ is_hidden: false })
    })
})
