import { describe, it, expect, beforeEach, vi } from "vitest"
import { renderTemplate, smsSegments } from "@/lib/workflow/preview"
import { listMergeFields } from "@/lib/workflow-api"
import {
    extractTokens,
    loadMergeFields,
    unknownTokens,
    _resetMergeFieldsCache,
} from "@/lib/workflow/merge-fields"

// Sample data comes from the backend catalog and nowhere else — there is no
// static fallback to render against, so these tests load one explicitly.
vi.mock("@/lib/workflow-api", () => ({ listMergeFields: vi.fn() }))

const mockList = listMergeFields as ReturnType<typeof vi.fn>

function item(name: string, sample: string) {
    return {
        name,
        token: `{{${name}}}`,
        label: name,
        description: "",
        sample,
        group: "location",
        availability: "derived" as const,
        requires: [],
        phi_level: "none" as const,
        channels: ["sms", "email", "voice"],
        trigger_types: ["event", "manual"],
    }
}

beforeEach(async () => {
    mockList.mockReset()
    _resetMergeFieldsCache()
    mockList.mockResolvedValue([
        item("patient_first_name", "Jordan"),
        item("clinic_name", "Riverside Dental"),
    ])
    await loadMergeFields()
})

describe("message preview", () => {
    it("substitutes known merge fields with sample data", () => {
        expect(renderTemplate("Hi {{patient_first_name}} at {{clinic_name}}")).toBe(
            "Hi Jordan at Riverside Dental",
        )
    })
    it("tolerates inner whitespace in tokens", () => {
        expect(renderTemplate("Hi {{ patient_first_name }}")).toBe("Hi Jordan")
    })
    it("renders unknown tokens as readable placeholders (no raw braces)", () => {
        expect(renderTemplate("Hi {{mystery}}")).toBe("Hi [mystery]")
    })
    it("accepts custom data overrides", () => {
        expect(renderTemplate("Hi {{patient_first_name}}", { "{{patient_first_name}}": "Sam" })).toBe(
            "Hi Sam",
        )
    })
})

describe("sms segments", () => {
    it("counts single and concatenated segments", () => {
        expect(smsSegments("")).toBe(0)
        expect(smsSegments("a".repeat(160))).toBe(1)
        expect(smsSegments("a".repeat(161))).toBe(2)
    })
})

describe("merge token extraction", () => {
    it("extracts and normalizes tokens", () => {
        expect(extractTokens("a {{x}} b {{ y }}")).toEqual(["{{x}}", "{{y}}"])
    })
    it("identifies unknown tokens against the catalog", () => {
        expect(unknownTokens("Hi {{patient_first_name}} {{foo}}")).toEqual(["{{foo}}"])
        expect(unknownTokens("Hi {{clinic_name}}")).toEqual([])
    })
})
