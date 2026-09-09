import { describe, expect, it } from "vitest"
import { AUTH_INACTIVITY_TIMEOUT_MS } from "@/lib/auth-session-policy"

describe("AuthContext inactivity policy", () => {
    it("matches the backend's eight-hour refresh-session window", () => {
        expect(AUTH_INACTIVITY_TIMEOUT_MS).toBe(8 * 60 * 60 * 1000)
    })
})
