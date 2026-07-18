import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CampaignDetail from "@/pages/CampaignDetail"
import {
    enrollCampaignAudience,
    getCampaign,
    getCampaignAudience,
    getCampaignAnalytics,
    getCampaignOperations,
    getCampaignOverview,
    getUsageByCampaign,
    getUsageSummary,
    listCampaignRuns,
    previewCampaignAudience,
    saveCampaignAudience,
} from "@/lib/automation-api"

vi.mock("@/lib/automation-api", () => ({
    getCampaign: vi.fn(),
    getCampaignOverview: vi.fn(),
    getCampaignAnalytics: vi.fn(),
    listCampaignRuns: vi.fn(),
    getCampaignOperations: vi.fn(),
    getUsageSummary: vi.fn(),
    getUsageByCampaign: vi.fn(),
    getCampaignAudience: vi.fn(),
    saveCampaignAudience: vi.fn(),
    previewCampaignAudience: vi.fn(),
    enrollCampaignAudience: vi.fn(),
    pauseCampaign: vi.fn(),
    resumeCampaign: vi.fn(),
    archiveCampaign: vi.fn(),
    enrollContactInCampaign: vi.fn(),
    cancelCampaignRun: vi.fn(),
    emergencyHaltCampaign: vi.fn(),
    getRunTimeline: vi.fn(),
}))
vi.mock("@/lib/contacts-api", () => ({ listContacts: vi.fn() }))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const campaign = getCampaign as ReturnType<typeof vi.fn>
const overview = getCampaignOverview as ReturnType<typeof vi.fn>
const analytics = getCampaignAnalytics as ReturnType<typeof vi.fn>
const runs = listCampaignRuns as ReturnType<typeof vi.fn>
const operations = getCampaignOperations as ReturnType<typeof vi.fn>
const usageSummary = getUsageSummary as ReturnType<typeof vi.fn>
const usageByCampaign = getUsageByCampaign as ReturnType<typeof vi.fn>
const audience = getCampaignAudience as ReturnType<typeof vi.fn>
const previewAudience = previewCampaignAudience as ReturnType<typeof vi.fn>
const saveAudience = saveCampaignAudience as ReturnType<typeof vi.fn>
const enrollAudience = enrollCampaignAudience as ReturnType<typeof vi.fn>

