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

import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest"
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
        label_name: null,
        is_bookable_window: true,
        source_metadata: null,
        synced_at: null,
        ...overrides,
    }
}

/** N dated windows, one per day starting tomorrow. */
/**
 * N dated windows spread across the coming week, several per day.
 *
 * The list opens on a 7-day range, so rows must land inside it; spreading them
 * over multiple days also exercises the per-date grouping headers.
 */
function datedWindows(count: number): CachedAvailability[] {
    return Array.from({ length: count }, (_, i) =>
        makeAvailability({
            source_id: `dated-${i}`,
            specific_date: addDays(todayISO(), (i % 6) + 1),
            begin_time: `${String(8 + Math.floor(i / 6)).padStart(2, "0")}:00`,
        })
    )
}

/** N dated windows beyond the default week — used to prove the range narrows. */
function farFutureWindows(count: number): CachedAvailability[] {
    return Array.from({ length: count }, (_, i) =>
        makeAvailability({
            source_id: `far-${i}`,
            specific_date: addDays(todayISO(), 20 + i),
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
        // Drives canLinkAvailability, which gates the appointment-type filter,
        // the "Work Windows" heading and the linking UI. Without it the page
        // renders in "Live Slots" mode and none of that exists.
        if (url.startsWith("/institution/setup/overview"))
            return Promise.resolve({ data: { can_link_availability: true } })
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

// Radix's Popover relies on pointer-capture and scrollIntoView, neither of
// which jsdom implements. Stubbing them lets the date-range picker be driven
// like a real user drives it, instead of reaching past the UI.
beforeAll(() => {
    Element.prototype.hasPointerCapture = vi.fn(() => false)
    Element.prototype.setPointerCapture = vi.fn()
    Element.prototype.releasePointerCapture = vi.fn()
    Element.prototype.scrollIntoView = vi.fn()
})

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

describe("Past-dated windows", () => {
    it("are filtered out client-side even if the backend sends them", async () => {
        // The backend drops these before they reach us
        // (adapter.list_availabilities defaults ignore_past_dates=True), so
        // there is no UI control to reveal them. If one arrives anyway, the
        // today-clamped range must still keep it out of the list.
        mountWith([
            makeAvailability({ source_id: "past-1", specific_date: addDays(todayISO(), -5) }),
            makeAvailability({ source_id: "past-2", specific_date: addDays(todayISO(), -1) }),
            makeAvailability({ source_id: "future-1", specific_date: addDays(todayISO(), 1) }),
        ])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        await waitFor(() => expect(rowCount()).toBe(1))
        expect(screen.queryByText("Expired")).not.toBeInTheDocument()
    })

    it("are reachable only through Show expired, not a second past-dates control", async () => {
        // This branch keeps a "Show expired" toggle. What must NOT exist is a
        // competing control for the same thing — a date-range lower bound and an
        // "include past dates" checkbox contradicted each other.
        mountWith(datedWindows(3))

        await waitFor(() => expect(rowCount()).toBe(3))
        expect(screen.getByRole("checkbox", { name: /show expired/i })).toBeInTheDocument()
        expect(screen.queryByRole("checkbox", { name: /include past/i })).not.toBeInTheDocument()
    })
})

describe("Inactive windows", () => {
    it("are hidden from the list, matching what the calendar already did", async () => {
        // NexHealth deactivates rather than deletes — a duplicate cleanup comes
        // back as active:false rather than disappearing. Both backend paths drop
        // these today, but the list must not depend on that.
        mountWith([
            makeAvailability({ source_id: "live-1", specific_date: addDays(todayISO(), 1) }),
            makeAvailability({ source_id: "dead-1", specific_date: addDays(todayISO(), 1), active: false }),
            makeAvailability({ source_id: "dead-2", specific_date: addDays(todayISO(), 2), active: false }),
        ])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        await waitFor(() => expect(rowCount()).toBe(1))
    })

    it("do not count toward the unlinked-appointment-types warning", async () => {
        mountWith([
            makeAvailability({
                source_id: "dead-unlinked",
                specific_date: addDays(todayISO(), 1),
                active: false,
                appointment_type_ids: [],
                appointment_type_names: [],
            }),
        ])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        // The banner counts unlinked windows; an inactive one generates no
        // slots so it must not appear there. queryAllByText because the
        // empty-state copy also mentions linking.
        expect(screen.queryAllByText(/window(s)? without linked/i)).toHaveLength(0)
    })
})

describe("Notes and breaks (v3 labels)", () => {
    it("shows non-bookable rows with their label, and can hide them", async () => {
        // NexHealth returns Lunch blocks and synced OpenDental notes in the same
        // collection as real working hours. For one clinic that was 659 of 2,045
        // rows, which buries the schedule the operator is actually linking.
        const user = userEvent.setup()
        mountWith([
            makeAvailability({ source_id: "real", specific_date: addDays(todayISO(), 1) }),
            makeAvailability({
                source_id: "lunch", specific_date: addDays(todayISO(), 1),
                label_name: "Lunch", is_bookable_window: false,
            }),
            makeAvailability({
                source_id: "note", specific_date: addDays(todayISO(), 1),
                label_name: "NOTE", is_bookable_window: false,
            }),
        ])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        // Shown by default, each carrying its label — the label is the whole point.
        await waitFor(() => expect(rowCount()).toBe(3))
        expect(screen.getByText("Lunch")).toBeInTheDocument()
        expect(screen.getByText("NOTE")).toBeInTheDocument()

        await user.click(screen.getByRole("checkbox", { name: /show notes & breaks/i }))

        await waitFor(() => expect(rowCount()).toBe(1))
        expect(screen.queryByText("Lunch")).not.toBeInTheDocument()
    })

    it("does not count notes or breaks as unlinked appointment types", async () => {
        // A lunch break has no appointment type to link, so warning about it
        // sends the operator chasing something they cannot fix.
        mountWith([
            makeAvailability({
                source_id: "lunch", specific_date: addDays(todayISO(), 1),
                label_name: "Lunch", is_bookable_window: false,
                appointment_type_ids: [], appointment_type_names: [],
            }),
        ])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        expect(screen.queryByText(/without linked/i)).not.toBeInTheDocument()
    })

    it("treats v2 rows as bookable, since v2 cannot label them", async () => {
        // The backend reports is_bookable_window=true for every v2 row.
        mountWith([
            makeAvailability({ source_id: "v2a", specific_date: addDays(todayISO(), 1) }),
            makeAvailability({ source_id: "v2b", specific_date: addDays(todayISO(), 2) }),
        ])

        await waitFor(() => expect(rowCount()).toBe(2))
        expect(screen.queryByRole("checkbox", { name: /show notes & breaks/i })).toBeInTheDocument()
    })
})

describe("Date range filter", () => {
    it("opens on the coming week and hides windows beyond it", async () => {
        // The default is deliberately narrow: NexHealth pre-expands work windows
        // one row per date, so an open-ended default buries the week an operator
        // is actually working on under thousands of future rows.
        mountWith([...datedWindows(10), ...farFutureWindows(8)])

        await waitFor(() => expect(screen.getByText(/Work Windows for/)).toBeInTheDocument())
        await waitFor(() => expect(rowCount()).toBe(10))
    })

    it("reveals the later windows when widened, and resets to page 1", async () => {
        const user = userEvent.setup()
        mountWith([...datedWindows(30), ...farFutureWindows(8)])

        await waitFor(() => expect(rowCount()).toBe(25))

        // Page to the end first, so the reset-to-page-1 behaviour is observable.
        await user.click(screen.getByRole("button", { name: /next/i }))
        expect(screen.getByText("Page 2 of 2")).toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: /filter by date range/i }))
        await user.click(await screen.findByRole("button", { name: "Next 30 days" }))

        // 30 in-week + 8 far-future = 38, so page 1 fills and the pager grows.
        await waitFor(() => expect(screen.getByText("Page 1 of 2")).toBeInTheDocument())
        expect(rowCount()).toBe(25)
        expect(pagerText()).toBe("Showing 1–25 of 38 dated windows")
    })

    it("returns to the default week when filters are cleared", async () => {
        const user = userEvent.setup()
        mountWith([...datedWindows(10), ...farFutureWindows(8)])

        await waitFor(() => expect(rowCount()).toBe(10))

        await user.click(screen.getByRole("button", { name: /filter by date range/i }))
        await user.click(await screen.findByRole("button", { name: "Next 30 days" }))
        await waitFor(() => expect(rowCount()).toBe(18))

        await user.click(screen.getByRole("button", { name: /clear filters/i }))
        await waitFor(() => expect(rowCount()).toBe(10))
    })

    it("keeps recurring rules visible regardless of the range", async () => {
        mountWith([...recurringWindows(2), ...datedWindows(10), ...farFutureWindows(8)])

        // 2 recurring + 10 in-week. Recurring rules repeat forever, so no range
        // excludes them — but the far-future dated rows are still filtered out.
        await waitFor(() => expect(rowCount()).toBe(12))
        expect(screen.getByText(/Recurring weekly windows/i)).toBeInTheDocument()
    })

    it("groups the dated rows under a heading per date", async () => {
        mountWith(datedWindows(12))

        await waitFor(() => expect(rowCount()).toBe(12))
        // datedWindows spreads across 6 days, so 6 date headings.
        const headings = screen.getAllByText(/\d{4}$/)
        expect(headings.length).toBeGreaterThanOrEqual(6)
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
            within(card as HTMLElement).getByRole("button", { name: /filter by date range/i })
        ).toBeInTheDocument()
    })
})
