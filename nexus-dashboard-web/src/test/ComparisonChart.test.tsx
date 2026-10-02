import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ComparisonChart } from "@/components/dashboard/ComparisonChart"

// Recharts needs a real box to lay out in; jsdom reports zero.
vi.mock("@/components/ui/chart", async () => {
    const actual = await vi.importActual<Record<string, unknown>>("@/components/ui/chart")
    return {
        ...actual,
        ChartContainer: ({ children }: { children: React.ReactNode }) => (
            <div style={{ width: 600, height: 300 }}>{children}</div>
        ),
    }
})

const ROWS = [
    { id: "a", label: "Downtown", values: { calls: 40, rate: 60 } },
    { id: "b", label: "Riverside", values: { calls: 120, rate: 40 } },
]
const METRICS = [
    { key: "calls", label: "Calls" },
    { key: "rate", label: "Booking Rate", suffix: "%" },
]

describe("ComparisonChart", () => {
    it("sums a count but averages a rate, and says which", async () => {
        // Rates do not add up to anything; presenting them as a total was the
        // reading the old donut invited.
        const user = userEvent.setup()
        render(<ComparisonChart title="Clinic Comparison" rows={ROWS} metrics={METRICS} />)
        expect(screen.getByText("160 total")).toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: "Booking Rate" }))
        expect(screen.getByText("50% average")).toBeInTheDocument()
        expect(screen.queryByText(/total/)).not.toBeInTheDocument()
    })

    it("offers every metric and renders the clinic labels", () => {
        render(<ComparisonChart title="Clinic Comparison" rows={ROWS} metrics={METRICS} />)

        expect(screen.getByRole("button", { name: "Calls" })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Booking Rate" })).toBeInTheDocument()
        expect(screen.getByText("Clinic Comparison")).toBeInTheDocument()
    })

    it("shows the empty state rather than an empty chart", () => {
        render(
            <ComparisonChart
                title="Clinic Comparison"
                rows={[]}
                metrics={METRICS}
                emptyText="No location data yet."
            />,
        )
        expect(screen.getByText("No location data yet.")).toBeInTheDocument()
    })
})
