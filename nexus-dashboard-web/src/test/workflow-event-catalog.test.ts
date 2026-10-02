import { beforeEach, describe, expect, it, vi } from "vitest"

import { listEventCatalog } from "@/lib/workflow-api"
import {
    _resetEventCatalogCache,
    loadEventCatalog,
} from "@/lib/workflow/event-catalog"

vi.mock("@/lib/workflow-api", () => ({ listEventCatalog: vi.fn() }))

const catalog = listEventCatalog as ReturnType<typeof vi.fn>

describe("PMS-scoped workflow event catalog", () => {
    beforeEach(() => {
        catalog.mockReset()
        _resetEventCatalogCache()
    })

    it("keeps NexHealth and GoTracker responses in separate cache entries", async () => {
        const nexhealth = [{ key: "appointment.booked", context: [] }]
        const gotracker = [{ key: "appointment.checked_in", context: [] }]
        catalog
            .mockResolvedValueOnce(nexhealth)
            .mockResolvedValueOnce(gotracker)

        expect(await loadEventCatalog("nexhealth")).toBe(nexhealth)
        expect(await loadEventCatalog("gotracker")).toBe(gotracker)
        expect(await loadEventCatalog("nexhealth")).toBe(nexhealth)
        expect(catalog).toHaveBeenNthCalledWith(1, "nexhealth")
        expect(catalog).toHaveBeenNthCalledWith(2, "gotracker")
        expect(catalog).toHaveBeenCalledTimes(2)
    })
})
