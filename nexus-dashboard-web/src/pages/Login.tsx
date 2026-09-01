import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { QRCodeSVG } from "qrcode.react"
import { toast } from "sonner"
import {
    startRegistration,
    startAuthentication,
    browserSupportsWebAuthn,
} from "@simplewebauthn/browser"
import axios from "axios"

import passkeyShield from "@/assets/icons/passkey-shield-v2.png"
import { useAuth } from "@/context/AuthContext"
import {
    AuthButton,
    AuthChoice,
    AuthCodePanel,
    AuthDivider,
    AuthField,
    AuthHeader,
    AuthScaffold,
} from "@/components/foundation/AuthScaffold"
import {
    startTotpSetup,
    verifyTotpSetup,
    verifyTotp,
    verifyRecoveryCode,
    startWebauthnRegistration,
    verifyWebauthnRegistration,
    startWebauthnAuthentication,
    verifyWebauthnAuthentication,
    type TotpSetupOptions,
    type AuthSession,
} from "@/lib/mfa-api"

const credentialsSchema = z.object({
    // Trim before validating so a copy-pasted email with stray surrounding
    // spaces is cleaned (and passes) instead of failing "Invalid email address".
    email: z.string().trim().email({ message: "Invalid email address" }),
    password: z.string().min(6, { message: "Password must be at least 6 characters" }),
})

const codeSchema = z.object({
    code: z.string().min(6, { message: "Enter the 6-digit code" }),
})

const recoverySchema = z.object({
    code: z.string().min(8, { message: "Enter a recovery code" }),
})

const labelSchema = z.object({
    device_label: z.string().max(64).optional(),
})

type SetupKind = "choose" | "totp" | "passkey"

type Step =
    | { kind: "credentials" }
    | {
          kind: "mfa_setup"
          ticket: string
          email: string
          methods: string[]
          choice: SetupKind
          totp?: TotpSetupOptions
      }
    | {
          kind: "mfa_verify"
          ticket: string
          email: string
          methods: string[]
          mode: "totp" | "passkey" | "recovery"
      }
    | { kind: "recovery_codes"; codes: string[]; session: AuthSession }

function getDetail(error: unknown, fallback: string): string {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (typeof detail === "string" && detail.trim()) return detail
    }
    if (error instanceof Error && error.message) return error.message
    return fallback
}

/**
 * @simplewebauthn/browser surfaces user-cancellation as NotAllowedError /
 * AbortError. We don't want to toast a scary "passkey failed" message in
 * that case — the user just bailed out of the platform prompt.
 */
function isWebAuthnUserCancel(err: unknown): boolean {
    const e = err as { name?: string }
    return e?.name === "NotAllowedError" || e?.name === "AbortError"
}

