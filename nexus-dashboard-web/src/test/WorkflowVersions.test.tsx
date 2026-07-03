import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import WorkflowVersions from "@/pages/WorkflowVersions"
import { getWorkflow, listVersions } from "@/lib/workflow-api"
import type { AutomationWorkflow } from "@/types"
import type { WorkflowVersion } from "@/types/workflow"

vi.mock("@/lib/workflow-api", () => ({
    getWorkflow: vi.fn(),
    listVersions: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))
// Canvas pulls in reactflow measurement APIs jsdom lacks; stub it.
vi.mock("@/components/workflow/WorkflowCanvas", () => ({
    default: () => <div data-testid="canvas" />,
}))

const getWf = getWorkflow as ReturnType<typeof vi.fn>
const getVersions = listVersions as ReturnType<typeof vi.fn>

const WF: AutomationWorkflow = {
    id: "wf-1",
    name: "My Campaign",
    status: "active",
    trigger_type: "manual",
    definition: null,
    current_version_id: "v-2",
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:00:00Z",
}

const DEF = {
    schema_version: "1.0" as const,
    trigger: { type: "manual" as const },
    entry_node_id: "exit-1",
    nodes: [{ type: "exit" as const, id: "exit-1", outcome: "done" }],
}

const VERSIONS: WorkflowVersion[] = [
    {
        id: "v-2",
        workflow_id: "wf-1",
        version_number: 2,
        definition: DEF,
        definition_checksum: "abcdef12345",
        content_classification: "recall",
        published_by_user_id: "u1",
        published_at: "2026-07-02T10:00:00Z",
        created_at: "2026-07-02T10:00:00Z",
        is_current: true,
    },
    {
        id: "v-1",
        workflow_id: "wf-1",
        version_number: 1,
        definition: DEF,
        definition_checksum: "998877",
        content_classification: null,
        published_by_user_id: "u1",
        published_at: "2026-07-01T10:00:00Z",
        created_at: "2026-07-01T10:00:00Z",
        is_current: false,
    },
]

function renderPage() {
    return render(
        <MemoryRouter initialEntries={["/institution-admin/campaigns/wf-1/versions"]}>
            <Routes>
                <Route path="/institution-admin/campaigns/:id/versions" element={<WorkflowVersions />} />
            </Routes>
        </MemoryRouter>,
    )
}

beforeEach(() => {
    getWf.mockReset()
    getVersions.mockReset()
})

describe("WorkflowVersions page", () => {
    it("lists real versions with number, current badge and content class", async () => {
        getWf.mockResolvedValue(WF)
        getVersions.mockResolvedValue(VERSIONS)
        renderPage()

        // "Version 2" appears in both the list row and the selected-version card.
        expect((await screen.findAllByText("Version 2")).length).toBeGreaterThan(0)
        expect(screen.getByText("Version 1")).toBeInTheDocument()
        expect(getVersions).toHaveBeenCalledWith("wf-1")
        // Current badge + content classification label render.
        expect(screen.getByText(/current/i)).toBeInTheDocument()
        expect(screen.getByText("Recall")).toBeInTheDocument()
    })

    it("shows an empty state when there are no versions", async () => {
        getWf.mockResolvedValue(WF)
        getVersions.mockResolvedValue([])
        renderPage()
        await waitFor(() =>
            expect(screen.getByText(/no published versions yet/i)).toBeInTheDocument(),
        )
    })
})
