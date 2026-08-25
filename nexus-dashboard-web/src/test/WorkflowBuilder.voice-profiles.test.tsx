import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import WorkflowBuilder from "@/pages/WorkflowBuilder"
import { getChannelReadiness, getWorkflow, previewLaunchChecklist } from "@/lib/workflow-api"
import { listOutboundVoiceProfiles } from "@/lib/outbound-voice-api"
import type { AutomationWorkflow, OutboundVoiceProfile } from "@/types"

vi.mock("@/lib/workflow-api", () => ({
    getWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
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
    listOutboundVoiceProfiles: vi.fn(),
}))
vi.mock("@/components/workflow/StepConfigPanel", () => ({
    default: ({ voiceProfiles }: { voiceProfiles: OutboundVoiceProfile[] }) => (
        <div data-testid="voice-profiles">
            {voiceProfiles.map((profile) => profile.display_name).join(",")}
        </div>
    ),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const get = getWorkflow as ReturnType<typeof vi.fn>
const readiness = getChannelReadiness as ReturnType<typeof vi.fn>
const profiles = listOutboundVoiceProfiles as ReturnType<typeof vi.fn>
const previewChecklist = previewLaunchChecklist as ReturnType<typeof vi.fn>

const WORKFLOW: AutomationWorkflow = {
    id: "wf-voice",
    name: "Voice Campaign",
    status: "active",
    trigger_type: "appointment_offset",
    location_id: "loc-1",
    definition: {
        schema_version: "1.0",
        trigger: { type: "appointment_offset", offset_hours: 0 },
        entry_node_id: "voice-1",
        nodes: [
            {
                type: "send_voice",
                id: "voice-1",
                voice_profile_id: "profile-1",
                retell_agent_id: "",
                next_node_id: "exit-1",
            },
            { type: "exit", id: "exit-1", outcome: "done" },
        ],
    } as unknown as Record<string, unknown>,
    current_version_id: "v-1",
    created_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
}

beforeEach(() => {
    get.mockReset()
    readiness.mockReset()
    profiles.mockReset()
    previewChecklist.mockReset()
    get.mockResolvedValue(WORKFLOW)
    previewChecklist.mockResolvedValue(null)
    localStorage.clear()
})

describe("WorkflowBuilder voice profile loading", () => {
    it("keeps voice profiles when channel readiness fails", async () => {
        readiness.mockRejectedValue(new Error("readiness unavailable"))
        profiles.mockResolvedValue([
            { id: "profile-1", display_name: "Pre-appointment", purpose: "reminder" },
        ])

        render(
            <MemoryRouter initialEntries={["/institution-admin/campaigns/wf-voice/builder"]}>
                <Routes>
                    <Route path="/institution-admin/campaigns/:id/builder" element={<WorkflowBuilder />} />
                </Routes>
            </MemoryRouter>,
        )

        await waitFor(() => {
            expect(screen.getByTestId("voice-profiles")).toHaveTextContent("Pre-appointment")
        })
    })
})
