/**
 * Inbox scoping and capabilities, rendered for real.
 *
 * The page must not restate the permission model — it reads capabilities from
 * `/inbox/scopes` and renders what the API says the caller may do. These tests
 * hold that line: a read-only role gets no write actions, a platform admin gets
 * the practice/location cascade, and an institution admin's sidebar location
 * choice actually narrows the request.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import { AuthProvider } from "@/context/AuthContext"
import { LocationProvider } from "@/context/LocationContext"
import Inbox from "@/pages/Inbox"
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

const THREAD = {
    id: "thread-1",
    channel: "email",
    status: "open",
    institution_id: "inst-1",
    location_id: "loc-a",
    institution_name: "Bright Smiles",
    location_name: "Downtown Clinic",
    contact_id: "contact-1",
    contact_name: "Jane Patient",
    contact_masked_email: "j***@example.com",
    last_message_at: "2026-08-25T10:00:00Z",
    opened_at: "2026-08-25T09:00:00Z",
    unresolved_handoffs: 1,
    assignee_user_id: null,
    latest_intent: "question",
    sender_mismatch: false,
}

function makeUser(role: User["role"], location_id: string | null = null): User {
    return {
        id: "user-1",
        email: "test@clinic.com",
        full_name: "Test User",
        role,
        institution_id: role === "SUPER_ADMIN" ? null : "inst-1",
        location_id,
        is_active: true,
        is_email_verified: true,
        provisional_password_set: false,
        mfa_enrolled: false,
    } as User
}

interface Options {
    user: User
    scopes: Record<string, unknown>
    locations?: Array<typeof LOC_A>
}

/** Records every /inbox/threads request so the tests can assert the params. */
function setupApiMocks(opts: Options) {
    const threadCalls: Array<Record<string, unknown>> = []
    const apiGet = api.get as ReturnType<typeof vi.fn>
    apiGet.mockReset()
    apiGet.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
        if (url === "/auth/users/me") return Promise.resolve({ data: opts.user })
        if (url === "/institution/setup/locations")
            return Promise.resolve({ data: opts.locations ?? [] })
        if (url === "/inbox/scopes") return Promise.resolve({ data: opts.scopes })
        if (url === "/inbox/threads") {
            threadCalls.push(config?.params ?? {})
            return Promise.resolve({ data: { threads: [THREAD] } })
        }
        if (url.startsWith("/inbox/threads/"))
            return Promise.resolve({ data: { thread: THREAD, messages: [] } })
        return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    return threadCalls
}

function renderInbox() {
    return render(
        <MemoryRouter>
            <AuthProvider>
                <LocationProvider>
                    <Inbox />
                </LocationProvider>
            </AuthProvider>
        </MemoryRouter>,
    )
}

const SUPER_SCOPES = {
    role: "SUPER_ADMIN",
    institutions: [
        { id: "inst-1", name: "Bright Smiles", locations: [{ id: "loc-a", name: "Downtown Clinic" }] },
        { id: "inst-2", name: "Northside Dental", locations: [{ id: "loc-c", name: "Main Street" }] },
    ],
    can_filter_institution: true,
    can_filter_location: true,
    can_read_content: true,
    can_write: true,
    can_assign: true,
}

const STAFF_SCOPES = {
    role: "STAFF",
    institutions: [
        { id: "inst-1", name: "Bright Smiles", locations: [{ id: "loc-a", name: "Downtown Clinic" }] },
    ],
    can_filter_institution: false,
    can_filter_location: false,
    can_read_content: true,
    can_write: false,
    can_assign: false,
}

const INSTITUTION_SCOPES = {
    role: "INSTITUTION_ADMIN",
    institutions: [
        {
            id: "inst-1",
            name: "Bright Smiles",
            locations: [
                { id: "loc-a", name: "Downtown Clinic" },
                { id: "loc-b", name: "Uptown Clinic" },
            ],
        },
    ],
    can_filter_institution: false,
    can_filter_location: true,
    can_read_content: true,
    can_write: true,
    can_assign: true,
}

beforeEach(() => {
    localStorage.clear()
})

describe("Inbox — super admin", () => {
    it("offers a practice filter and narrows the request to the chosen one", async () => {
        const calls = setupApiMocks({ user: makeUser("SUPER_ADMIN"), scopes: SUPER_SCOPES })
        renderInbox()

        const practice = await screen.findByLabelText("Practice")
        await userEvent.click(practice)
        await userEvent.click(await screen.findByText("Northside Dental"))

        await waitFor(() =>
            expect(calls[calls.length - 1]?.institution_id).toBe("inst-2"),
        )
    })

    it("shows which practice a conversation belongs to", async () => {
        setupApiMocks({ user: makeUser("SUPER_ADMIN"), scopes: SUPER_SCOPES })
        renderInbox()

        expect(await screen.findByText(/Bright Smiles — Downtown Clinic/)).toBeTruthy()
    })
})

describe("Inbox — institution admin", () => {
    it("follows the sidebar's active location", async () => {
        const calls = setupApiMocks({
            user: makeUser("INSTITUTION_ADMIN"),
            scopes: INSTITUTION_SCOPES,
            locations: [LOC_A, LOC_B],
        })
        renderInbox()

        // LocationProvider defaults to the first active location; the inbox is
        // expected to pick that up rather than listing every location's threads.
        await waitFor(() => expect(calls[calls.length - 1]?.location_id).toBe("loc-a"))
    })

    it("has no practice filter — one institution is the whole span", async () => {
        setupApiMocks({
            user: makeUser("INSTITUTION_ADMIN"),
            scopes: INSTITUTION_SCOPES,
            locations: [LOC_A, LOC_B],
        })
        renderInbox()

        await screen.findByLabelText("Location")
        expect(screen.queryByLabelText("Practice")).toBeNull()
    })
})

describe("Inbox — staff", () => {
    it("is read-only: no assign, no resolve", async () => {
        setupApiMocks({
            user: makeUser("STAFF", "loc-a"),
            scopes: STAFF_SCOPES,
            locations: [LOC_A],
        })
        renderInbox()

        await userEvent.click(await screen.findByText("Jane Patient"))

        await screen.findByText(/You have read access to this conversation/)
        expect(screen.queryByText("Mark resolved")).toBeNull()
        expect(screen.queryByText("Assign to me")).toBeNull()
    })

    it("gets no location or practice filter", async () => {
        setupApiMocks({
            user: makeUser("STAFF", "loc-a"),
            scopes: STAFF_SCOPES,
            locations: [LOC_A],
        })
        renderInbox()

        await screen.findByText("Jane Patient")
        expect(screen.queryByLabelText("Practice")).toBeNull()
        expect(screen.queryByLabelText("Location")).toBeNull()
    })
})