export default function Login() {
    const { signIn, completeAuthSession, requestPasswordReset } = useAuth()
    const [step, setStep] = useState<Step>({ kind: "credentials" })
    const [busy, setBusy] = useState(false)
    const [resetLoading, setResetLoading] = useState(false)
    const supportsPasskey = typeof window !== "undefined" && browserSupportsWebAuthn()

    const credForm = useForm<z.infer<typeof credentialsSchema>>({
        resolver: zodResolver(credentialsSchema),
        defaultValues: { email: "", password: "" },
    })
    const codeForm = useForm<z.infer<typeof codeSchema>>({
        resolver: zodResolver(codeSchema),
        defaultValues: { code: "" },
    })
    const recoveryForm = useForm<z.infer<typeof recoverySchema>>({
        resolver: zodResolver(recoverySchema),
        defaultValues: { code: "" },
    })
    const labelForm = useForm<z.infer<typeof labelSchema>>({
        resolver: zodResolver(labelSchema),
        defaultValues: { device_label: "" },
    })

    async function submitCredentials(values: z.infer<typeof credentialsSchema>) {
        setBusy(true)
        try {
            const result = await signIn(values.email, values.password)
            if (result.kind === "authenticated") return
            const ch = result.challenge

            codeForm.reset({ code: "" })
            recoveryForm.reset({ code: "" })
            labelForm.reset({ device_label: "" })

            if (ch.status === "mfa_setup_required") {
                // Decide what the setup screen should offer based on the
                // backend's role-aware setup_methods (mfa.py:setup_methods_
                // for_role: SUPER_ADMIN -> ['webauthn'] only; everyone
                // else -> ['webauthn','totp']).
                const allowsTotp = ch.setup_methods.includes("totp")
                const allowsPasskey = ch.setup_methods.includes("webauthn")
                if (!allowsTotp && !allowsPasskey) {
                    toast.error(
                        "This account has no available MFA enrollment methods. Contact an admin.",
                    )
                    return
                }
                setStep({
                    kind: "mfa_setup",
                    ticket: ch.mfa_ticket,
                    email: ch.email,
                    methods: ch.setup_methods,
                    choice: allowsTotp && allowsPasskey ? "choose" : (allowsPasskey ? "passkey" : "totp"),
                })
                return
            }

            // mfa_required path — pick a default mode the user actually has.
            const allowsTotpVerify = ch.methods.includes("totp")
            const allowsPasskeyVerify = ch.methods.includes("webauthn")
            const allowsRecovery = ch.methods.includes("recovery_code")
            if (!allowsTotpVerify && !allowsPasskeyVerify && !allowsRecovery) {
                toast.error("No verification methods available for this account.")
                return
            }
            const mode: "totp" | "passkey" | "recovery" = allowsPasskeyVerify
                ? "passkey"
                : allowsTotpVerify
                  ? "totp"
                  : "recovery"
            setStep({
                kind: "mfa_verify",
                ticket: ch.mfa_ticket,
                email: ch.email,
                methods: ch.methods,
                mode,
            })
        } catch (err) {
            const detail = getDetail(err, "")
            if (detail) toast.error(detail)
        } finally {
            setBusy(false)
        }
    }

    async function pickTotpSetup() {
        if (step.kind !== "mfa_setup") return
        setBusy(true)
        try {
            const totp = await startTotpSetup(step.ticket)
            setStep({ ...step, choice: "totp", totp })
        } catch (err) {
            toast.error(getDetail(err, "Couldn't start TOTP setup"))
        } finally {
            setBusy(false)
        }
    }

    async function submitSetupTotp(values: z.infer<typeof codeSchema>) {
        if (step.kind !== "mfa_setup" || step.choice !== "totp") return
        setBusy(true)
        try {
            const session = await verifyTotpSetup(step.ticket, values.code.trim())
            const codes = session.recovery_codes ?? []
            if (codes.length > 0) {
                setStep({ kind: "recovery_codes", codes, session })
            } else {
                await completeAuthSession(session)
            }
        } catch (err) {
            toast.error(getDetail(err, "MFA setup verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function registerPasskey() {
        if (step.kind !== "mfa_setup") return
        if (!supportsPasskey) {
            toast.error(
                "This browser doesn't support passkeys. Use Safari, Chrome, Edge, or Firefox on a recent OS.",
            )
            return
        }
        setBusy(true)
        try {
            const { options } = await startWebauthnRegistration(step.ticket)
            // startRegistration triggers the platform prompt (Touch ID,
            // Face ID, Windows Hello, hardware key) and returns a
            // RegistrationResponseJSON ready for the verify endpoint.
            const credential = await startRegistration({ optionsJSON: options })
            const label = labelForm.getValues("device_label")?.trim() || undefined
            const session = await verifyWebauthnRegistration(step.ticket, credential, label)
            const codes = session.recovery_codes ?? []
            if (codes.length > 0) {
                setStep({ kind: "recovery_codes", codes, session })
            } else {
                await completeAuthSession(session)
            }
        } catch (err) {
            if (isWebAuthnUserCancel(err)) {
                toast.message("Passkey prompt was cancelled.")
                return
            }
            toast.error(getDetail(err, "Passkey registration failed"))
        } finally {
            setBusy(false)
        }
    }

    async function submitVerifyTotp(values: z.infer<typeof codeSchema>) {
        if (step.kind !== "mfa_verify") return
        setBusy(true)
        try {
            const session = await verifyTotp(step.ticket, values.code.trim())
            await completeAuthSession(session)
        } catch (err) {
            toast.error(getDetail(err, "MFA verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function submitVerifyRecovery(values: z.infer<typeof recoverySchema>) {
        if (step.kind !== "mfa_verify") return
        setBusy(true)
        try {
            const session = await verifyRecoveryCode(step.ticket, values.code.trim())
            await completeAuthSession(session)
        } catch (err) {
            toast.error(getDetail(err, "Recovery code verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function authenticatePasskey() {
        if (step.kind !== "mfa_verify") return
        if (!supportsPasskey) {
            toast.error(
                "This browser doesn't support passkeys. Use Safari, Chrome, Edge, or Firefox.",
            )
            return
        }
        setBusy(true)
        try {
            const { options } = await startWebauthnAuthentication(step.ticket)
            const credential = await startAuthentication({ optionsJSON: options })
            const session = await verifyWebauthnAuthentication(step.ticket, credential)
            await completeAuthSession(session)
        } catch (err) {
            if (isWebAuthnUserCancel(err)) {
                toast.message("Passkey prompt was cancelled.")
                return
            }
            toast.error(getDetail(err, "Passkey verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function continueAfterRecoveryCodes() {
        if (step.kind !== "recovery_codes") return
        setBusy(true)
        try {
            await completeAuthSession(step.session)
        } finally {
            setBusy(false)
        }
    }

    async function onForgotPassword() {
        const email = credForm.getValues("email").trim()
        const valid = await credForm.trigger("email")
        if (!valid || !email) return
        setResetLoading(true)
        try {
            await requestPasswordReset(email)
            credForm.setValue("password", "")
            toast.success("If an account exists, a password reset email has been sent.")
        } catch (err: unknown) {
            const e = err as { message?: string }
            credForm.setError("email", { message: e?.message || "Failed to send reset email" })
        } finally {
            setResetLoading(false)
        }
    }

    function backToCredentials() {
        setStep({ kind: "credentials" })
        credForm.setValue("password", "")
    }

    const securityIcon = <img src={passkeyShield} alt="" aria-hidden="true" />

    return (
        <AuthScaffold>
            {step.kind === "credentials" && (
                <>
                    <AuthHeader
                        title="Login"
                        description="Enter your email below to login to your account."
                    />
                    <form onSubmit={credForm.handleSubmit(submitCredentials)} className="auth-form">
                        <AuthField
                            label="Email"
                            type="email"
                            placeholder="m@example.com"
                            autoComplete="email"
                            error={credForm.formState.errors.email?.message}
                            {...credForm.register("email")}
                        />
                        <AuthField
                            label="Password"
                            type="password"
                            autoComplete="current-password"
                            error={credForm.formState.errors.password?.message}
                            {...credForm.register("password")}
                        />
                        <AuthButton type="submit" disabled={busy}>
                            {busy ? "Signing in..." : "Sign in"}
                        </AuthButton>
                        <AuthButton
                            type="button"
                            variant="quiet"
                            disabled={resetLoading}
                            onClick={onForgotPassword}
                        >
                            {resetLoading ? "Sending reset link..." : "Forgot password?"}
                        </AuthButton>
                    </form>
                </>
            )}

            {step.kind === "mfa_setup" && step.choice === "choose" && (
                <>
                    <AuthHeader
                        title="Set up two-factor"
                        description="Choose how you want to verify future sign-ins. You can change this later from your account settings."
                        icon={securityIcon}
                    />
                    <div className="auth-stack">
                        <AuthChoice
                            type="button"
                            title="Use a passkey (Touch ID, Face ID, security key)"
                            description="Your device handles authentication; nothing is shared with the server beyond the public key."
                            badge="Recommended"
                            icon={securityIcon}
                            disabled={busy || !supportsPasskey}
                            onClick={registerPasskey}
                        />
                        <AuthDivider />
                        <AuthChoice
                            type="button"
                            title="Use an authenticator app (TOTP)"
                            description="Scan a QR code with Google Authenticator, 1Password, Authy, etc."
                            disabled={busy}
                            onClick={pickTotpSetup}
                        />
                        <AuthButton type="button" variant="quiet" onClick={backToCredentials} disabled={busy}>
                            Back
                        </AuthButton>
                    </div>
                </>
            )}

            {step.kind === "mfa_setup" && step.choice === "passkey" && (
                <>
                    <AuthHeader
                        title="Register a passkey"
                        description={supportsPasskey
                            ? "Click Continue to create a passkey for this account. Your browser will prompt for biometrics or your security key."
                            : "This browser doesn't support passkeys. Try Safari, Chrome, Edge, or Firefox on a recent OS."}
                        icon={securityIcon}
                    />
                    <div className="auth-stack">
                        <AuthField
                            label="Device name (optional)"
                            placeholder="e.g. MacBook Pro"
                            autoComplete="off"
                            error={labelForm.formState.errors.device_label?.message}
                            {...labelForm.register("device_label")}
                        />
                        <AuthButton type="button" onClick={registerPasskey} disabled={busy || !supportsPasskey}>
                            {busy ? "Waiting for prompt..." : "Continue"}
                        </AuthButton>
                        {step.methods.includes("totp") && (
                            <AuthButton type="button" variant="secondary" onClick={pickTotpSetup} disabled={busy}>
                                Use an authenticator app instead
                            </AuthButton>
                        )}
                        <AuthButton type="button" variant="quiet" onClick={backToCredentials} disabled={busy}>
                            Back
                        </AuthButton>
                    </div>
                </>
            )}

            {step.kind === "mfa_setup" && step.choice === "totp" && step.totp && (
                <>
                    <AuthHeader
                        title="Set up authenticator"
                        description="Scan the QR with an authenticator app (Google Authenticator, 1Password, Authy) and enter the 6-digit code it shows."
                    />
                    <div className="auth-stack">
                        <div className="auth-qr-wrap">
                            <QRCodeSVG value={step.totp.provisioning_uri} size={192} includeMargin={false} />
                        </div>
                        <AuthCodePanel>
                            <div className="auth-description">Can&apos;t scan? Enter this secret manually:</div>
                            <div className="break-all font-mono text-foreground">{step.totp.secret}</div>
                        </AuthCodePanel>
                        <form onSubmit={codeForm.handleSubmit(submitSetupTotp)} className="auth-form">
                            <AuthField
                                label="6-digit code"
                                inputMode="numeric"
                                autoComplete="one-time-code"
                                placeholder="123456"
                                maxLength={6}
                                error={codeForm.formState.errors.code?.message}
                                {...codeForm.register("code")}
                            />
                            <AuthButton type="submit" disabled={busy}>
                                {busy ? "Verifying..." : "Verify and continue"}
                            </AuthButton>
                            {step.methods.includes("webauthn") && (
                                <AuthButton
                                    type="button"
                                    variant="secondary"
                                    onClick={() => setStep({ ...step, choice: "passkey", totp: undefined })}
                                    disabled={busy}
                                >
                                    Use a passkey instead
                                </AuthButton>
                            )}
                            <AuthButton type="button" variant="quiet" onClick={backToCredentials} disabled={busy}>
                                Back
                            </AuthButton>
                        </form>
                    </div>
                </>
            )}

            {step.kind === "mfa_verify" && (
                <>
                    <AuthHeader
                        title="Two-factor verification"
                        description={step.mode === "passkey"
                            ? `Use your registered passkey for ${step.email}.`
                            : step.mode === "recovery"
                              ? `Enter one of your saved recovery codes for ${step.email}.`
                              : `Enter the 6-digit code from your authenticator app for ${step.email}.`}
                        icon={step.mode === "passkey" ? securityIcon : undefined}
                    />
                    <div className="auth-stack">
                        {step.mode === "passkey" && (
                            <>
                                <AuthButton type="button" onClick={authenticatePasskey} disabled={busy || !supportsPasskey}>
                                    {busy ? "Waiting for prompt..." : "Sign in with passkey"}
                                </AuthButton>
                                {step.methods.includes("totp") && (
                                    <AuthButton type="button" variant="secondary" onClick={() => setStep({ ...step, mode: "totp" })} disabled={busy}>
                                        Use authenticator code instead
                                    </AuthButton>
                                )}
                                {step.methods.includes("recovery_code") && (
                                    <AuthButton type="button" variant="quiet" onClick={() => setStep({ ...step, mode: "recovery" })} disabled={busy}>
                                        Use a recovery code instead
                                    </AuthButton>
                                )}
                            </>
                        )}
                        {step.mode === "totp" && (
                            <form onSubmit={codeForm.handleSubmit(submitVerifyTotp)} className="auth-form">
                                <AuthField
                                    label="6-digit code"
                                    inputMode="numeric"
                                    autoComplete="one-time-code"
                                    placeholder="123456"
                                    maxLength={6}
                                    error={codeForm.formState.errors.code?.message}
                                    {...codeForm.register("code")}
                                />
                                <AuthButton type="submit" disabled={busy}>
                                    {busy ? "Verifying..." : "Verify"}
                                </AuthButton>
                                {step.methods.includes("webauthn") && (
                                    <AuthButton type="button" variant="secondary" onClick={() => setStep({ ...step, mode: "passkey" })} disabled={busy}>
                                        Use passkey instead
                                    </AuthButton>
                                )}
                                {step.methods.includes("recovery_code") && (
                                    <AuthButton type="button" variant="quiet" onClick={() => setStep({ ...step, mode: "recovery" })} disabled={busy}>
                                        Use a recovery code instead
                                    </AuthButton>
                                )}
                            </form>
                        )}
                        {step.mode === "recovery" && (
                            <form onSubmit={recoveryForm.handleSubmit(submitVerifyRecovery)} className="auth-form">
                                <AuthField
                                    label="Recovery code"
                                    autoComplete="off"
                                    placeholder="xxxxxxxx"
                                    error={recoveryForm.formState.errors.code?.message}
                                    {...recoveryForm.register("code")}
                                />
                                <AuthButton type="submit" disabled={busy}>
                                    {busy ? "Verifying..." : "Verify recovery code"}
                                </AuthButton>
                                {step.methods.includes("totp") && (
                                    <AuthButton type="button" variant="secondary" onClick={() => setStep({ ...step, mode: "totp" })} disabled={busy}>
                                        Use authenticator code instead
                                    </AuthButton>
                                )}
                            </form>
                        )}
                        <AuthButton type="button" variant="quiet" onClick={backToCredentials} disabled={busy}>
                            Back
                        </AuthButton>
                    </div>
                </>
            )}

            {step.kind === "recovery_codes" && (
                <>
                    <AuthHeader
                        title="Save your recovery codes"
                        description="These codes let you sign in if you lose your authenticator. Each code works once. They will not be shown again — copy them somewhere safe before continuing."
                    />
                    <div className="auth-stack">
                        <AuthCodePanel mono>
                            {step.codes.map((code) => <div key={code}>{code}</div>)}
                        </AuthCodePanel>
                        <AuthButton
                            type="button"
                            variant="secondary"
                            onClick={() => {
                                void navigator.clipboard
                                    .writeText(step.codes.join("\n"))
                                    .then(() => toast.success("Recovery codes copied to clipboard"))
                                    .catch(() => toast.error("Couldn't copy. Select them manually."))
                            }}
                        >
                            Copy codes
                        </AuthButton>
                        <AuthButton type="button" onClick={continueAfterRecoveryCodes} disabled={busy}>
                            {busy ? "Continuing..." : "I've saved them — continue"}
                        </AuthButton>
                    </div>
                </>
            )}
        </AuthScaffold>
    )
}
