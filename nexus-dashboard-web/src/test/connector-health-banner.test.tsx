import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/context/LocationContext", () => ({
    useLocationContext: () => ({ selectedLocationId: "loc-1" }),
}))

vi.mock("@/lib/connector-health-api", () => ({
    getConnectorHealth: vi.fn(),
}))

import { getConnectorHealth, type ConnectorHealth } from "@/lib/connector-health-api"
import { ConnectorHealthBanner } from "@/components/dashboard/ConnectorHealthBanner"

const mockGet = getConnectorHealth as ReturnType<typeof vi.fn>

function health(over: Partial<ConnectorHealth> = {}): ConnectorHealth {
    return {
        applicable: true,
        status: "online",
        warning: false,
        message: "Your practice system connection is active.",
        connector_version: "2.2.1",
        last_report_at: null,
        seconds_since_last_report: null,
        queue_depth: null,
        failed_write_count: null,
        conflict_count: null,
        ...over,
    }
}

beforeEach(() => mockGet.mockReset())

describe("ConnectorHealthBanner", () => {
    it("warns clinic staff when the connection is down", async () => {
        mockGet.mockResolvedValue(
            health({ status: "offline", warning: true, message: "Bookings may not sync." }),
        )
        render(<ConnectorHealthBanner />)
        await waitFor(() =>
            expect(screen.getByTestId("connector-health-warning")).toBeTruthy(),
        )
        expect(screen.getByText("Bookings may not sync.")).toBeTruthy()
    })

    it("shows nothing when the connection is healthy", async () => {
        mockGet.mockResolvedValue(health())
        render(<ConnectorHealthBanner />)
        await waitFor(() => expect(mockGet).toHaveBeenCalled())
        expect(screen.queryByTestId("connector-health-warning")).toBeNull()
    })

    it("shows nothing for a clinic with no connector", async () => {
        mockGet.mockResolvedValue(health({ applicable: false, status: "not_applicable" }))
        render(<ConnectorHealthBanner />)
        await waitFor(() => expect(mockGet).toHaveBeenCalled())
        expect(screen.queryByTestId("connector-health-warning")).toBeNull()
    })
})
