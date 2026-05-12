/**
 * The MFA flow component, shared by every entry point that needs to
 * push a user through MFA setup or verification:
 *
 *   - Login (after password)
 *   - SetPassword / ResetPassword (after consuming the email token)
 *   - Step-up modal (before a sensitive factor-management operation —
 *     see useStepUp() / StepUpDialog)
 *
 * The component is purely "given an MfaChallengeResponse, drive it to
 * an AuthSession". Wrappers decide what to do with the AuthSession
 * (start a session, finish the step-up, etc.) via the
 * `onAuthenticated` callback.
 *
 * Keeping the state machine and the platform integration (WebAuthn,
 * QR rendering) in one place is what made it tractable to retrofit
 * MFA into the invite/reset password flows without copy-pasting the
 * mid-flow UI.
 */

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { QRCodeSVG } from "qrcode.react"
import { toast } from "sonner"
import axios from "axios"
import {
    startRegistration,
    startAuthentication,
    browserSupportsWebAuthn,
} from "@simplewebauthn/browser"

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
    type AuthSession,
    type MfaChallengeResponse,
    type TotpSetupOptions,
} from "@/lib/mfa-api"

const codeSchema = z.object({
    code: z.string().min(6, { message: "Enter the 6-digit code" }),
})

const recoverySchema = z.object({
    code: z.string().min(8, { message: "Enter a recovery code" }),
})

const labelSchema = z.object({
    device_label: z.string().max(64).optional(),
})

type Step =
    | { kind: "setup_choose" }
    | { kind: "setup_passkey" }
    | { kind: "setup_totp"; options: TotpSetupOptions }
    | { kind: "verify"; mode: "totp" | "passkey" | "recovery" }
    | { kind: "recovery_codes"; codes: string[]; session: AuthSession }

interface MfaFlowProps {
    challenge: MfaChallengeResponse
    onAuthenticated: (session: AuthSession) => void | Promise<void>
    onCancel?: () => void
}

function getDetail(error: unknown, fallback: string): string {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (typeof detail === "string" && detail.trim()) return detail
    }
    if (error instanceof Error && error.message) return error.message
    return fallback
}

function isWebAuthnUserCancel(err: unknown): boolean {
    const e = err as { name?: string }
    return e?.name === "NotAllowedError" || e?.name === "AbortError"
}

export function RecoveryCodesPanel({
    codes,
    onContinue,
    busy,
}: {
    codes: string[]
    onContinue: () => void | Promise<void>
    busy?: boolean
}) {
    return (
        <div className="space-y-4">
            <div className="rounded-md border border-border bg-muted/40 p-3 font-mono text-sm space-y-1">
                {codes.map((c) => (
                    <div key={c}>{c}</div>
                ))}
            </div>
            <Button
                type="button"
                variant="outline"
                className="w-full"
                onClick={() => {
                    void navigator.clipboard
                        .writeText(codes.join("\n"))
                        .then(() => toast.success("Recovery codes copied to clipboard"))
                        .catch(() => toast.error("Couldn't copy. Select them manually."))
                }}
            >
                Copy codes
            </Button>
            <Button type="button" className="w-full" onClick={onContinue} disabled={busy}>
                {busy ? "Continuing..." : "I've saved them — continue"}
            </Button>
        </div>
    )
}

