import { describe, it, expect, beforeEach, vi } from "vitest"
import { listMergeFields } from "@/lib/workflow-api"
import {
    FALLBACK_MERGE_FIELDS,
    getMergeFields,
    loadMergeFields,
    sampleMergeData,
    unknownTokens,
    _resetMergeFieldsCache,
} from "@/lib/workflow/merge-fields"

vi.mock("@/lib/workflow-api", () => ({ listMergeFields: vi.fn() }))

const mockList = listMergeFields as ReturnType<typeof vi.fn>

beforeEach(() => {
    mockList.mockReset()
    _resetMergeFieldsCache()
})

describe("merge-field catalog", () => {
    it("starts from the static fallback (backend tokens only, no provider/appointment)", () => {
        const tokens = getMergeFields().map((f) => f.token)
        expect(tokens).toEqual(FALLBACK_MERGE_FIELDS.map((f) => f.token))
        expect(tokens).not.toContain("{{provider_name}}")
        expect(tokens).not.toContain("{{appointment_date}}")
    })

    it("fetches and caches the backend catalog once", async () => {
        mockList.mockResolvedValue([
            { name: "clinic_name", token: "{{clinic_name}}", label: "Clinic", description: "", sample: "Acme", group: "clinic" },
        ])
        const first = await loadMergeFields()
        const second = await loadMergeFields()
        expect(mockList).toHaveBeenCalledTimes(1)
        expect(first).toBe(second)
        expect(getMergeFields()).toEqual([{ token: "{{clinic_name}}", label: "Clinic", sample: "Acme" }])
        expect(sampleMergeData()["{{clinic_name}}"]).toBe("Acme")
    })

    it("treats tokens outside the fetched catalog as unknown", async () => {
        mockList.mockResolvedValue([
            { name: "clinic_name", token: "{{clinic_name}}", label: "Clinic", description: "", sample: "Acme", group: "clinic" },
        ])
        await loadMergeFields()
        expect(unknownTokens("Hi {{clinic_name}} {{provider_name}}")).toEqual(["{{provider_name}}"])
    })

    it("keeps the fallback and allows retry when the fetch fails", async () => {
        mockList.mockRejectedValueOnce(new Error("boom"))
        await expect(loadMergeFields()).rejects.toThrow("boom")
        expect(getMergeFields()).toEqual(FALLBACK_MERGE_FIELDS)
        mockList.mockResolvedValueOnce([
            { name: "clinic_name", token: "{{clinic_name}}", label: "Clinic", description: "", sample: "Acme", group: "clinic" },
        ])
        await loadMergeFields()
        expect(mockList).toHaveBeenCalledTimes(2)
    })
})
