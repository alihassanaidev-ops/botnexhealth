import { describe, it, expect, beforeEach, vi } from "vitest"
import api from "@/lib/api"
import {
    createWorkflow,
    createWorkflowFromTemplate,
    dryRun,
    getChannelReadiness,
    getLaunchChecklist,
    getWorkflow,
    listMergeFields,
    listTemplates,
    listVersions,
    listWorkflows,
    publishWorkflow,
    previewLaunchChecklist,
    updateWorkflow,
    validateDefinition,
} from "@/lib/workflow-api"
import type { WorkflowDefinition } from "@/types/workflow"

vi.mock("@/lib/api", () => ({
    default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

const get = api.get as ReturnType<typeof vi.fn>
const post = api.post as ReturnType<typeof vi.fn>
const patch = api.patch as ReturnType<typeof vi.fn>

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "exit-1",
    nodes: [{ type: "exit", id: "exit-1", outcome: "done" }],
}

beforeEach(() => {
    get.mockReset()
    post.mockReset()
    patch.mockReset()
})

describe("workflow-api", () => {
    it("listWorkflows GETs the workflows collection", async () => {
        get.mockResolvedValue({ data: [] })
        await listWorkflows()
        expect(get).toHaveBeenCalledWith("/automation/workflows")
    })

    it("getWorkflow GETs by id", async () => {
        get.mockResolvedValue({ data: { id: "w1" } })
        const wf = await getWorkflow("w1")
        expect(get).toHaveBeenCalledWith("/automation/workflows/w1")
        expect(wf).toMatchObject({ id: "w1" })
    })

    it("createWorkflow POSTs name + definition", async () => {
        post.mockResolvedValue({ data: { id: "w2" } })
        await createWorkflow({ name: "Test", definition: DEF })
        expect(post).toHaveBeenCalledWith("/automation/workflows", { name: "Test", definition: DEF })
    })

    it("updateWorkflow PATCHes by id", async () => {
        patch.mockResolvedValue({ data: { id: "w1" } })
        await updateWorkflow("w1", { definition: DEF })
        expect(patch).toHaveBeenCalledWith("/automation/workflows/w1", { definition: DEF })
    })

    it("publishWorkflow POSTs the publish action", async () => {
        post.mockResolvedValue({ data: { id: "w1" } })
        await publishWorkflow("w1")
        expect(post).toHaveBeenCalledWith("/automation/workflows/w1/publish")
    })

    it("listTemplates GETs the templates collection", async () => {
        get.mockResolvedValue({ data: [] })
        await listTemplates()
        expect(get).toHaveBeenCalledWith("/automation/templates")
    })

    it("listTemplates forwards location_id when supplied", async () => {
        get.mockResolvedValue({ data: [] })
        await listTemplates("loc-1")
        expect(get).toHaveBeenCalledWith("/automation/templates?location_id=loc-1")
    })

    it("createWorkflowFromTemplate posts to the guided instantiate endpoint", async () => {
        post.mockResolvedValue({ data: { id: "w3", name: "Reminder" } })
        const wf = await createWorkflowFromTemplate("tpl-1")
        expect(post).toHaveBeenCalledWith("/automation/templates/tpl-1/instantiate", {})
        expect(wf).toMatchObject({ id: "w3" })
    })

    it("createWorkflowFromTemplate forwards guided setup values", async () => {
        post.mockResolvedValue({ data: { id: "w4" } })
        await createWorkflowFromTemplate("tpl-1", "  My Campaign  ", {
            locationId: "loc-1",
            voiceAgentId: " agent-1 ",
            setupOptions: { copy_variant: "standard" },
        })
        expect(post).toHaveBeenCalledWith("/automation/templates/tpl-1/instantiate", {
            name: "My Campaign",
            location_id: "loc-1",
            voice_agent_id: "agent-1",
            setup_options: { copy_variant: "standard" },
        })
    })

    it("listVersions GETs the workflow's versions collection", async () => {
        get.mockResolvedValue({ data: [{ id: "v1", version_number: 2, is_current: true }] })
        const versions = await listVersions("w1")
        expect(get).toHaveBeenCalledWith("/automation/workflows/w1/versions")
        expect(versions).toHaveLength(1)
    })

    it("validateDefinition POSTs the definition to the validate endpoint", async () => {
        post.mockResolvedValue({ data: { valid: true, issues: [] } })
        const res = await validateDefinition(DEF)
        expect(post).toHaveBeenCalledWith("/automation/workflows/validate", { definition: DEF })
        expect(res).toEqual({ valid: true, issues: [] })
    })

    it("dryRun POSTs the definition + condition_choices to the dry-run endpoint", async () => {
        post.mockResolvedValue({
            data: {
                steps: [{ node_id: "exit-1", node_type: "exit", summary: "Exit", detail: "done" }],
                outcome: "done",
                truncated: false,
            },
        })
        const res = await dryRun(DEF, { conditionChoices: { "cond-1": false } })
        expect(post).toHaveBeenCalledWith("/automation/workflows/dry-run", {
            definition: DEF,
            condition_choices: { "cond-1": false },
        })
        expect(res.outcome).toBe("done")
        expect(res.steps[0].node_id).toBe("exit-1")
    })

    it("dryRun defaults condition_choices to {} and includes context when given", async () => {
        post.mockResolvedValue({ data: { steps: [], outcome: null, truncated: false } })
        await dryRun(DEF, { context: { patient_first_name: "Sam" } })
        expect(post).toHaveBeenCalledWith("/automation/workflows/dry-run", {
            definition: DEF,
            condition_choices: {},
            context: { patient_first_name: "Sam" },
        })
    })

    it("getChannelReadiness GETs the readiness endpoint scoped to the location", async () => {
        get.mockResolvedValue({
            data: { sms: true, email: false, voice_configurable: true, details: [] },
        })
        const res = await getChannelReadiness("loc-1")
        expect(get).toHaveBeenCalledWith(
            "/automation/workflows/channel-readiness?location_id=loc-1",
        )
        expect(res.email).toBe(false)
        expect(res.voice_configurable).toBe(true)
    })

    it("getLaunchChecklist GETs the saved checklist with optional location context", async () => {
        get.mockResolvedValue({ data: { workflow_id: "w1", items: [] } })
        const res = await getLaunchChecklist("w1", { locationId: "loc-1" })
        expect(get).toHaveBeenCalledWith(
            "/automation/workflows/w1/launch-checklist?location_id=loc-1",
        )
        expect(res.workflow_id).toBe("w1")
    })

    it("previewLaunchChecklist POSTs an unsaved definition", async () => {
        post.mockResolvedValue({ data: { workflow_id: "w1", items: [] } })
        await previewLaunchChecklist("w1", DEF, { locationId: "loc-1" })
        expect(post).toHaveBeenCalledWith(
            "/automation/workflows/w1/launch-checklist/preview",
            { definition: DEF, location_id: "loc-1" },
        )
    })

    it("listMergeFields GETs the merge-field catalog", async () => {
        get.mockResolvedValue({
            data: [
                {
                    name: "clinic_name",
                    token: "{{clinic_name}}",
                    label: "Clinic name",
                    description: "",
                    sample: "X",
                    group: "clinic",
                    availability: "derived",
                    requires: [],
                    phi_level: "none",
                    channels: ["sms", "email", "voice"],
                    trigger_types: ["appointment_offset", "recall_scan", "manual", "bulk_import", "callback_requested"],
                },
            ],
        })
        const fields = await listMergeFields()
        expect(get).toHaveBeenCalledWith("/automation/workflows/merge-fields")
        expect(fields[0].token).toBe("{{clinic_name}}")
    })

    it("listMergeFields forwards trigger and channel filters", async () => {
        get.mockResolvedValue({ data: [] })
        await listMergeFields({ triggerType: "appointment_offset", channel: "sms" })
        expect(get).toHaveBeenCalledWith(
            "/automation/workflows/merge-fields?trigger_type=appointment_offset&channel=sms",
        )
    })
})