beforeEach(() => {
    campaign.mockReset()
    overview.mockReset()
    analytics.mockReset()
    runs.mockReset()
    operations.mockReset()
    usageSummary.mockReset()
    usageByCampaign.mockReset()
    audience.mockReset()
    previewAudience.mockReset()
    saveAudience.mockReset()
    enrollAudience.mockReset()

    campaign.mockResolvedValue({
        id: "wf-1",
        name: "Recall campaign",
        status: "active",
        trigger_type: "recall_scan",
        definition: null,
        current_version_id: "ver-1",
        created_at: "2026-07-01T00:00:00Z",
        updated_at: "2026-07-01T00:00:00Z",
    })
    overview.mockResolvedValue({
        workflow_id: "wf-1",
        workflow_name: "Recall campaign",
        workflow_status: "active",
        trigger_type: "recall_scan",
        location_id: "loc-1",
        latest_version: null,
        readiness: {
            overall_status: "pass",
            blockers_count: 0,
            warnings_count: 0,
            unknown_count: 0,
            estimate_basis: "unknown",
            generated_at: "2026-07-18T00:00:00Z",
        },
        channels: ["sms"],
        run_counts: { running: 0, waiting: 0, pending: 0, completed: 2 },
        outcome_counts: {},
        response_counts: {},
        open_handoff_count: 1,
        channel_attempts: {},
        recent_outcomes: [],
        generated_at: "2026-07-18T00:00:00Z",
    })
    analytics.mockResolvedValue({
        workflow_id: "wf-1",
        workflow_name: "Recall campaign",
        category: "recall",
        start_date: "2026-06-19",
        end_date: "2026-07-18",
        summary: {
            enrollments: 12,
            sms_sent: 10,
            sms_delivered: 9,
            sms_failed: 1,
            sms_replied: 4,
            voice_attempted: 0,
            voice_answered: 0,
            email_sent: 0,
            email_clicked: 0,
            confirmed: 0,
            booked: 3,
            staff_handoff: 1,
        },
        channels: [
            { channel: "sms", attempted: 10, delivered: 9, failed: 1, responded: 4 },
        ],
        outcomes: [
            {
                key: "booked",
                label: "Recall Booked",
                group: "success",
                count: 3,
                rate: 0.25,
                description: "Patient booked from recall outreach.",
            },
        ],
        trend: [
            {
                date: "2026-07-18",
                enrollments: 12,
                sends: 10,
                responses: 4,
                confirmed: 0,
                booked: 3,
                handoffs: 1,
                total_cost: 8.5,
            },
        ],
        cost: {
            currency: "USD",
            total_cost: 8.5,
            cost_per_booking: 2.83333,
            cost_per_confirmation: null,
        },
        generated_at: "2026-07-18T00:00:00Z",
        rollup_fresh_at: "2026-07-18T00:05:00Z",
    })
    runs.mockResolvedValue({ items: [], limit: 50, next_cursor: null })
    operations.mockResolvedValue({
        stuck_waiting_runs: [],
        failed_sends: [],
        suppressed_skipped_runs: [],
        open_handoffs: [],
        generated_at: "2026-07-18T00:00:00Z",
    })
    usageSummary.mockResolvedValue({ currency: "USD", total_cost: 8.5, channels: [] })
    usageByCampaign.mockResolvedValue({ campaigns: [] })
    audience.mockResolvedValue({
        workflow_id: "wf-1",
        location_id: "loc-1",
        segment: {
            has_no_future_appointment: true,
            contact_channel_available: ["sms"],
        },
        exclusions: {
            no_consent: true,
            do_not_contact: true,
            suppressed: true,
            contacted_within_days: 1,
            max_contacts_per_rolling_7_days: 3,
            already_enrolled_active: true,
            already_booked: true,
            missing_required_merge_context: true,
        },
        persisted: true,
        updated_at: "2026-07-18T00:00:00Z",
    })
    previewAudience.mockResolvedValue({
        preview_id: "prev-1",
        workflow_id: "wf-1",
        workflow_version_id: "ver-1",
        location_id: "loc-1",
        segment: {},
        exclusions: {},
        total_candidates: 9,
        included_count: 6,
        excluded_count: 3,
        counts_by_reason: { do_not_contact: 2, already_booked: 1 },
        samples: [
            {
                contact_id: "c-1",
                display_name: "Jordan Rivera",
                phone_masked: "(***) ***-1010",
                email_masked: null,
                status: "included",
                reasons: [],
            },
            {
                contact_id: "c-2",
                display_name: "Taylor Kim",
                phone_masked: "(***) ***-2020",
                email_masked: null,
                status: "excluded",
                reasons: ["do_not_contact"],
            },
        ],
        warnings: ["NexHealth unsubscribe hints are not projected yet."],
        estimate_basis: "Computed from local contacts.",
        generated_at: "2026-07-18T00:00:00Z",
        expires_at: "2026-07-18T00:30:00Z",
    })
    saveAudience.mockResolvedValue({})
    enrollAudience.mockResolvedValue({
        workflow_id: "wf-1",
        workflow_version_id: "ver-1",
        preview_id: "prev-1",
        enqueued: 6,
        skipped: 0,
        counts_by_reason: {},
    })
})

describe("CampaignDetail analytics tab", () => {
    it("renders normalized outcome labels and cost per result", async () => {
        const user = userEvent.setup()
        render(
            <MemoryRouter initialEntries={["/campaigns/wf-1"]}>
                <Routes>
                    <Route path="/campaigns/:id" element={<CampaignDetail />} />
                </Routes>
            </MemoryRouter>,
        )

        await screen.findByText("Recall campaign")
        await user.click(screen.getByRole("tab", { name: "Analytics" }))

        expect(await screen.findByText("Recall Booked")).toBeInTheDocument()
        expect(screen.getByText("25%")).toBeInTheDocument()
        expect(screen.getByText("Cost per booking")).toBeInTheDocument()
        expect(screen.getByText("$2.83")).toBeInTheDocument()
        expect(screen.getByText("Channel funnel")).toBeInTheDocument()
        expect(screen.getByText("2026-07-18")).toBeInTheDocument()
    })
})

describe("CampaignDetail audience tab", () => {
    it("previews counts, exclusions, and masked samples", async () => {
        const user = userEvent.setup()
        render(
            <MemoryRouter initialEntries={["/campaigns/wf-1"]}>
                <Routes>
                    <Route path="/campaigns/:id" element={<CampaignDetail />} />
                </Routes>
            </MemoryRouter>,
        )

        await screen.findByText("Recall campaign")
        await user.click(screen.getByRole("tab", { name: "Audience" }))
        await user.click(screen.getByRole("button", { name: "Preview" }))

        expect(await screen.findByText("Jordan Rivera")).toBeInTheDocument()
        expect(screen.getByText("Taylor Kim")).toBeInTheDocument()
        expect(screen.getAllByText("do not contact").length).toBeGreaterThanOrEqual(1)
        expect(screen.getByText("already booked")).toBeInTheDocument()
        expect(screen.getByText("(***) ***-1010")).toBeInTheDocument()
        expect(screen.queryByText("+15550101010")).not.toBeInTheDocument()
    })
})
