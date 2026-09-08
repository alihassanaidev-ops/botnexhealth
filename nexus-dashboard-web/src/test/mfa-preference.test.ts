import { beforeEach, describe, expect, it } from "vitest"
import { getInitialMfaMode, rememberMfaMode } from "@/lib/mfa-preference"

describe("MFA display preference", () => {
    beforeEach(() => window.localStorage.clear())

    it("defaults mixed clinic accounts to the multi-computer-friendly authenticator", () => {
        expect(getInitialMfaMode(["webauthn", "totp", "recovery_code"])).toBe("totp")
    })

    it("honours a previously successful passkey preference when available", () => {
        rememberMfaMode("passkey")
        expect(getInitialMfaMode(["webauthn", "totp", "recovery_code"])).toBe("passkey")
    })

    it("falls back safely when the preferred method is unavailable", () => {
        rememberMfaMode("passkey")
        expect(getInitialMfaMode(["totp", "recovery_code"])).toBe("totp")
        expect(getInitialMfaMode(["recovery_code"])).toBe("recovery")
    })
})
