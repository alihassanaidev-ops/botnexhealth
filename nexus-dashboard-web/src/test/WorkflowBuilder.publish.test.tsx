import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import WorkflowBuilder from "@/pages/WorkflowBuilder"
import {
    getWorkflow,
    previewLaunchChecklist,
    publishWorkflow,
} from "@/lib/workflow-api"
import { toast } from "sonner"
import type { AutomationWorkflow } from "@/types"

vi.mock("@/lib/workflow-api", () => ({
    getWorkflow: vi.fn(),
    publishWorkflow: vi.fn(),
    pauseWorkflow: vi.fn(),
    resumeWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
    validateDefinition: vi.fn().mockResolvedValue({ valid: true, issues: [] }),
    listNodeCapabilities: vi.fn().mockResolvedValue({ registry_version: "1.0", nodes: [] }),
    listPhoneCountryRegions: vi.fn().mockResolvedValue([]),
    getChannelReadiness: vi.fn(),
    previewLaunchChecklist: vi.fn(),
    listMergeFields: vi.fn().mockResolvedValue([]),
}))
vi.mock("@/lib/outbound-voice-api", () => ({
    listOutboundVoiceProfiles: vi.fn().mockResolvedValue([]),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const get = getWorkflow as ReturnType<typeof vi.fn>
const publish = publishWorkflow as ReturnType<typeof vi.fn>
const previewChecklist = previewLaunchChecklist as ReturnType<typeof vi.fn>

// A well-formed workflow: no client-side validation errors.
const WORKFLOW: AutomationWorkflow = {
    id: "wf-1",
    name: "My Reminder Campaign",
    status: "active",
    trigger_type: "manual",
    definition: {
        schema_version: "1.0",
        trigger: { type: "manual" },
        entry_node_id: "exit-1",
        nodes: [{ type: "exit", id: "exit-1", outcome: "sent" }],
    } as unknown as Record<string, unknown>,
    current_version_id: "v-1",
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:00:00Z",
}

const LAUNCH_CHECKLIST = {
    workflow_id: "wf-1",
    workflow_version_id: "v-1",
    location_id: null,
    overall_status: "warning",
    blockers_count: 0,
    warnings_count: 1,
    unknown_count: 1,
    estimated_audience: null,
    estimated_send_volume: null,
    estimated_cost_cents: null,
    estimate_basis: "Audience preview is not available yet.",
    generated_at: "2026-07-18T00:00:00Z",
    items: [
        {
            id: "audience_estimate",
            section: "audience",
            label: "Audience estimate and exclusions",
            status: "warning",
            message: "Audience is selected at enrollment/import time.",
            fix_href: null,
            metadata: {},
        },
    ],
}

function renderBuilder() {
    return render(
        <MemoryRouter initialEntries={["/institution-admin/campaigns/wf-1/builder"]}>
            <Routes>
                <Route path="/institution-admin/campaigns/:id/builder" element={<WorkflowBuilder />} />
            </Routes>
        </MemoryRouter>,
    )
}

async function makeDirtyAndOpenPublish(user: ReturnType<typeof userEvent.setup>) {
    // Editing the name marks the buffer dirty, which enables the publish button.
    const nameInput = await screen.findByDisplayValue("My Reminder Campaign")
    await user.type(nameInput, "!")
    await user.click(screen.getByRole("button", { name: /publish changes/i }))
    // Confirm dialog → the actual publish action.
    await user.click(screen.getByRole("button", { name: /^publish$/i }))
}

beforeEach(() => {
    vi.clearAllMocks()
    get.mockReset()
    publish.mockReset()
    previewChecklist.mockReset()
    previewChecklist.mockResolvedValue(LAUNCH_CHECKLIST)
    localStorage.clear()
})

describe("WorkflowBuilder publish", () => {
    it("publishes through one authoritative command and clears the draft on a new version", async () => {
        get.mockResolvedValue(WORKFLOW)
        publish.mockResolvedValue({
            ...WORKFLOW,
            name: "My Reminder Campaign!",
            current_version_id: "v-2",
        })
        const user = userEvent.setup()
        renderBuilder()
        await makeDirtyAndOpenPublish(user)

        await waitFor(() => expect(publish).toHaveBeenCalledTimes(1))
        expect(get).toHaveBeenCalledTimes(1)
        expect(toast.success).toHaveBeenCalledWith("Changes published")
        expect(localStorage.getItem("nex.workflow-draft.wf-1")).toBeNull()
    }, 10000)

    it("surfaces the publish validation error and keeps the local draft", async () => {
        get.mockResolvedValue(WORKFLOW)
        publish.mockRejectedValue({
            response: { data: { detail: "Cannot publish: invalid condition." } },
        })
        const user = userEvent.setup()
        renderBuilder()
        await makeDirtyAndOpenPublish(user)

        await waitFor(() => expect(publish).toHaveBeenCalled())
        expect(toast.error).toHaveBeenCalledWith(
            "Couldn't publish: Cannot publish: invalid condition.",
        )
        expect(toast.success).not.toHaveBeenCalled()
        expect(screen.getByText(/unsaved/i)).toBeInTheDocument()
    }, 10000)

    it("does not report success or lose the route id when publish returns an invalid workflow", async () => {
        get.mockResolvedValue(WORKFLOW)
        publish.mockResolvedValue({
            ...WORKFLOW,
            id: undefined,
            current_version_id: "v-1",
        } as unknown as AutomationWorkflow)
        const user = userEvent.setup()
        renderBuilder()
        await makeDirtyAndOpenPublish(user)

        await waitFor(() => expect(publish).toHaveBeenCalled())
        expect(toast.success).not.toHaveBeenCalledWith("Changes published")
        expect(toast.error).toHaveBeenCalled()
        expect(screen.getByRole("link", { name: /versions/i })).toHaveAttribute(
            "href",
            "/institution-admin/campaigns/wf-1/versions",
        )
    }, 10000)
})
