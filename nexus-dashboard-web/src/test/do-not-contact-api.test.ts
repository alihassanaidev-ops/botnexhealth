import { describe, it, expect, beforeEach, vi } from "vitest"
import api from "@/lib/api"
import {
    createDoNotContact,
    listDoNotContact,
    releaseDoNotContact,
} from "@/lib/do-not-contact-api"

vi.mock("@/lib/api", () => ({
    default: {
        get: vi.fn(),
        post: vi.fn(),
        delete: vi.fn(),
    },
}))

const get = api.get as ReturnType<typeof vi.fn>
const post = api.post as ReturnType<typeof vi.fn>
const del = api.delete as ReturnType<typeof vi.fn>

beforeEach(() => {
    get.mockReset()
    post.mockReset()
    del.mockReset()
})

describe("do-not-contact-api", () => {
    it("unwraps the records list", async () => {
        get.mockResolvedValue({ data: { records: [{ phone_masked: "+1555***4567" }] } })
        const records = await listDoNotContact()
        expect(get).toHaveBeenCalledWith("/institution/do-not-contact")
        expect(records).toHaveLength(1)
        expect(records[0].phone_masked).toBe("+1555***4567")
    })

    it("creates an institution-scoped opt-out", async () => {
        post.mockResolvedValue({ data: { phone_masked: "+1555***4567", scope: "institution" } })
        await createDoNotContact({
            phone: "+15551234567",
            scope: "institution",
            location_id: null,
            reason: "asked in person",
        })
        expect(post).toHaveBeenCalledWith("/institution/do-not-contact", {
            phone: "+15551234567",
            scope: "institution",
            location_id: null,
            reason: "asked in person",
        })
    })

    it("releases by phone via a DELETE body", async () => {
        del.mockResolvedValue({ data: { released: true } })
        const released = await releaseDoNotContact("+15551234567")
        expect(del).toHaveBeenCalledWith("/institution/do-not-contact", {
            data: { phone: "+15551234567" },
        })
        expect(released).toBe(true)
    })
})
