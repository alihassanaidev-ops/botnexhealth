import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

import SchedulerCalendar from "@/components/scheduling/SchedulerCalendar"
import { listAvailabilities } from "@/lib/tenant-api"
import type { CachedAvailability } from "@/types"

vi.mock("@/lib/tenant-api", () => ({
    listAvailabilities: vi.fn(),
    updateAvailability: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn() },
}))

const today = new Intl.DateTimeFormat("en-CA", { timeZone: "UTC" }).format(new Date())

function availability(overrides: Partial<CachedAvailability>): CachedAvailability {
    return {
        id: "window-1",
        source_id: "gt-1",
        provider_source_id: "gt-1",
        provider_name: "Dr Smith",
        operatory_source_id: "gt-1",
        operatory_name: "Room 1",
        begin_time: "09:00",
        end_time: "12:00",
        days: null,
        specific_date: today,
        appointment_type_ids: ["type-1"],
        appointment_type_names: ["Cleaning"],
        active: true,
        synced: true,
        status: "open",
        label_name: null,
        is_bookable_window: true,
        types_overridden: false,
        source_metadata: null,
        synced_at: null,
        ...overrides,
    }
}

describe("GoTracker closed periods in the scheduler calendar", () => {
    beforeEach(() => {
        vi.mocked(listAvailabilities).mockResolvedValue([
            availability({
                id: "closed:today:gt-1:gt-1:00:00:00:09:00:00",
                source_id: "closed:today:gt-1:gt-1:00:00:00:09:00:00",
                begin_time: "00:00",
                end_time: "09:00",
                appointment_type_ids: [],
                appointment_type_names: [],
                synced: false,
                status: "closed",
                is_bookable_window: false,
            }),
            availability({}),
        ])
    })

    it("requests and renders derived closed periods as non-interactive tiles", async () => {
        render(
            <SchedulerCalendar
                locationId="location-1"
                operatories={[]}
                appointmentTypes={[]}
                canManage
                pmsSource="gotracker"
                timezone="UTC"
            />
        )

        await waitFor(() => expect(screen.getByText("Closed")).toBeInTheDocument())
        expect(listAvailabilities).toHaveBeenCalledWith(
            "location-1", undefined, { includeClosed: true }
        )
        expect(screen.getByText("Closed").closest("button")).toBeNull()
        expect(screen.getByText("Read-only")).toBeInTheDocument()
        expect(screen.getByText("Working windows").parentElement).toHaveTextContent("1")
    })
})
