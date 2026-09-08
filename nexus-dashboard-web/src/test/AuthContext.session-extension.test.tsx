import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"

import { AuthProvider } from "@/context/AuthContext"
import { AUTH_INACTIVITY_TIMEOUT_MS } from "@/lib/auth-session-policy"

const { apiGet, refreshBackendToken, toastSuccess } = vi.hoisted(() => ({
    apiGet: vi.fn(),
    refreshBackendToken: vi.fn(),
    toastSuccess: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
    refreshBackendToken,
    default: {
        defaults: { baseURL: "http://test.local/api" },
        get: apiGet,
        interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    },
}))

vi.mock("@/lib/token-manager", () => ({
    getAccessToken: vi.fn(() => "access-token"),
    setAccessToken: vi.fn(),
    clearAccessToken: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: {
        error: vi.fn(),
        info: vi.fn(),
        success: toastSuccess,
    },
}))

describe("AuthContext server-side session extension", () => {
    beforeEach(() => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        apiGet.mockResolvedValue({
            data: {
                id: "user-1",
                email: "staff@clinic.test",
                role: "STAFF",
                institution_id: "inst-1",
                location_id: "loc-1",
                is_active: true,
            },
        })
        refreshBackendToken.mockResolvedValue("new-access-token")
    })

    afterEach(() => vi.useRealTimers())

    it("renews the backend refresh session before reporting success", async () => {
        render(
            <MemoryRouter>
                <AuthProvider><div>Dashboard</div></AuthProvider>
            </MemoryRouter>,
        )

        await waitFor(() => expect(apiGet).toHaveBeenCalled())

        await act(async () => {
            vi.advanceTimersByTime(AUTH_INACTIVITY_TIMEOUT_MS - 60_000)
        })

        fireEvent.click(screen.getByRole("button", { name: "Stay signed in" }))

        await waitFor(() => expect(refreshBackendToken).toHaveBeenCalledTimes(1))
        expect(toastSuccess).toHaveBeenCalledWith("Session extended")
    })
})
