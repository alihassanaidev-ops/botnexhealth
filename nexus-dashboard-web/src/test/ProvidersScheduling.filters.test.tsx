/**
 * Integration tests for the Work Windows list: pagination, the recurring-rules
 * section, and the "Include past dates" control.
 *
 * These render the real page against a mocked API. Radix Select and Popover
 * need pointer-event APIs jsdom doesn't implement, so the appointment-type /
 * operatory / date-range controls are exercised through
 * `availability-filter.test.ts` instead; what's asserted here is the wiring
 * those pure functions feed — how many rows reach the DOM, which section they
 * land in, and that paging doesn't lose the recurring rules.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import ProvidersScheduling from "@/pages/ProvidersScheduling"
import { AuthProvider } from "@/context/AuthContext"
import { LocationProvider } from "@/context/LocationContext"
import api from "@/lib/api"
import { addDays, todayISO } from "@/lib/availability-filter"
import type { CachedAvailability, User } from "@/types"

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

const LOCATION = { id: "11111111-1111-1111-1111-111111111111", name: "Downtown", slug: "downtown" }
const PROVIDER = { id: "prov-uuid", source_id: "nh-1", name: "Dr Kadri", first_name: "A", last_name: "Kadri", is_active: true }
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

function makeAvailability(overrides: Partial<CachedAvailability>): CachedAvailability {
    return {
        id: `id-${overrides.source_id}`,
        source_id: "s",
        provider_source_id: "nh-1",
        provider_name: "Dr Kadri",
        operatory_source_id: "op1",
        operatory_name: "Room 1",
        begin_time: "09:00",
        end_time: "17:00",
        days: null,
        specific_date: null,
        appointment_type_ids: ["t1"],
        appointment_type_names: ["Cleaning"],
        active: true,
        synced: true,
        source_metadata: null,
        synced_at: null,
        ...overrides,
    }
}

/** N dated windows, one per day starting tomorrow. */
function datedWindows(count: number): CachedAvailability[] {
    return Array.from({ length: count }, (_, i) =>
        makeAvailability({
            source_id: `dated-${i}`,
            specific_date: addDays(todayISO(), i + 1),
        })
    )
}

function recurringWindows(count: number): CachedAvailability[] {
    return Array.from({ length: count }, (_, i) =>
        makeAvailability({
            source_id: `recurring-${i}`,
            specific_date: null,
            days: ["Monday", "Wednesday"],
        })
    )
}

