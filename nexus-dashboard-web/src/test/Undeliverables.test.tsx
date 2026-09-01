import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import Undeliverables from "@/pages/Undeliverables"
import { useAuth } from "@/context/AuthContext"
import { dismissUndeliverable, listUndeliverables, retryUndeliverable } from "@/lib/undeliverables-api"

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
const dismiss = dismissUndeliverable as ReturnType<typeof vi.fn>

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
    originating_timer_id: "timer-123",
}

const duplicateEvent = {
    ...event,
    id: "event-2",
    created_at: "2026-09-01T11:57:00Z",
    updated_at: "2026-09-01T11:57:00Z",
}

beforeEach(() => {
    auth.mockReset()
    list.mockReset()
    retry.mockReset()
    dismiss.mockReset()
    auth.mockReturnValue({ user: { role: "INSTITUTION_ADMIN" } })
    list.mockResolvedValue({ items: [event], total: 1, page: 1, size: 50, pages: 1 })
})

describe("Automation issues", () => {
    it("labels the page as an automation operator queue", async () => {
        render(<Undeliverables />)

        expect(await screen.findByText("Automation issues")).toBeInTheDocument()
        expect(screen.getByText("Background actions that could not complete automatically and may need attention.")).toBeInTheDocument()
    })

    it("shows a human cause and related workflow before technical details", async () => {
        list.mockResolvedValue({
            items: [{
                ...event,
                last_error: "current transaction is aborted, commands ignored until end of transaction block",
            }],
            total: 1,
            page: 1,
            size: 50,
            pages: 1,
        })
        render(<Undeliverables />)

        expect(await screen.findByText("A previous database operation failed during this background action. The query in Technical details was attempted after the transaction was already unusable.")).toBeInTheDocument()
        expect(screen.getByText("Technical details")).toBeInTheDocument()
        expect(screen.getByText("run-123")).toBeInTheDocument()
        expect(screen.getByText("4 attempts")).toBeInTheDocument()
    })

    it("groups duplicate alerts for the same workflow timer", async () => {
        list.mockResolvedValue({ items: [event, duplicateEvent], total: 2, page: 1, size: 50, pages: 1 })
        render(<Undeliverables />)

        expect(await screen.findByText("2 related failures")).toBeInTheDocument()
        expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1)
        expect(screen.getByText("1 issue (2 events)")).toBeInTheDocument()
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

    it("routes super-admin actions to the platform queue", async () => {
        auth.mockReturnValue({ user: { role: "SUPER_ADMIN" } })
        render(<Undeliverables />)

        await screen.findByText("Provider unavailable")
        expect(list).toHaveBeenCalledWith("platform", { page: 1, size: 50, status: "open" })

        fireEvent.click(screen.getByRole("button", { name: "Retry" }))

        await waitFor(() => expect(retry).toHaveBeenCalledWith("platform", "event-1"))
    })

    it("dismisses with a bounded reason and optional note", async () => {
        dismiss.mockResolvedValue({ ...event, status: "discarded" })
        render(<Undeliverables />)

        fireEvent.click(await screen.findByRole("button", { name: "Mark resolved" }))
        fireEvent.change(screen.getByLabelText("Note (optional)"), {
            target: { value: "Handled directly in the PMS" },
        })
        fireEvent.click(screen.getByRole("button", { name: "Mark resolved" }))

        await waitFor(() => expect(dismiss).toHaveBeenCalledWith("institution", "event-1", {
            reason: "resolved_elsewhere",
            note: "Handled directly in the PMS",
        }))
        await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    })
})
