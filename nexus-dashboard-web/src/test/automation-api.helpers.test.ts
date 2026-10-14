import { describe, expect, it } from "vitest"
import { campaignActionError, isRecallCampaign } from "@/lib/automation-api"
import type { AutomationWorkflow } from "@/types"

function apiError(detail: unknown) {
    return { response: { data: { detail } } }
}

describe("campaignActionError", () => {
    it("shows the launch-checklist blockers that stopped a resume", () => {
        const error = apiError({
            message: "Launch checklist has blockers; workflow cannot be resumed.",
            blockers: ["channel_provisioning"],
            blocker_messages: ["Channel provisioning: Voice is not provisioned for this location."],
        })
        expect(campaignActionError(error)).toBe(
            "Channel provisioning: Voice is not provisioned for this location.",
        )
    })

    it("passes a plain server reason through", () => {
        expect(campaignActionError(apiError("Only paused workflows can be resumed."))).toBe(
            "Only paused workflows can be resumed.",
        )
    })

    it("returns null when the server gave no reason", () => {
        expect(campaignActionError(new Error("Network Error"))).toBeNull()
    })
})

describe("isRecallCampaign", () => {
    function workflow(definition: Record<string, unknown> | null): AutomationWorkflow {
        return {
            id: "wf-1",
            name: "Recall",
            status: "paused",
            trigger_type: "schedule",
            definition,
            current_version_id: "v1",
            created_at: "",
            updated_at: "",
        }
    }

    it("recognises a schedule sourced from the PMS recall list", () => {
        expect(
            isRecallCampaign(workflow({ trigger: { type: "schedule", source: { kind: "pms_recall" } } })),
        ).toBe(true)
    })

    it("does not treat other campaigns as recall", () => {
        expect(isRecallCampaign(workflow({ trigger: { type: "event" } }))).toBe(false)
        expect(isRecallCampaign(workflow(null))).toBe(false)
    })
})
