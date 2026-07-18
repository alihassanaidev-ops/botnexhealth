import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import WorkflowBuilder from "@/pages/WorkflowBuilder"
import {
    getWorkflow,
    previewLaunchChecklist,
    updateWorkflow,
    validateDefinition,
} from "@/lib/workflow-api"
import type { AutomationWorkflow } from "@/types"

vi.mock("@/lib/workflow-api", () => ({
    getWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    pauseWorkflow: vi.fn(),
    resumeWorkflow: vi.fn(),
    archiveWorkflow: vi.fn(),
    validateDefinition: vi.fn(),
    getChannelReadiness: vi.fn(),
    previewLaunchChecklist: vi.fn(),
    listMergeFields: vi.fn().mockResolvedValue([]),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const get = getWorkflow as ReturnType<typeof vi.fn>
const patch = updateWorkflow as ReturnType<typeof vi.fn>
const validate = validateDefinition as ReturnType<typeof vi.fn>
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
    get.mockReset()
    patch.mockReset()
    validate.mockReset()
    previewChecklist.mockReset()
    previewChecklist.mockResolvedValue(LAUNCH_CHECKLIST)
    localStorage.clear()
})

describe("WorkflowBuilder publish — authoritative backend validation", () => {
    it("blocks publish when the backend returns a severity=error issue", async () => {
        get.mockResolvedValue(WORKFLOW)
        validate.mockResolvedValue({
            valid: false,
            issues: [
                {
                    node_id: "exit-1",
                    severity: "error",
                    message: "Consent is required for this channel.",
                    code: "consent_required",
                },
            ],
        })
        const user = userEvent.setup()
        renderBuilder()
        await makeDirtyAndOpenPublish(user)

        await waitFor(() => expect(validate).toHaveBeenCalled())
        // Publish is blocked — no PATCH — and the server issue is surfaced.
        expect(patch).not.toHaveBeenCalled()
        expect(await screen.findByText("Consent is required for this channel.")).toBeInTheDocument()
        expect(screen.getByText(/server & compliance checks/i)).toBeInTheDocument()
    })

    it("proceeds to publish when the backend validation passes", async () => {
        get.mockResolvedValue(WORKFLOW)
        validate.mockResolvedValue({ valid: true, issues: [] })
        patch.mockResolvedValue({ ...WORKFLOW, name: "My Reminder Campaign!" })
        const user = userEvent.setup()
        renderBuilder()
        await makeDirtyAndOpenPublish(user)

        await waitFor(() => expect(validate).toHaveBeenCalled())
        await waitFor(() => expect(patch).toHaveBeenCalled())
    })
})
