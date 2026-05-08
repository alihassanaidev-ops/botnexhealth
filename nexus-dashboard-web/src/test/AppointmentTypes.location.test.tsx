/**
 * Real integration test: verifies the bug is fixed end-to-end.
 *
 * Before this PR, AppointmentTypes called listAppointmentTypes() with
 * no location — the backend then 400'd for multi-location institutions
 * (commit ad223bb). This test asserts that with a LocationProvider in
 * the tree, the page actually calls the GET endpoint with
 * `?location_id=<selected>` baked into the URL.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import AppointmentTypes from "@/pages/AppointmentTypes"
import { AuthProvider } from "@/context/AuthContext"
import { LocationProvider } from "@/context/LocationContext"
import api from "@/lib/api"
import type { User } from "@/types"

vi.mock("@/lib/api", () => ({
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

const LOC_A = { id: "11111111-1111-1111-1111-111111111111", name: "Downtown", slug: "downtown" }
const LOC_B = { id: "22222222-2222-2222-2222-222222222222", name: "Uptown", slug: "uptown" }

function makeUser(role: User["role"], location_id: string | null = null): User {
    return {
        id: "u",
        email: "x@y.com",
        full_name: "X",
        role,
        institution_id: "inst",
        location_id,
        is_active: true,
        is_email_verified: true,
        provisional_password_set: false,
        mfa_enrolled: false,
    } as User
}

beforeEach(() => {
    localStorage.clear()
    ;(api.get as ReturnType<typeof vi.fn>).mockReset()
})

describe("AppointmentTypes page threading location_id from context", () => {
    it("INSTITUTION_ADMIN with multi-location: GETs appointment-types with the selected location_id", async () => {
        const user = makeUser("INSTITUTION_ADMIN")
        const apiGet = api.get as ReturnType<typeof vi.fn>
        const calls: string[] = []
        apiGet.mockImplementation((url: string) => {
            calls.push(url)
            if (url === "/auth/users/me") return Promise.resolve({ data: user })
            if (url === "/institution/setup/locations") return Promise.resolve({ data: [LOC_A, LOC_B] })
            if (url.startsWith("/institution/setup/appointment-types")) return Promise.resolve({ data: [] })
            if (url.startsWith("/institution/setup/descriptors")) return Promise.resolve({ data: [] })
            return Promise.reject(new Error(`unexpected GET ${url}`))
        })

        // Default selection is the first active location (LOC_A).
        render(
            <MemoryRouter>
                <AuthProvider>
                    <LocationProvider>
                        <AppointmentTypes />
                    </LocationProvider>
                </AuthProvider>
            </MemoryRouter>
        )

        await waitFor(() => {
            expect(calls).toContain(`/institution/setup/appointment-types?location_id=${LOC_A.id}`)
        })
        expect(calls).toContain(`/institution/setup/descriptors?location_id=${LOC_A.id}`)
        // Critical regression check: must NOT call without location_id —
        // that's the path that 400'd before this fix.
        expect(calls).not.toContain("/institution/setup/appointment-types")
    })

    it("LOCATION_ADMIN: GETs appointment-types with their pinned location_id", async () => {
        const user = makeUser("LOCATION_ADMIN", LOC_B.id)
        const apiGet = api.get as ReturnType<typeof vi.fn>
        const calls: string[] = []
        apiGet.mockImplementation((url: string) => {
            calls.push(url)
            if (url === "/auth/users/me") return Promise.resolve({ data: user })
            if (url === "/institution/setup/locations") return Promise.resolve({ data: [LOC_B] })
            if (url.startsWith("/institution/setup/appointment-types")) return Promise.resolve({ data: [] })
            if (url.startsWith("/institution/setup/descriptors")) return Promise.resolve({ data: [] })
            return Promise.reject(new Error(`unexpected GET ${url}`))
        })

        render(
            <MemoryRouter>
                <AuthProvider>
                    <LocationProvider>
                        <AppointmentTypes />
                    </LocationProvider>
                </AuthProvider>
            </MemoryRouter>
        )

        await waitFor(() => {
            expect(calls).toContain(`/institution/setup/appointment-types?location_id=${LOC_B.id}`)
        })
    })

    it("switching location refetches appointment-types for the new location", async () => {
        const user = makeUser("INSTITUTION_ADMIN")
        const apiGet = api.get as ReturnType<typeof vi.fn>
        const calls: string[] = []
        apiGet.mockImplementation((url: string) => {
            calls.push(url)
            if (url === "/auth/users/me") return Promise.resolve({ data: user })
            if (url === "/institution/setup/locations") return Promise.resolve({ data: [LOC_A, LOC_B] })
            if (url.startsWith("/institution/setup/appointment-types")) return Promise.resolve({ data: [] })
            if (url.startsWith("/institution/setup/descriptors")) return Promise.resolve({ data: [] })
            return Promise.reject(new Error(`unexpected GET ${url}`))
        })

        // Pre-seed localStorage so the switch is observable.
        // Then simulate a switch by writing to localStorage and re-rendering.
        const { unmount } = render(
            <MemoryRouter>
                <AuthProvider>
                    <LocationProvider>
                        <AppointmentTypes />
                    </LocationProvider>
                </AuthProvider>
            </MemoryRouter>
        )

        await waitFor(() => {
            expect(calls).toContain(`/institution/setup/appointment-types?location_id=${LOC_A.id}`)
        })
        unmount()

        // Switch to LOC_B (simulating user picking it from the dropdown
        // — the dropdown's onValueChange writes to localStorage).
        localStorage.setItem("nex.selectedLocationId", LOC_B.id)
        calls.length = 0

        render(
            <MemoryRouter>
                <AuthProvider>
                    <LocationProvider>
                        <AppointmentTypes />
                    </LocationProvider>
                </AuthProvider>
            </MemoryRouter>
        )

        await waitFor(() => {
            expect(calls).toContain(`/institution/setup/appointment-types?location_id=${LOC_B.id}`)
        })
        // Did not regress to the unscoped path.
        expect(calls).not.toContain(`/institution/setup/appointment-types?location_id=${LOC_A.id}`)
        // Mark userEvent as exercised so the import isn't dead — the
        // higher-fidelity dropdown-driven version is in
        // LocationContext.test.tsx.
        void userEvent
    })
})
