import { describe, it, expect } from "vitest"
import { renderTemplate, smsSegments } from "@/lib/workflow/preview"
import { extractTokens, unknownTokens } from "@/lib/workflow/merge-fields"

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
