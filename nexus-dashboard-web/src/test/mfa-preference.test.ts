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

    it("ranks email codes below the factors they stand in for", () => {
        // Email is the fallback for a user whose phone or passkey is out of
        // reach — offering it by default would quietly downgrade everyone's
        // everyday sign-in to mailbox strength.
        expect(getInitialMfaMode(["totp", "email", "recovery_code"])).toBe("totp")
        expect(getInitialMfaMode(["webauthn", "email", "recovery_code"])).toBe("passkey")
    })

    it("uses an email code ahead of a recovery code when nothing else is offered", () => {
        expect(getInitialMfaMode(["email", "recovery_code"])).toBe("email")
        expect(getInitialMfaMode(["email"])).toBe("email")
    })

    it("honours a remembered email preference, and forgets it when withdrawn", () => {
        rememberMfaMode("email")
        expect(getInitialMfaMode(["totp", "email", "recovery_code"])).toBe("email")
        // Server stopped offering the method (kill switch, factor removed).
        expect(getInitialMfaMode(["totp", "recovery_code"])).toBe("totp")
    })
})
