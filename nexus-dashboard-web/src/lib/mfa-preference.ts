export type MfaVerifyMode = "totp" | "passkey" | "recovery" | "email"

const MFA_PREFERENCE_KEY = "scalenexus.preferred-mfa-method"

/**
 * Keep only a display preference in local storage. Authentication state,
 * secrets, and recovery codes must never be persisted here.
 */
export function getInitialMfaMode(methods: string[]): MfaVerifyMode {
    try {
        const preferred = window.localStorage.getItem(MFA_PREFERENCE_KEY)
        if (preferred === "totp" && methods.includes("totp")) return "totp"
        if (preferred === "passkey" && methods.includes("webauthn")) return "passkey"
        if (preferred === "email" && methods.includes("email")) return "email"
    } catch {
        // Storage may be unavailable in private/restricted browser contexts.
    }

    // Authenticator apps are the most predictable option for clinic staff
    // who move between shared workstations. Passkeys remain available and
    // become the default after a user successfully chooses one.
    if (methods.includes("totp")) return "totp"
    if (methods.includes("webauthn")) return "passkey"
    // Email codes rank below both: they are the fallback for a user whose
    // phone or passkey is out of reach, not the everyday route. A user who
    // picks one anyway is remembered by the preference check above.
    if (methods.includes("email")) return "email"
    return "recovery"
}

export function rememberMfaMode(mode: Exclude<MfaVerifyMode, "recovery">): void {
    try {
        window.localStorage.setItem(MFA_PREFERENCE_KEY, mode)
    } catch {
        // Preference persistence is optional and must never block sign-in.
    }
}
