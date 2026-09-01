import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import Undeliverables from "@/pages/Undeliverables"
import { useAuth } from "@/context/AuthContext"
import { listUndeliverables, retryUndeliverable } from "@/lib/undeliverables-api"

vi.mock("@/context/AuthContext", () => ({ useAuth: vi.fn() }))
vi.mock("@/lib/undeliverables-api", () => ({
    listUndeliverables: vi.fn(),
    retryUndeliverable: vi.fn(),
    dismissUndeliverable: vi.fn(),
}))
vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn() },
}))

const auth = useAuth as ReturnType<typeof vi.fn>
const list = listUndeliverables as ReturnType<typeof vi.fn>
const retry = retryUndeliverable as ReturnType<typeof vi.fn>

const event = {
    id: "event-1",
    source: "workflow_dispatch",
    event_type: "dispatch_workflow_timer",
    status: "open",
    attempts: 4,
    last_error: "Provider unavailable",
    payload_hash: "hash",
    redacted_payload: { run_id: "run-123" },
    institution_id: "inst-1",
    location_id: "loc-1",
    created_at: "2026-09-01T12:00:00Z",
    updated_at: "2026-09-01T12:00:00Z",
    resolved_at: null,
    resolution_reason: null,
    resolution_note: null,
    replay_supported: true,
    originating_run_id: "run-123",
}

beforeEach(() => {
    auth.mockReset()
    list.mockReset()
    retry.mockReset()
    auth.mockReturnValue({ user: { role: "INSTITUTION_ADMIN" } })
    list.mockResolvedValue({ items: [event], total: 1, page: 1, size: 50, pages: 1 })
})

describe("Undeliverable work", () => {
    it("shows the cause and originating campaign run", async () => {
        render(<Undeliverables />)

        expect(await screen.findByText("Provider unavailable")).toBeInTheDocument()
        expect(screen.getByText("run-123")).toBeInTheDocument()
        expect(screen.getByText("4 attempts")).toBeInTheDocument()
    })

    it("disables the retry immediately so a double click queues once", async () => {
        let finish!: () => void
        retry.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
        render(<Undeliverables />)

        const button = await screen.findByRole("button", { name: "Retry" })
        fireEvent.click(button)
        expect(button).toBeDisabled()
        fireEvent.click(button)
        expect(retry).toHaveBeenCalledTimes(1)

        finish()
        await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    })

    it("lets a location admin investigate but not replay", async () => {
        auth.mockReturnValue({ user: { role: "LOCATION_ADMIN" } })
        render(<Undeliverables />)

        const button = await screen.findByRole("button", { name: "Retry" })
        expect(button).toBeDisabled()
        expect(button).toHaveAttribute("title", "An institution administrator is required to retry")
    })
})
