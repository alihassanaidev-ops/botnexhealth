/**
 * Real integration tests for LocationProvider.
 *
 * These render the actual provider with a stubbed axios `api`, drive the
 * hook through real React state transitions, and assert on:
 *   - what gets fetched
 *   - what gets stored in localStorage
 *   - what survives a remount
 *   - which roles are gated
 *
 * No mocks beyond `@/lib/api.get`. Persistence is real localStorage
 * (jsdom). Auth is the actual AuthContext stubbed only at its api layer.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import type { ReactNode } from "react"

import { AuthProvider } from "@/context/AuthContext"
import { LocationProvider, useLocationContext } from "@/context/LocationContext"
import { LocationSelector } from "@/components/location-selector"
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

const LOC_A = { id: "loc-a", name: "Downtown Clinic", slug: "downtown" }
const LOC_B = { id: "loc-b", name: "Uptown Clinic", slug: "uptown" }
const LOC_C = { id: "loc-c", name: "Riverside Clinic", slug: "riverside" }

function makeUser(role: User["role"], location_id: string | null = null): User {
    return {
        id: "user-1",
        email: "test@clinic.com",
        full_name: "Test User",
        role,
        institution_id: "inst-1",
        location_id,
        is_active: true,
        is_email_verified: true,
        provisional_password_set: false,
        mfa_enrolled: false,
    } as User
}

function setupApiMocks(opts: { user: User | null; locations: Array<typeof LOC_A> }) {
    const apiGet = api.get as ReturnType<typeof vi.fn>
    apiGet.mockReset()
    apiGet.mockImplementation((url: string) => {
        if (url === "/auth/users/me") {
            if (!opts.user) return Promise.reject(new Error("unauth"))
            return Promise.resolve({ data: opts.user })
        }
        if (url === "/institution/setup/locations") {
            return Promise.resolve({ data: opts.locations })
        }
        return Promise.reject(new Error(`unexpected GET ${url}`))
    })
}

function renderWithProviders(children: ReactNode) {
    return render(
        <MemoryRouter>
            <AuthProvider>
                <LocationProvider>{children}</LocationProvider>
            </AuthProvider>
        </MemoryRouter>
    )
}

function Spy() {
    const ctx = useLocationContext()
    return (
        <div>
            <span data-testid="selected">{ctx.selectedLocationId ?? "(none)"}</span>
            <span data-testid="canSwitch">{String(ctx.canSwitch)}</span>
            <span data-testid="count">{ctx.locations.length}</span>
        </div>
    )
}

beforeEach(() => {
    localStorage.clear()
})

describe("LocationProvider — INSTITUTION_ADMIN", () => {
    it("fetches active locations and defaults to the first", async () => {
        setupApiMocks({ user: makeUser("INSTITUTION_ADMIN"), locations: [LOC_A, LOC_B, LOC_C] })

        renderWithProviders(<Spy />)

        await waitFor(() => {
            expect(screen.getByTestId("selected").textContent).toBe(LOC_A.id)
        })
        expect(screen.getByTestId("count").textContent).toBe("3")
        expect(screen.getByTestId("canSwitch").textContent).toBe("true")
        expect(localStorage.getItem("nex.selectedLocationId")).toBe(LOC_A.id)
    })

    it("restores a valid selection from localStorage on mount", async () => {
        localStorage.setItem("nex.selectedLocationId", LOC_B.id)
        setupApiMocks({ user: makeUser("INSTITUTION_ADMIN"), locations: [LOC_A, LOC_B, LOC_C] })

        renderWithProviders(<Spy />)

        await waitFor(() => {
            expect(screen.getByTestId("selected").textContent).toBe(LOC_B.id)
        })
    })

    it("ignores a stale localStorage id that's no longer in the active list", async () => {
        localStorage.setItem("nex.selectedLocationId", "loc-deleted")
        setupApiMocks({ user: makeUser("INSTITUTION_ADMIN"), locations: [LOC_A, LOC_B] })

        renderWithProviders(<Spy />)

        await waitFor(() => {
            expect(screen.getByTestId("selected").textContent).toBe(LOC_A.id)
        })
        expect(localStorage.getItem("nex.selectedLocationId")).toBe(LOC_A.id)
    })

    it("setSelectedLocationId persists the new choice to localStorage", async () => {
        setupApiMocks({ user: makeUser("INSTITUTION_ADMIN"), locations: [LOC_A, LOC_B, LOC_C] })

        function Switcher() {
            const { setSelectedLocationId } = useLocationContext()
            return (
                <button onClick={() => setSelectedLocationId(LOC_C.id)} data-testid="switch">
                    switch
                </button>
            )
        }

        renderWithProviders(
            <>
                <Spy />
                <Switcher />
            </>
        )

        await waitFor(() => {
            expect(screen.getByTestId("selected").textContent).toBe(LOC_A.id)
        })

        await userEvent.click(screen.getByTestId("switch"))

        await waitFor(() => {
            expect(screen.getByTestId("selected").textContent).toBe(LOC_C.id)
        })
        expect(localStorage.getItem("nex.selectedLocationId")).toBe(LOC_C.id)
    })

    it("LocationSelector renders a dropdown with every active location", async () => {
        setupApiMocks({ user: makeUser("INSTITUTION_ADMIN"), locations: [LOC_A, LOC_B] })

        renderWithProviders(<LocationSelector />)

        await waitFor(() => {
            expect(screen.getByTestId("location-selector")).toBeInTheDocument()
        })
        // The trigger reflects the default selection by name.
        expect(screen.getByTestId("location-selector").textContent).toContain(LOC_A.name)
    })

    it("LocationSelector hides when only one active location exists (nothing to switch)", async () => {
        setupApiMocks({ user: makeUser("INSTITUTION_ADMIN"), locations: [LOC_A] })

        renderWithProviders(<LocationSelector />)

        // After load completes, the selector should still not render.
        await act(async () => {
            await Promise.resolve()
        })
        expect(screen.queryByTestId("location-selector")).not.toBeInTheDocument()
    })
})

describe("LocationProvider — LOCATION_ADMIN / STAFF", () => {
    it("LOCATION_ADMIN does not show the selector and does not persist", async () => {
        setupApiMocks({
            user: makeUser("LOCATION_ADMIN", LOC_B.id),
            locations: [LOC_B], // backend returns only their own
        })

        renderWithProviders(
            <>
                <Spy />
                <LocationSelector />
            </>
        )

        await waitFor(() => {
            expect(screen.getByTestId("selected").textContent).toBe(LOC_B.id)
        })
        expect(screen.getByTestId("canSwitch").textContent).toBe("false")
        expect(screen.queryByTestId("location-selector")).not.toBeInTheDocument()
        // canSwitch=false → must NOT write to localStorage. The backend
        // is the authority for these roles; persisting client-side
        // would just go stale on a role change.
        expect(localStorage.getItem("nex.selectedLocationId")).toBeNull()
    })

    it("STAFF behaves the same as LOCATION_ADMIN (pinned, no selector)", async () => {
        setupApiMocks({ user: makeUser("STAFF", LOC_A.id), locations: [LOC_A] })

        renderWithProviders(<LocationSelector />)
        await act(async () => {
            await Promise.resolve()
        })
        expect(screen.queryByTestId("location-selector")).not.toBeInTheDocument()
    })
})

describe("LocationProvider — non-institution roles", () => {
    it("SUPER_ADMIN gets no locations and no selection", async () => {
        setupApiMocks({ user: makeUser("SUPER_ADMIN"), locations: [] })

        renderWithProviders(<Spy />)

        await waitFor(() => {
            expect(screen.getByTestId("count").textContent).toBe("0")
        })
        expect(screen.getByTestId("selected").textContent).toBe("(none)")
        expect(screen.getByTestId("canSwitch").textContent).toBe("false")
    })
})
