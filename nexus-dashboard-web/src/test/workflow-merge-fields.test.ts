import { describe, it, expect, beforeEach, vi } from "vitest"
import { listMergeFields } from "@/lib/workflow-api"
import {
    FALLBACK_MERGE_FIELDS,
    getMergeFields,
    loadMergeFields,
    sampleMergeData,
    unavailableTokens,
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
    it("starts from the dental fallback catalog", () => {
        const tokens = getMergeFields().map((f) => f.token)
        expect(tokens).toEqual(FALLBACK_MERGE_FIELDS.map((f) => f.token))
        expect(tokens).toContain("{{provider_name}}")
        expect(tokens).toContain("{{appointment_date}}")
    })

    it("fetches and caches the backend catalog once", async () => {
        mockList.mockResolvedValue([
            catalogItem("clinic_name", "{{clinic_name}}", "Clinic", "Acme"),
        ])
        const first = await loadMergeFields()
        const second = await loadMergeFields()
        expect(mockList).toHaveBeenCalledTimes(1)
        expect(first).toBe(second)
        expect(getMergeFields()).toEqual([
            {
                name: "clinic_name",
                token: "{{clinic_name}}",
                label: "Clinic",
                sample: "Acme",
                description: "",
                group: "location",
                availability: "derived",
                requires: [],
                phi_level: "none",
                channels: ["sms", "email", "voice"],
                trigger_types: ["appointment_offset", "recall_scan", "manual", "bulk_import", "callback_requested"],
            },
        ])
        expect(sampleMergeData()["{{clinic_name}}"]).toBe("Acme")
    })

    it("treats tokens outside the fetched catalog as unknown", async () => {
        mockList.mockResolvedValue([
            catalogItem("clinic_name", "{{clinic_name}}", "Clinic", "Acme"),
        ])
        await loadMergeFields()
        expect(unknownTokens("Hi {{clinic_name}} {{provider_name}}")).toEqual(["{{provider_name}}"])
    })

    it("keeps the fallback and allows retry when the fetch fails", async () => {
        mockList.mockRejectedValueOnce(new Error("boom"))
        await expect(loadMergeFields()).rejects.toThrow("boom")
        expect(getMergeFields()).toEqual(FALLBACK_MERGE_FIELDS)
        mockList.mockResolvedValueOnce([
            catalogItem("clinic_name", "{{clinic_name}}", "Clinic", "Acme"),
        ])
        await loadMergeFields()
        expect(mockList).toHaveBeenCalledTimes(2)
    })

    it("loads scoped catalogs for trigger/channel-specific insertion", async () => {
        mockList.mockResolvedValueOnce([
            catalogItem("provider_name", "{{provider_name}}", "Provider", "Dr. Smith"),
        ])
        const fields = await loadMergeFields({
            triggerType: "appointment_offset",
            channel: "sms",
        })
        expect(mockList).toHaveBeenCalledWith({
            triggerType: "appointment_offset",
            channel: "sms",
        })
        expect(fields.map((f) => f.token)).toEqual(["{{provider_name}}"])
        expect(getMergeFields({ triggerType: "appointment_offset", channel: "sms" })).toBe(fields)
    })

    it("identifies tokens unavailable for a trigger or channel", () => {
        expect(
            unavailableTokens("Hi {{appointment_date}} {{clinic_name}}", {
                triggerType: "manual",
                channel: "sms",
            }),
        ).toEqual(["{{appointment_date}}"])
        expect(
            unavailableTokens("Hi {{appointment_type}}", {
                triggerType: "appointment_offset",
                channel: "sms",
            }),
        ).toEqual(["{{appointment_type}}"])
    })
})

function catalogItem(name: string, token: string, label: string, sample: string) {
    return {
        name,
        token,
        label,
        description: "",
        sample,
        group: "location",
        availability: "derived" as const,
        requires: [],
        phi_level: "none" as const,
        channels: ["sms", "email", "voice"],
        trigger_types: [
            "appointment_offset",
            "recall_scan",
            "manual",
            "bulk_import",
            "callback_requested",
        ],
    }
}
