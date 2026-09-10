import { beforeEach, describe, expect, it, vi } from "vitest"

import api from "@/lib/api"
import { listCallbacks } from "@/lib/callbacks-api"
import { listCalls } from "@/lib/calls-api"
import { listContacts } from "@/lib/contacts-api"
import { listDoNotContact } from "@/lib/do-not-contact-api"
import { listUndeliverables } from "@/lib/undeliverables-api"

vi.mock("@/lib/api", () => ({
    default: {
        get: vi.fn(),
    },
}))

const get = api.get as ReturnType<typeof vi.fn>

beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({ data: { items: [], records: [] } })
})

describe("active-location API boundary", () => {
    it("scopes calls", async () => {
        await listCalls({ limit: 25, locationId: "loc-1" })
        expect(get).toHaveBeenCalledWith("/institution/calls?limit=25&location_id=loc-1")
    })

    it("scopes callbacks", async () => {
        await listCallbacks({ limit: 25, locationId: "loc-1" })
        expect(get).toHaveBeenCalledWith("/institution/callbacks?limit=25&location_id=loc-1")
    })

    it("scopes contacts", async () => {
        await listContacts({ limit: 25, locationId: "loc-1" })
        expect(get).toHaveBeenCalledWith("/institution/contacts?limit=25&location_id=loc-1")
    })

    it("scopes DNC records", async () => {
        await listDoNotContact("loc-1")
        expect(get).toHaveBeenCalledWith("/institution/do-not-contact", {
            params: { location_id: "loc-1" },
        })
    })

    it("scopes institution automation issues", async () => {
        await listUndeliverables("institution", { locationId: "loc-1" })
        expect(get).toHaveBeenCalledWith(
            "/institution/undeliverables?page=1&size=50&location_id=loc-1",
        )
    })
})
