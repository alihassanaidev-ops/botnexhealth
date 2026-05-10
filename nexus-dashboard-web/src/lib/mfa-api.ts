/**
 * MFA endpoints (TOTP only — WebAuthn passkeys can be added later).
 *
 * The login flow is two-phase:
 *   1. POST /auth/login with {email, password}. Backend returns either
 *      AuthSession (legacy / no-MFA path) or MfaChallengeResponse with
 *      a short-lived ticket bound to the client's IP+UA.
 *   2. The ticket is presented to the appropriate /auth/mfa/totp/*
 *      endpoint to either enroll a new authenticator or verify an
 *      existing one. On success, an AuthSession with access_token is
 *      returned and the refresh cookie is set HttpOnly.
 *
 * Recovery codes are issued exactly once during enrollment — they're
 * the only way back into the account if the authenticator is lost,
 * so the UI must show them with a copy/save affordance.
 */

import axios from "axios"
import type {
    PublicKeyCredentialCreationOptionsJSON,
    PublicKeyCredentialRequestOptionsJSON,
    RegistrationResponseJSON,
    AuthenticationResponseJSON,
} from "@simplewebauthn/browser"
import api from "@/lib/api"

const baseURL = api.defaults.baseURL

export interface MfaChallengeResponse {
    status: "mfa_required" | "mfa_setup_required"
    mfa_ticket: string
    methods: string[]
    setup_methods: string[]
    expires_in_seconds: number
    role: string
    email: string
}

export interface AuthSession {
    status: "authenticated"
    access_token: string
    token_type: string
    recovery_codes: string[] | null
}

export interface TotpSetupOptions {
    secret: string
    provisioning_uri: string
}

export type LoginResult = AuthSession | MfaChallengeResponse

export async function loginWithPassword(email: string, password: string): Promise<LoginResult> {
    const { data } = await axios.post<LoginResult>(
        `${baseURL}/auth/login`,
        { email, password },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function startTotpSetup(mfaTicket: string): Promise<TotpSetupOptions> {
    const { data } = await axios.post<TotpSetupOptions>(
        `${baseURL}/auth/mfa/totp/setup/options`,
        { mfa_ticket: mfaTicket },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function verifyTotpSetup(mfaTicket: string, code: string): Promise<AuthSession> {
    const { data } = await axios.post<AuthSession>(
        `${baseURL}/auth/mfa/totp/setup/verify`,
        { mfa_ticket: mfaTicket, code },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function verifyTotp(mfaTicket: string, code: string): Promise<AuthSession> {
    const { data } = await axios.post<AuthSession>(
        `${baseURL}/auth/mfa/totp/verify`,
        { mfa_ticket: mfaTicket, code },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function verifyRecoveryCode(mfaTicket: string, code: string): Promise<AuthSession> {
    const { data } = await axios.post<AuthSession>(
        `${baseURL}/auth/mfa/recovery-code/verify`,
        { mfa_ticket: mfaTicket, code },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

// ── WebAuthn (passkeys) ──────────────────────────────────────────────────
//
// Backend builds py_webauthn options and serialises them via
// ``options_to_json``, which produces JSON already shaped to the
// @simplewebauthn/browser ``*JSON`` types. We type the responses with
// those upstream types directly so the call sites can pass the options
// straight into ``startRegistration`` / ``startAuthentication`` without
// any cast.

export interface WebAuthnRegistrationOptionsResponse {
    options: PublicKeyCredentialCreationOptionsJSON
}

export interface WebAuthnAuthenticationOptionsResponse {
    options: PublicKeyCredentialRequestOptionsJSON
}

export async function startWebauthnRegistration(
    mfaTicket: string,
): Promise<WebAuthnRegistrationOptionsResponse> {
    const { data } = await axios.post<WebAuthnRegistrationOptionsResponse>(
        `${baseURL}/auth/mfa/webauthn/register/options`,
        { mfa_ticket: mfaTicket },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function verifyWebauthnRegistration(
    mfaTicket: string,
    credential: RegistrationResponseJSON,
    deviceLabel?: string,
): Promise<AuthSession> {
    const { data } = await axios.post<AuthSession>(
        `${baseURL}/auth/mfa/webauthn/register/verify`,
        { mfa_ticket: mfaTicket, credential, device_label: deviceLabel ?? null },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function startWebauthnAuthentication(
    mfaTicket: string,
): Promise<WebAuthnAuthenticationOptionsResponse> {
    const { data } = await axios.post<WebAuthnAuthenticationOptionsResponse>(
        `${baseURL}/auth/mfa/webauthn/authenticate/options`,
        { mfa_ticket: mfaTicket },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}

export async function verifyWebauthnAuthentication(
    mfaTicket: string,
    credential: AuthenticationResponseJSON,
): Promise<AuthSession> {
    const { data } = await axios.post<AuthSession>(
        `${baseURL}/auth/mfa/webauthn/authenticate/verify`,
        { mfa_ticket: mfaTicket, credential },
        { headers: { "Content-Type": "application/json" }, withCredentials: true },
    )
    return data
}
