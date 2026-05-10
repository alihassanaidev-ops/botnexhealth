import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { QRCodeSVG } from "qrcode.react"
import { toast } from "sonner"

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
import {
    startTotpSetup,
    verifyTotpSetup,
    verifyTotp,
    verifyRecoveryCode,
    type TotpSetupOptions,
    type AuthSession,
} from "@/lib/mfa-api"
import axios from "axios"

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

type Step =
    | { kind: "credentials" }
    | { kind: "mfa_setup"; ticket: string; email: string; options: TotpSetupOptions }
    | { kind: "mfa_verify"; ticket: string; email: string; methods: string[]; useRecovery: boolean }
    | { kind: "recovery_codes"; codes: string[]; session: AuthSession }

function getDetail(error: unknown, fallback: string): string {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (typeof detail === "string" && detail.trim()) return detail
    }
    if (error instanceof Error && error.message) return error.message
    return fallback
}

export default function Login() {
    const { signIn, completeAuthSession, requestPasswordReset } = useAuth()
    const [step, setStep] = useState<Step>({ kind: "credentials" })
    const [busy, setBusy] = useState(false)
    const [resetLoading, setResetLoading] = useState(false)

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

    async function submitCredentials(values: z.infer<typeof credentialsSchema>) {
        setBusy(true)
        try {
            const result = await signIn(values.email, values.password)
            if (result.kind === "authenticated") {
                return // navigation handled inside AuthContext
            }
            const ch = result.challenge
            if (ch.status === "mfa_setup_required") {
                // Pull a fresh TOTP secret + provisioning URI bound to this ticket.
                const options = await startTotpSetup(ch.mfa_ticket)
                setStep({ kind: "mfa_setup", ticket: ch.mfa_ticket, email: ch.email, options })
            } else {
                setStep({
                    kind: "mfa_verify",
                    ticket: ch.mfa_ticket,
                    email: ch.email,
                    methods: ch.methods,
                    useRecovery: false,
                })
            }
            codeForm.reset({ code: "" })
            recoveryForm.reset({ code: "" })
        } catch (err) {
            // signIn already toasts on its own error path; nothing else to surface here.
            void err
        } finally {
            setBusy(false)
        }
    }

    async function submitSetupCode(values: z.infer<typeof codeSchema>) {
        if (step.kind !== "mfa_setup") return
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

    async function submitVerifyCode(values: z.infer<typeof codeSchema>) {
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

    async function submitRecoveryCode(values: z.infer<typeof recoverySchema>) {
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

                {step.kind === "mfa_setup" && (
                    <>
                        <CardHeader>
                            <CardTitle className="text-2xl">Set up two-factor</CardTitle>
                            <CardDescription>
                                Scan the QR code with an authenticator app (Google Authenticator,
                                1Password, Authy) and enter the 6-digit code it shows.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
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
                                <form onSubmit={codeForm.handleSubmit(submitSetupCode)} className="space-y-3">
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
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        className="w-full"
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
                                {step.useRecovery
                                    ? `Enter one of your saved recovery codes for ${step.email}.`
                                    : `Enter the 6-digit code from your authenticator app for ${step.email}.`}
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {step.useRecovery ? (
                                <Form {...recoveryForm}>
                                    <form onSubmit={recoveryForm.handleSubmit(submitRecoveryCode)} className="space-y-3">
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
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            className="w-full"
                                            onClick={() => setStep({ ...step, useRecovery: false })}
                                            disabled={busy}
                                        >
                                            Use authenticator code instead
                                        </Button>
                                    </form>
                                </Form>
                            ) : (
                                <Form {...codeForm}>
                                    <form onSubmit={codeForm.handleSubmit(submitVerifyCode)} className="space-y-3">
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
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            className="w-full"
                                            onClick={() => setStep({ ...step, useRecovery: true })}
                                            disabled={busy}
                                        >
                                            Use a recovery code instead
                                        </Button>
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
