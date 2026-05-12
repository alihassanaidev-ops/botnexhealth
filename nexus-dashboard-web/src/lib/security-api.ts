/**
 * Account-security endpoints — read MFA status, list passkeys, and
 * (with a step-up ticket) regenerate recovery codes, remove a passkey,
 * or disable the authenticator.
 *
 * Every destructive call sends ``mfa_ticket`` in the body. The backend
 * (auth.py _require_step_up) consumes the elevated ticket atomically;
 * a missing or already-spent ticket yields 401.
 */

import type {
    PublicKeyCredentialRequestOptionsJSON,
    PublicKeyCredentialCreationOptionsJSON,
    AuthenticationResponseJSON,
    RegistrationResponseJSON,
} from "@simplewebauthn/browser"

import api from "@/lib/api"
import type { AuthSession, MfaChallengeResponse } from "@/lib/mfa-api"

// ── Read-only ───────────────────────────────────────────────────────────

export interface MfaStatusResponse {
    webauthn_count: number
    totp_enabled: boolean
    recovery_codes_remaining: number
    methods: string[]
}

export async function getMfaStatus(): Promise<MfaStatusResponse> {
    const { data } = await api.get<MfaStatusResponse>("/auth/mfa/status")
    return data
}

export interface WebAuthnCredentialSummary {
    id: string
    device_label: string | null
    aaguid: string | null
    credential_device_type: string | null
    credential_backed_up: boolean
    transports: string[] | null
    created_at: string
    last_used_at: string | null
}

export async function listPasskeys(): Promise<WebAuthnCredentialSummary[]> {
    const { data } = await api.get<{ credentials: WebAuthnCredentialSummary[] }>(
        "/auth/mfa/webauthn",
    )
    return data.credentials
}

// ── Step-up flow ────────────────────────────────────────────────────────
//
// The shape mirrors the login MfaChallengeResponse so the same MfaFlow
// component renders both. The verify endpoints under /mfa/step-up/*
// return ``status: "step_up_complete"`` (with the now-elevated
// mfa_ticket) rather than a session — the calling component takes that
// elevated ticket and uses it on the destructive endpoint.

export type StepUpChallenge = Omit<MfaChallengeResponse, "status" | "setup_methods"> & {
    status: "step_up_required"
    setup_methods: never[]
}

export async function startStepUp(): Promise<StepUpChallenge> {
    const { data } = await api.post<StepUpChallenge>("/auth/mfa/step-up/challenge")
    return data
}

// We expose a typed wrapper around `AuthSession` for the elevated-ticket
// payload so callers don't accidentally feed it back into a session-
// applying helper. The runtime shape is the same envelope-by-axios
// response from /auth/mfa/step-up/*/verify endpoints.
export interface StepUpComplete {
    status: "step_up_complete"
    mfa_ticket: string
    expires_in_seconds: number
}

export async function stepUpVerifyTotp(mfaTicket: string, code: string): Promise<StepUpComplete> {
    const { data } = await api.post<StepUpComplete>(
        "/auth/mfa/step-up/totp/verify",
        { mfa_ticket: mfaTicket, code },
    )
    return data
}

export async function stepUpVerifyRecoveryCode(
    mfaTicket: string,
    code: string,
): Promise<StepUpComplete> {
    const { data } = await api.post<StepUpComplete>(
        "/auth/mfa/step-up/recovery-code/verify",
        { mfa_ticket: mfaTicket, code },
    )
    return data
}

export interface WebAuthnAuthOptions {
    options: PublicKeyCredentialRequestOptionsJSON
}

export async function stepUpWebauthnOptions(mfaTicket: string): Promise<WebAuthnAuthOptions> {
    const { data } = await api.post<WebAuthnAuthOptions>(
        "/auth/mfa/step-up/webauthn/authenticate/options",
        { mfa_ticket: mfaTicket },
    )
    return data
}

export async function stepUpWebauthnVerify(
    mfaTicket: string,
    credential: AuthenticationResponseJSON,
): Promise<StepUpComplete> {
    const { data } = await api.post<StepUpComplete>(
        "/auth/mfa/step-up/webauthn/authenticate/verify",
        { mfa_ticket: mfaTicket, credential },
    )
    return data
}

// ── Destructive factor-management ───────────────────────────────────────

export interface RecoveryCodesResponse {
    recovery_codes: string[]
}

export async function regenerateRecoveryCodes(elevatedTicket: string): Promise<string[]> {
    const { data } = await api.post<RecoveryCodesResponse>(
        "/auth/mfa/recovery-codes/regenerate",
        { mfa_ticket: elevatedTicket },
    )
    return data.recovery_codes
}

export async function removePasskey(credentialId: string, elevatedTicket: string): Promise<void> {
    await api.delete(`/auth/mfa/webauthn/${credentialId}`, {
        data: { mfa_ticket: elevatedTicket },
    })
}

export async function disableTotp(elevatedTicket: string): Promise<string> {
    const { data } = await api.post<{ message: string }>(
        "/auth/mfa/totp/disable",
        { mfa_ticket: elevatedTicket },
    )
    return data.message
}

// ── Add-factor (enrol an additional passkey or TOTP) ────────────────────
//
// Two-step pattern per factor. The /options endpoint consumes the
// elevated step-up ticket the caller produced via the step-up modal
// and hands back a short-lived enrollment ticket carrying the WebAuthn
// challenge / TOTP secret. The /verify endpoint consumes the
// enrollment ticket atomically and persists the new factor.

export interface AddPasskeyOptions {
    enrollment_ticket: string
    options: PublicKeyCredentialCreationOptionsJSON
    expires_in_seconds: number
}

export async function addPasskeyOptions(elevatedTicket: string): Promise<AddPasskeyOptions> {
    const { data } = await api.post<AddPasskeyOptions>(
        "/auth/mfa/factors/webauthn/register/options",
        { mfa_ticket: elevatedTicket },
    )
    return data
}

export interface AddPasskeyResult {
    status: "registered"
    credential: WebAuthnCredentialSummary
}

export async function addPasskeyVerify(
    enrollmentTicket: string,
    credential: RegistrationResponseJSON,
    deviceLabel?: string,
): Promise<AddPasskeyResult> {
    const { data } = await api.post<AddPasskeyResult>(
        "/auth/mfa/factors/webauthn/register/verify",
        {
            enrollment_ticket: enrollmentTicket,
            credential,
            device_label: deviceLabel ?? null,
        },
    )
    return data
}

export interface AddTotpOptions {
    enrollment_ticket: string
    secret: string
    provisioning_uri: string
    expires_in_seconds: number
}

export async function addTotpOptions(elevatedTicket: string): Promise<AddTotpOptions> {
    const { data } = await api.post<AddTotpOptions>(
        "/auth/mfa/factors/totp/setup/options",
        { mfa_ticket: elevatedTicket },
    )
    return data
}

export async function addTotpVerify(
    enrollmentTicket: string,
    code: string,
): Promise<{ status: "enrolled"; totp_enabled: true }> {
    const { data } = await api.post<{ status: "enrolled"; totp_enabled: true }>(
        "/auth/mfa/factors/totp/setup/verify",
        { enrollment_ticket: enrollmentTicket, code },
    )
    return data
}

// Unused export retained so consumers can mass-import without missing
// pieces; AuthSession may show up in shared-component prop types.
export type { AuthSession }
