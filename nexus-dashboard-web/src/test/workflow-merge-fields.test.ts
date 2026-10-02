import { describe, it, expect, beforeEach, vi } from "vitest"
import { listMergeFields } from "@/lib/workflow-api"
import {
    catalogLoaded,
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
    it("has no catalog until the backend supplies one", () => {
        expect(catalogLoaded()).toBe(false)
        expect(getMergeFields()).toEqual([])
    })

    it("asserts nothing about tokens while the catalog is unloaded", () => {
        // With nothing to compare against, every token would read as unknown —
        // which would flag every message in the workflow.
        expect(unknownTokens("Hi {{clinic_name}} {{nonsense}}")).toEqual([])
        expect(
            unavailableTokens("Hi {{appointment_date}}", {
                triggerType: "schedule",
                channel: "sms",
            }),
        ).toEqual([])
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
                trigger_types: [
                    "event",
                    "manual",
                    "form_submitted",
                    "internal_status",
                    "schedule",
                    "inbound_message",
                ],
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

    it("stays empty and allows retry when the fetch fails", async () => {
        mockList.mockRejectedValueOnce(new Error("boom"))
        await expect(loadMergeFields()).rejects.toThrow("boom")
        // No stand-in catalog: a failed fetch must not look like a loaded one.
        expect(getMergeFields()).toEqual([])
        expect(catalogLoaded()).toBe(false)
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
            triggerType: "event",
            channel: "sms",
        })
        expect(mockList).toHaveBeenCalledWith({
            triggerType: "event",
            channel: "sms",
        })
        expect(fields.map((f) => f.token)).toEqual(["{{provider_name}}"])
        expect(getMergeFields({ triggerType: "event", channel: "sms" })).toBe(fields)
    })

    it("identifies tokens unavailable for a trigger or channel", async () => {
        // Scoping is asserted against a loaded catalog; before one exists there
        // is nothing to scope, which the unloaded-catalog test above covers.
        mockList.mockResolvedValue([
            scopedItem("clinic_name", ALL_TRIGGERS, ALL_CHANNELS),
            scopedItem("appointment_date", APPOINTMENT_TRIGGERS, ALL_CHANNELS),
            scopedItem("appointment_type", APPOINTMENT_TRIGGERS, ALL_CHANNELS),
            // Long enough that SMS is the wrong place for it.
            scopedItem("location_address", ALL_TRIGGERS, ["email", "voice"]),
        ])
        await loadMergeFields()

        expect(
            unavailableTokens("Hi {{appointment_date}} {{clinic_name}}", {
                triggerType: "manual",
                channel: "sms",
            }),
        ).toEqual(["{{appointment_date}}"])
        expect(
            unavailableTokens("Hi {{appointment_date}}", {
                triggerType: "internal_status",
                channel: "voice",
            }),
        ).toEqual([])
        expect(
            unavailableTokens("Hi {{appointment_type}}", {
                triggerType: "event",
                channel: "sms",
            }),
        ).toEqual([])
        // Restricted by channel rather than by trigger.
        expect(
            unavailableTokens("At {{location_address}}", {
                triggerType: "event",
                channel: "sms",
            }),
        ).toEqual(["{{location_address}}"])
    })
})

const ALL_TRIGGERS = [
    "event",
    "manual",
    "form_submitted",
    "internal_status",
    "schedule",
    "inbound_message",
]
const APPOINTMENT_TRIGGERS = ["event", "internal_status"]
const ALL_CHANNELS = ["sms", "email", "voice"]

function scopedItem(name: string, triggerTypes: string[], channels: string[]) {
    return {
        ...catalogItem(name, `{{${name}}}`, name, "sample"),
        trigger_types: triggerTypes,
        channels,
    }
}

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
            "event",
            "manual",
            "form_submitted",
            "internal_status",
            "schedule",
            "inbound_message",
        ],
    }
}