function mountWith(availabilities: CachedAvailability[]) {
    const apiGet = api.get as ReturnType<typeof vi.fn>
    apiGet.mockImplementation((url: string) => {
        if (url === "/auth/users/me") return Promise.resolve({ data: USER })
        if (url.startsWith("/institution/setup/locations")) return Promise.resolve({ data: [LOCATION] })
        if (url.startsWith("/institution/setup/providers")) return Promise.resolve({ data: [PROVIDER] })
        if (url.startsWith("/institution/setup/appointment-types")) return Promise.resolve({ data: [APPT_TYPE] })
        if (url.startsWith("/institution/setup/operatories")) return Promise.resolve({ data: [OPERATORY] })
        if (url.startsWith("/institution/setup/availabilities")) return Promise.resolve({ data: availabilities })
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

/** Work-window rows are identified by their "Edit Linking" button. */
function rowCount() {
    return screen.queryAllByRole("button", { name: /edit linking/i }).length
}

/**
 * Full text of the pagination summary. Queried loosely because the counts live
 * in nested <span>s and testing-library's text matcher joins only an element's
 * direct text nodes.
 */
function pagerText() {
    return screen.getByText(/^Showing/).textContent?.replace(/\s+/g, " ").trim() ?? ""
}

beforeEach(() => {
    localStorage.clear()
    ;(api.get as ReturnType<typeof vi.fn>).mockReset()
})

describe("Work Windows pagination", () => {
    it("renders only the first page of dated windows", async () => {
        mountWith(datedWindows(60))

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        await waitFor(() => expect(rowCount()).toBe(25))

        expect(pagerText()).toBe("Showing 1–25 of 60 dated windows")
        expect(screen.getByText("Page 1 of 3")).toBeInTheDocument()
    })

    it("advances to the next page and back", async () => {
        const user = userEvent.setup()
        mountWith(datedWindows(60))

        await waitFor(() => expect(rowCount()).toBe(25))

        await user.click(screen.getByRole("button", { name: /next/i }))
        expect(screen.getByText("Page 2 of 3")).toBe(screen.getByText("Page 2 of 3"))
        expect(rowCount()).toBe(25)

        await user.click(screen.getByRole("button", { name: /next/i }))
        expect(screen.getByText("Page 3 of 3")).toBeInTheDocument()
        // 60 = 25 + 25 + 10
        expect(rowCount()).toBe(10)

        await user.click(screen.getByRole("button", { name: /previous/i }))
        expect(screen.getByText("Page 2 of 3")).toBeInTheDocument()
        expect(rowCount()).toBe(25)
    })

    it("disables Previous on the first page and Next on the last", async () => {
        const user = userEvent.setup()
        mountWith(datedWindows(30))

        await waitFor(() => expect(rowCount()).toBe(25))
        expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled()

        await user.click(screen.getByRole("button", { name: /next/i }))
        expect(screen.getByRole("button", { name: /next/i })).toBeDisabled()
        expect(screen.getByRole("button", { name: /previous/i })).toBeEnabled()
    })

    it("hides the pager entirely when everything fits on one page", async () => {
        mountWith(datedWindows(10))

        await waitFor(() => expect(rowCount()).toBe(10))
        expect(screen.queryByText(/^Showing/)).not.toBeInTheDocument()
        expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument()
    })
})

describe("Recurring weekly windows", () => {
    it("are pinned outside pagination and stay visible on every page", async () => {
        const user = userEvent.setup()
        mountWith([...recurringWindows(3), ...datedWindows(60)])

        await waitFor(() => expect(screen.getByText(/Recurring weekly windows/i)).toBeInTheDocument())

        // 3 recurring (unpaginated) + 25 dated (page 1).
        await waitFor(() => expect(rowCount()).toBe(28))
        // The pager counts only the dated rows — the 3 recurring ones sit outside it.
        expect(pagerText()).toBe("Showing 1–25 of 60 dated windows")

        await user.click(screen.getByRole("button", { name: /next/i }))
        expect(screen.getByText(/Recurring weekly windows/i)).toBeInTheDocument()
        expect(rowCount()).toBe(28)
    })

    it("does not render the recurring section when there are none", async () => {
        mountWith(datedWindows(5))

        await waitFor(() => expect(rowCount()).toBe(5))
        expect(screen.queryByText(/Recurring weekly windows/i)).not.toBeInTheDocument()
    })

    it("shows recurring rules even when no dated window survives the filter", async () => {
        mountWith(recurringWindows(2))

        await waitFor(() => expect(rowCount()).toBe(2))
        expect(screen.getByText(/Recurring weekly windows/i)).toBeInTheDocument()
        expect(screen.queryByText(/No work windows/i)).not.toBeInTheDocument()
    })
})

describe("Include past dates", () => {
    it("hides expired windows by default and reveals them when checked", async () => {
        const user = userEvent.setup()
        mountWith([
            makeAvailability({ source_id: "past-1", specific_date: addDays(todayISO(), -5) }),
            makeAvailability({ source_id: "past-2", specific_date: addDays(todayISO(), -1) }),
            makeAvailability({ source_id: "future-1", specific_date: addDays(todayISO(), 1) }),
        ])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        // Only the future window survives the default today-clamped range.
        await waitFor(() => expect(rowCount()).toBe(1))
        expect(screen.queryByText("Expired")).not.toBeInTheDocument()

        await user.click(screen.getByRole("checkbox", { name: /include past dates/i }))

        await waitFor(() => expect(rowCount()).toBe(3))
        expect(screen.getAllByText("Expired")).toHaveLength(2)
    })

    it("re-clamps to today when unchecked", async () => {
        const user = userEvent.setup()
        mountWith([
            makeAvailability({ source_id: "past-1", specific_date: addDays(todayISO(), -5) }),
            makeAvailability({ source_id: "future-1", specific_date: addDays(todayISO(), 1) }),
        ])

        await waitFor(() => expect(rowCount()).toBe(1))

        const checkbox = screen.getByRole("checkbox", { name: /include past dates/i })
        await user.click(checkbox)
        await waitFor(() => expect(rowCount()).toBe(2))

        await user.click(checkbox)
        await waitFor(() => expect(rowCount()).toBe(1))
    })

    it("resets to page 1 when the filter changes, so the operator is never stranded", async () => {
        const user = userEvent.setup()
        // 60 future + 1 past: paging to 3 then widening must not leave page 3 empty.
        mountWith([
            ...datedWindows(60),
            makeAvailability({ source_id: "past-1", specific_date: addDays(todayISO(), -5) }),
        ])

        await waitFor(() => expect(rowCount()).toBe(25))
        await user.click(screen.getByRole("button", { name: /next/i }))
        await user.click(screen.getByRole("button", { name: /next/i }))
        expect(screen.getByText("Page 3 of 3")).toBeInTheDocument()

        await user.click(screen.getByRole("checkbox", { name: /include past dates/i }))

        await waitFor(() => expect(screen.getByText("Page 1 of 3")).toBeInTheDocument())
        expect(rowCount()).toBe(25)
    })
})

describe("Filter placement", () => {
    it("renders the list filters above the rows they act on", async () => {
        mountWith(datedWindows(3))

        await waitFor(() => expect(rowCount()).toBe(3))

        const filters = screen.getByText("Filters")
        const firstRow = screen.getAllByRole("button", { name: /edit linking/i })[0]

        // Node.DOCUMENT_POSITION_FOLLOWING === 4: the row comes after the filter bar.
        expect(filters.compareDocumentPosition(firstRow) & 4).toBeTruthy()
    })

    it("keeps the list filters in the same container as the rows", async () => {
        mountWith(datedWindows(3))

        await waitFor(() => expect(rowCount()).toBe(3))

        // Both the filter bar and the rows must live under the Work Windows card,
        // not split across the page with the Scheduling Rules card between them.
        // Walk up from the card title to the first ancestor that holds a row.
        let card: HTMLElement | null = screen.getByText(/Work Windows for/)
        while (card && !card.querySelector("button")?.textContent?.match(/Edit Linking/)) {
            const rows = within(card).queryAllByRole("button", { name: /edit linking/i })
            if (rows.length > 0) break
            card = card.parentElement
        }
        expect(card).not.toBeNull()
        expect(within(card as HTMLElement).getByText("Filters")).toBeInTheDocument()
        expect(
            within(card as HTMLElement).getByRole("checkbox", { name: /include past dates/i })
        ).toBeInTheDocument()
    })
})