export function MfaFlow({ challenge, onAuthenticated, onCancel }: MfaFlowProps) {
    const supportsPasskey = typeof window !== "undefined" && browserSupportsWebAuthn()

    const initialStep: Step =
        challenge.status === "mfa_setup_required"
            ? challenge.setup_methods.includes("totp") && challenge.setup_methods.includes("webauthn")
                ? { kind: "setup_choose" }
                : challenge.setup_methods.includes("webauthn")
                  ? { kind: "setup_passkey" }
                  : { kind: "setup_totp", options: { secret: "", provisioning_uri: "" } }
            : { kind: "verify", mode: challenge.methods.includes("webauthn") ? "passkey" : challenge.methods.includes("totp") ? "totp" : "recovery" }

    const [step, setStep] = useState<Step>(initialStep)
    const [busy, setBusy] = useState(false)

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

    async function pickTotpSetup() {
        setBusy(true)
        try {
            const options = await startTotpSetup(challenge.mfa_ticket)
            setStep({ kind: "setup_totp", options })
            codeForm.reset({ code: "" })
        } catch (err) {
            toast.error(getDetail(err, "Couldn't start TOTP setup"))
        } finally {
            setBusy(false)
        }
    }

    async function submitSetupTotp(values: z.infer<typeof codeSchema>) {
        setBusy(true)
        try {
            const session = await verifyTotpSetup(challenge.mfa_ticket, values.code.trim())
            const codes = session.recovery_codes ?? []
            if (codes.length > 0) {
                setStep({ kind: "recovery_codes", codes, session })
            } else {
                await onAuthenticated(session)
            }
        } catch (err) {
            toast.error(getDetail(err, "MFA setup verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function registerPasskey() {
        if (!supportsPasskey) {
            toast.error(
                "This browser doesn't support passkeys. Use Safari, Chrome, Edge, or Firefox on a recent OS.",
            )
            return
        }
        setBusy(true)
        try {
            const { options } = await startWebauthnRegistration(challenge.mfa_ticket)
            const credential = await startRegistration({ optionsJSON: options })
            const label = labelForm.getValues("device_label")?.trim() || undefined
            const session = await verifyWebauthnRegistration(challenge.mfa_ticket, credential, label)
            const codes = session.recovery_codes ?? []
            if (codes.length > 0) {
                setStep({ kind: "recovery_codes", codes, session })
            } else {
                await onAuthenticated(session)
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
        setBusy(true)
        try {
            const session = await verifyTotp(challenge.mfa_ticket, values.code.trim())
            await onAuthenticated(session)
        } catch (err) {
            toast.error(getDetail(err, "MFA verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function submitVerifyRecovery(values: z.infer<typeof recoverySchema>) {
        setBusy(true)
        try {
            const session = await verifyRecoveryCode(challenge.mfa_ticket, values.code.trim())
            await onAuthenticated(session)
        } catch (err) {
            toast.error(getDetail(err, "Recovery code verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function authenticatePasskey() {
        if (!supportsPasskey) {
            toast.error("This browser doesn't support passkeys.")
            return
        }
        setBusy(true)
        try {
            const { options } = await startWebauthnAuthentication(challenge.mfa_ticket)
            const credential = await startAuthentication({ optionsJSON: options })
            const session = await verifyWebauthnAuthentication(challenge.mfa_ticket, credential)
            await onAuthenticated(session)
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

    if (step.kind === "setup_choose") {
        return (
            <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                    Choose how you want to verify future sign-ins. You can change this
                    later from Security settings.
                </p>
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
                {onCancel && (
                    <>
                        <Separator />
                        <Button
                            type="button"
                            variant="ghost"
                            className="w-full"
                            onClick={onCancel}
                            disabled={busy}
                        >
                            Cancel
                        </Button>
                    </>
                )}
            </div>
        )
    }

    if (step.kind === "setup_passkey") {
        return (
            <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                    {supportsPasskey
                        ? "Click Continue to register a passkey. Your browser will prompt for biometrics or your security key."
                        : "This browser doesn't support passkeys. Try Safari, Chrome, Edge, or Firefox on a recent OS."}
                </p>
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
                {challenge.setup_methods.includes("totp") && (
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
                {onCancel && (
                    <Button
                        type="button"
                        variant="ghost"
                        className="w-full text-muted-foreground"
                        onClick={onCancel}
                        disabled={busy}
                    >
                        Cancel
                    </Button>
                )}
            </div>
        )
    }

    if (step.kind === "setup_totp") {
        return (
            <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                    Scan the QR with an authenticator app (Google Authenticator,
                    1Password, Authy) and enter the 6-digit code it shows.
                </p>
                <div className="flex justify-center rounded-lg bg-white p-4">
                    <QRCodeSVG value={step.options.provisioning_uri} size={192} includeMargin={false} />
                </div>
                <div className="rounded-md border border-border bg-muted/40 p-3 text-xs">
                    <div className="text-muted-foreground mb-1">
                        Can&apos;t scan? Enter this secret manually:
                    </div>
                    <div className="break-all font-mono text-foreground">
                        {step.options.secret}
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
                        {challenge.setup_methods.includes("webauthn") && (
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => setStep({ kind: "setup_passkey" })}
                                disabled={busy}
                            >
                                Use a passkey instead
                            </Button>
                        )}
                        {onCancel && (
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full text-muted-foreground"
                                onClick={onCancel}
                                disabled={busy}
                            >
                                Cancel
                            </Button>
                        )}
                    </form>
                </Form>
            </div>
        )
    }

    if (step.kind === "verify") {
        return (
            <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                    {step.mode === "passkey"
                        ? `Use your registered passkey for ${challenge.email}.`
                        : step.mode === "recovery"
                          ? `Enter one of your saved recovery codes for ${challenge.email}.`
                          : `Enter the 6-digit code from your authenticator app for ${challenge.email}.`}
                </p>
                {step.mode === "passkey" && (
                    <>
                        <Button
                            type="button"
                            className="w-full"
                            onClick={authenticatePasskey}
                            disabled={busy || !supportsPasskey}
                        >
                            {busy ? "Waiting for prompt..." : "Verify with passkey"}
                        </Button>
                        {challenge.methods.includes("totp") && (
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => setStep({ kind: "verify", mode: "totp" })}
                                disabled={busy}
                            >
                                Use authenticator code instead
                            </Button>
                        )}
                        {challenge.methods.includes("recovery_code") && (
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => setStep({ kind: "verify", mode: "recovery" })}
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
                            {challenge.methods.includes("webauthn") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={() => setStep({ kind: "verify", mode: "passkey" })}
                                    disabled={busy}
                                >
                                    Use passkey instead
                                </Button>
                            )}
                            {challenge.methods.includes("recovery_code") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={() => setStep({ kind: "verify", mode: "recovery" })}
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
                            {challenge.methods.includes("totp") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={() => setStep({ kind: "verify", mode: "totp" })}
                                    disabled={busy}
                                >
                                    Use authenticator code instead
                                </Button>
                            )}
                        </form>
                    </Form>
                )}
                {onCancel && (
                    <Button
                        type="button"
                        variant="ghost"
                        className="w-full text-muted-foreground"
                        onClick={onCancel}
                        disabled={busy}
                    >
                        Cancel
                    </Button>
                )}
            </div>
        )
    }

    // step.kind === "recovery_codes"
    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
                These codes let you sign in if you lose your authenticator. Each
                code works once. They will not be shown again — copy them somewhere
                safe before continuing.
            </p>
            <RecoveryCodesPanel
                codes={step.codes}
                busy={busy}
                onContinue={async () => {
                    setBusy(true)
                    try {
                        await onAuthenticated(step.session)
                    } finally {
                        setBusy(false)
                    }
                }}
            />
        </div>
    )
}
