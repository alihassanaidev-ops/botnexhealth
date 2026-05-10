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

import { useAuth } from "@/context/AuthContext"
import { Button } from "@/components/ui/button"
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
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
    email: z.string().email({ message: "Invalid email address" }),
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

    return (
        <div className="relative flex h-screen w-full items-center justify-center bg-background p-4">
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" />
            </div>
            <Card className="w-full max-w-md border-border bg-gradient-to-b from-card to-accent/20 shadow-lg">
                {step.kind === "credentials" && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Login</CardTitle>
                            <CardDescription>
                                Enter your email below to login to your account.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Form {...credForm}>
                                <form onSubmit={credForm.handleSubmit(submitCredentials)} className="space-y-4">
                                    <FormField
                                        control={credForm.control}
                                        name="email"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>Email</FormLabel>
                                                <FormControl>
                                                    <Input placeholder="m@example.com" autoComplete="email" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={credForm.control}
                                        name="password"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>Password</FormLabel>
                                                <FormControl>
                                                    <Input type="password" autoComplete="current-password" {...field} />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <Button type="submit" className="w-full" disabled={busy}>
                                        {busy ? "Signing in..." : "Sign in"}
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        className="w-full"
                                        disabled={resetLoading}
                                        onClick={onForgotPassword}
                                    >
                                        {resetLoading ? "Sending reset link..." : "Forgot password?"}
                                    </Button>
                                </form>
                            </Form>
                        </CardContent>
                    </>
                )}

                {step.kind === "mfa_setup" && step.choice === "choose" && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Set up two-factor</CardTitle>
                            <CardDescription>
                                Choose how you want to verify future sign-ins. You can change this
                                later from your account settings.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <Button
                                type="button"
                                className="w-full"
                                disabled={busy || !supportsPasskey}
                                onClick={registerPasskey}
                            >
                                {busy ? "Working..." : "Use a passkey (Touch ID, Face ID, security key)"}
                            </Button>
                            <p className="text-xs text-muted-foreground">
                                Recommended. Your device handles authentication; nothing is shared
                                with the server beyond the public key.
                            </p>
                            <Separator />
                            <Button
                                type="button"
                                variant="outline"
                                className="w-full"
                                disabled={busy}
                                onClick={pickTotpSetup}
                            >
                                Use an authenticator app (TOTP)
                            </Button>
                            <p className="text-xs text-muted-foreground">
                                Scan a QR code with Google Authenticator, 1Password, Authy, etc.
                            </p>
                            <Separator />
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={backToCredentials}
                                disabled={busy}
                            >
                                Back
                            </Button>
                        </CardContent>
                    </>
                )}

                {step.kind === "mfa_setup" && step.choice === "passkey" && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Register a passkey</CardTitle>
                            <CardDescription>
                                {supportsPasskey
                                    ? "Click Continue to create a passkey for this account. Your browser will prompt for biometrics or your security key."
                                    : "This browser doesn't support passkeys. Try Safari, Chrome, Edge, or Firefox on a recent OS."}
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <Form {...labelForm}>
                                <FormField
                                    control={labelForm.control}
                                    name="device_label"
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>Device name (optional)</FormLabel>
                                            <FormControl>
                                                <Input
                                                    placeholder="e.g. MacBook Pro"
                                                    autoComplete="off"
                                                    {...field}
                                                />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                            </Form>
                            <Button
                                type="button"
                                className="w-full"
                                onClick={registerPasskey}
                                disabled={busy || !supportsPasskey}
                            >
                                {busy ? "Waiting for prompt..." : "Continue"}
                            </Button>
                            {step.methods.includes("totp") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={pickTotpSetup}
                                    disabled={busy}
                                >
                                    Use an authenticator app instead
                                </Button>
                            )}
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full text-muted-foreground"
                                onClick={backToCredentials}
                                disabled={busy}
                            >
                                Back
                            </Button>
                        </CardContent>
                    </>
                )}

                {step.kind === "mfa_setup" && step.choice === "totp" && step.totp && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Set up authenticator</CardTitle>
                            <CardDescription>
                                Scan the QR with an authenticator app (Google Authenticator,
                                1Password, Authy) and enter the 6-digit code it shows.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex justify-center rounded-lg bg-white p-4">
                                <QRCodeSVG value={step.totp.provisioning_uri} size={192} includeMargin={false} />
                            </div>
                            <div className="rounded-md border border-border bg-muted/40 p-3 text-xs">
                                <div className="text-muted-foreground mb-1">
                                    Can&apos;t scan? Enter this secret manually:
                                </div>
                                <div className="break-all font-mono text-foreground">
                                    {step.totp.secret}
                                </div>
                            </div>
                            <Form {...codeForm}>
                                <form onSubmit={codeForm.handleSubmit(submitSetupTotp)} className="space-y-3">
                                    <FormField
                                        control={codeForm.control}
                                        name="code"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>6-digit code</FormLabel>
                                                <FormControl>
                                                    <Input
                                                        inputMode="numeric"
                                                        autoComplete="one-time-code"
                                                        placeholder="123456"
                                                        maxLength={6}
                                                        {...field}
                                                    />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <Button type="submit" className="w-full" disabled={busy}>
                                        {busy ? "Verifying..." : "Verify and continue"}
                                    </Button>
                                    {step.methods.includes("webauthn") && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            className="w-full"
                                            onClick={() =>
                                                setStep({ ...step, choice: "passkey", totp: undefined })
                                            }
                                            disabled={busy}
                                        >
                                            Use a passkey instead
                                        </Button>
                                    )}
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        className="w-full text-muted-foreground"
                                        onClick={backToCredentials}
                                        disabled={busy}
                                    >
                                        Back
                                    </Button>
                                </form>
                            </Form>
                        </CardContent>
                    </>
                )}

                {step.kind === "mfa_verify" && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Two-factor verification</CardTitle>
                            <CardDescription>
                                {step.mode === "passkey"
                                    ? `Use your registered passkey for ${step.email}.`
                                    : step.mode === "recovery"
                                      ? `Enter one of your saved recovery codes for ${step.email}.`
                                      : `Enter the 6-digit code from your authenticator app for ${step.email}.`}
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {step.mode === "passkey" && (
                                <>
                                    <Button
                                        type="button"
                                        className="w-full"
                                        onClick={authenticatePasskey}
                                        disabled={busy || !supportsPasskey}
                                    >
                                        {busy ? "Waiting for prompt..." : "Sign in with passkey"}
                                    </Button>
                                    {step.methods.includes("totp") && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            className="w-full"
                                            onClick={() => setStep({ ...step, mode: "totp" })}
                                            disabled={busy}
                                        >
                                            Use authenticator code instead
                                        </Button>
                                    )}
                                    {step.methods.includes("recovery_code") && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            className="w-full"
                                            onClick={() => setStep({ ...step, mode: "recovery" })}
                                            disabled={busy}
                                        >
                                            Use a recovery code instead
                                        </Button>
                                    )}
                                </>
                            )}
                            {step.mode === "totp" && (
                                <Form {...codeForm}>
                                    <form onSubmit={codeForm.handleSubmit(submitVerifyTotp)} className="space-y-3">
                                        <FormField
                                            control={codeForm.control}
                                            name="code"
                                            render={({ field }) => (
                                                <FormItem>
                                                    <FormLabel>6-digit code</FormLabel>
                                                    <FormControl>
                                                        <Input
                                                            inputMode="numeric"
                                                            autoComplete="one-time-code"
                                                            placeholder="123456"
                                                            maxLength={6}
                                                            {...field}
                                                        />
                                                    </FormControl>
                                                    <FormMessage />
                                                </FormItem>
                                            )}
                                        />
                                        <Button type="submit" className="w-full" disabled={busy}>
                                            {busy ? "Verifying..." : "Verify"}
                                        </Button>
                                        {step.methods.includes("webauthn") && (
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                className="w-full"
                                                onClick={() => setStep({ ...step, mode: "passkey" })}
                                                disabled={busy}
                                            >
                                                Use passkey instead
                                            </Button>
                                        )}
                                        {step.methods.includes("recovery_code") && (
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                className="w-full"
                                                onClick={() => setStep({ ...step, mode: "recovery" })}
                                                disabled={busy}
                                            >
                                                Use a recovery code instead
                                            </Button>
                                        )}
                                    </form>
                                </Form>
                            )}
                            {step.mode === "recovery" && (
                                <Form {...recoveryForm}>
                                    <form onSubmit={recoveryForm.handleSubmit(submitVerifyRecovery)} className="space-y-3">
                                        <FormField
                                            control={recoveryForm.control}
                                            name="code"
                                            render={({ field }) => (
                                                <FormItem>
                                                    <FormLabel>Recovery code</FormLabel>
                                                    <FormControl>
                                                        <Input
                                                            autoComplete="off"
                                                            placeholder="xxxxxxxx"
                                                            {...field}
                                                        />
                                                    </FormControl>
                                                    <FormMessage />
                                                </FormItem>
                                            )}
                                        />
                                        <Button type="submit" className="w-full" disabled={busy}>
                                            {busy ? "Verifying..." : "Verify recovery code"}
                                        </Button>
                                        {step.methods.includes("totp") && (
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                className="w-full"
                                                onClick={() => setStep({ ...step, mode: "totp" })}
                                                disabled={busy}
                                            >
                                                Use authenticator code instead
                                            </Button>
                                        )}
                                    </form>
                                </Form>
                            )}
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full text-muted-foreground"
                                onClick={backToCredentials}
                                disabled={busy}
                            >
                                Back
                            </Button>
                        </CardContent>
                    </>
                )}

                {step.kind === "recovery_codes" && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Save your recovery codes</CardTitle>
                            <CardDescription>
                                These codes let you sign in if you lose your authenticator. Each
                                code works once. They will not be shown again — copy them somewhere
                                safe before continuing.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="rounded-md border border-border bg-muted/40 p-3 font-mono text-sm space-y-1">
                                {step.codes.map((c) => (
                                    <div key={c}>{c}</div>
                                ))}
                            </div>
                            <Button
                                type="button"
                                variant="outline"
                                className="w-full"
                                onClick={() => {
                                    void navigator.clipboard
                                        .writeText(step.codes.join("\n"))
                                        .then(() => toast.success("Recovery codes copied to clipboard"))
                                        .catch(() => toast.error("Couldn't copy. Select them manually."))
                                }}
                            >
                                Copy codes
                            </Button>
                            <Button
                                type="button"
                                className="w-full"
                                onClick={continueAfterRecoveryCodes}
                                disabled={busy}
                            >
                                {busy ? "Continuing..." : "I've saved them — continue"}
                            </Button>
                        </CardContent>
                    </>
                )}
            </Card>
        </div>
    )
}
