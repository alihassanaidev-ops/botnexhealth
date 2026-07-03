import { describe, it, expect, beforeEach, vi } from "vitest"
import api from "@/lib/api"
import {
    createWorkflow,
    createWorkflowFromTemplate,
    getWorkflow,
    listTemplates,
    listWorkflows,
    publishWorkflow,
    updateWorkflow,
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

    it("createWorkflowFromTemplate fetches the template then creates via the working create endpoint", async () => {
        get.mockResolvedValue({
            data: { id: "tpl-1", name: "Reminder", description: "", trigger_type: "manual", definition: DEF, tags: [] },
        })
        post.mockResolvedValue({ data: { id: "w3", name: "Reminder" } })
        const wf = await createWorkflowFromTemplate("tpl-1")
        expect(get).toHaveBeenCalledWith("/automation/templates/tpl-1")
        expect(post).toHaveBeenCalledWith("/automation/workflows", { name: "Reminder", definition: DEF })
        expect(wf).toMatchObject({ id: "w3" })
    })

    it("createWorkflowFromTemplate honors a custom name", async () => {
        get.mockResolvedValue({
            data: { id: "tpl-1", name: "Reminder", description: "", trigger_type: "manual", definition: DEF, tags: [] },
        })
        post.mockResolvedValue({ data: { id: "w4" } })
        await createWorkflowFromTemplate("tpl-1", "  My Campaign  ")
        expect(post).toHaveBeenCalledWith("/automation/workflows", { name: "My Campaign", definition: DEF })
    })
})
