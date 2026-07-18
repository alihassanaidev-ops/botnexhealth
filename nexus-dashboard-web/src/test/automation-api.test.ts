import { describe, it, expect, beforeEach, vi } from "vitest"
import api from "@/lib/api"
import {
    activateOutboundHalt,
    cancelCampaignRun,
    enrollContactInCampaign,
    emergencyHaltCampaign,
    getCampaignOperations,
    getCampaignOverview,
    getOutboundHaltStatus,
    getRunTimeline,
    getUsageByCampaign,
    getUsageSummary,
    listCampaignRuns,
    releaseOutboundHalt,
} from "@/lib/automation-api"

vi.mock("@/lib/api", () => ({
    default: {
        get: vi.fn(),
        post: vi.fn(),
        delete: vi.fn(),
    },
}))

const get = api.get as ReturnType<typeof vi.fn>
const post = api.post as ReturnType<typeof vi.fn>
const del = api.delete as ReturnType<typeof vi.fn>

beforeEach(() => {
    get.mockReset()
    post.mockReset()
    del.mockReset()
})

describe("automation-api", () => {
    it("lists campaign runs with a bounded limit", async () => {
        get.mockResolvedValue({ data: { items: [], limit: 25, next_cursor: null } })
        await listCampaignRuns("wf-1", { limit: 25 })
        expect(get).toHaveBeenCalledWith("/automation/workflows/wf-1/runs?limit=25")
    })

    it("lists campaign runs with operational filters", async () => {
        get.mockResolvedValue({ data: { items: [], limit: 50, next_cursor: "next" } })
        await listCampaignRuns("wf-1", {
            status: "waiting",
            channel: "sms",
            outcome: "booked",
            current_node: "wait-1",
            contact_search: "Jordan",
            failure_reason: "blocked",
            cursor: "cursor-1",
        })
        expect(get).toHaveBeenCalledWith(
            "/automation/workflows/wf-1/runs?limit=50&status=waiting&outcome=booked&current_node=wait-1&channel=sms&failure_reason=blocked&contact_search=Jordan&cursor=cursor-1",
        )
    })

    it("fetches campaign overview, operations, and run timeline", async () => {
        get.mockResolvedValueOnce({ data: { workflow_id: "wf-1" } })
        get.mockResolvedValueOnce({ data: { failed_sends: [] } })
        get.mockResolvedValueOnce({ data: { items: [] } })

        await getCampaignOverview("wf-1")
        await getCampaignOperations("wf-1")
        await getRunTimeline("wf-1", "run-1")

        expect(get).toHaveBeenNthCalledWith(1, "/automation/workflows/wf-1/overview")
        expect(get).toHaveBeenNthCalledWith(2, "/automation/workflows/wf-1/operations")
        expect(get).toHaveBeenNthCalledWith(3, "/automation/workflows/wf-1/runs/run-1/timeline")
    })

    it("cancels a campaign run", async () => {
        post.mockResolvedValue({ data: { id: "run-1", status: "cancelled" } })
        const run = await cancelCampaignRun("wf-1", "run-1")
        expect(post).toHaveBeenCalledWith("/automation/workflows/wf-1/runs/run-1/cancel")
        expect(run.status).toBe("cancelled")
    })

    it("enrolls one contact in a campaign", async () => {
        const now = vi.spyOn(Date, "now").mockReturnValue(12345)
        post.mockResolvedValue({ data: { id: "run-1", status: "running" } })
        await enrollContactInCampaign("wf-1", "contact-1")
        expect(post).toHaveBeenCalledWith("/automation/workflows/wf-1/enroll", {
            contact_id: "contact-1",
            idempotency_key: "manual:wf-1:contact-1:12345",
            trigger_metadata: { source: "manual_ui" },
        })
        now.mockRestore()
    })

    it("fetches usage summary with a date range", async () => {
        get.mockResolvedValue({ data: { total_cost: 0, channels: [] } })
        await getUsageSummary({ startDate: "2026-07-01", endDate: "2026-07-08" })
        expect(get).toHaveBeenCalledWith(
            "/institution/usage/summary?start_date=2026-07-01&end_date=2026-07-08",
        )
    })

    it("fetches usage by campaign with limit", async () => {
        get.mockResolvedValue({ data: { campaigns: [] } })
        await getUsageByCampaign(undefined, 200)
        expect(get).toHaveBeenCalledWith("/institution/usage/by-campaign?limit=200")
    })

    it("uses outbound halt endpoints", async () => {
        get.mockResolvedValue({ data: { halted: false } })
        post.mockResolvedValue({ data: { halted: true } })
        del.mockResolvedValue({ data: { halted: false } })

        await getOutboundHaltStatus()
        await activateOutboundHalt("stop sends")
        await releaseOutboundHalt()

        expect(get).toHaveBeenCalledWith("/automation/workflows/outbound-halt")
        expect(post).toHaveBeenCalledWith("/automation/workflows/outbound-halt", {
            reason: "stop sends",
        })
        expect(del).toHaveBeenCalledWith("/automation/workflows/outbound-halt")
    })

    it("halts a single campaign", async () => {
        post.mockResolvedValue({ data: { workflow_id: "wf-1", halted_runs: 2, status: "paused" } })
        await emergencyHaltCampaign("wf-1", "unsafe")
        expect(post).toHaveBeenCalledWith("/automation/workflows/wf-1/emergency-halt", {
            reason: "unsafe",
        })
    })
})
