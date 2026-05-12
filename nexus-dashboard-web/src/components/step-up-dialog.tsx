/**
 * Step-up MFA verification dialog.
 *
 * Pure component on purpose: the imperative hook side
 * (`useStepUp` in components/use-step-up.tsx) owns the promise dance.
 * Keeping the hook in a sibling file satisfies Fast Refresh's
 * only-export-components rule and makes the dialog testable in
 * isolation.
 */

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import axios from "axios"
import { toast } from "sonner"
import {
    startAuthentication,
    browserSupportsWebAuthn,
} from "@simplewebauthn/browser"

import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
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
import {
    stepUpVerifyTotp,
    stepUpVerifyRecoveryCode,
    stepUpWebauthnOptions,
    stepUpWebauthnVerify,
    type StepUpChallenge,
} from "@/lib/security-api"

const codeSchema = z.object({
    code: z.string().min(6, { message: "Enter the 6-digit code" }),
})
const recoverySchema = z.object({
    code: z.string().min(8, { message: "Enter a recovery code" }),
})

type Mode = "passkey" | "totp" | "recovery"

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

export function StepUpDialog({
    open,
    title,
    description,
    challenge,
    onClose,
}: {
    open: boolean
    title: string
    description: string
    challenge: StepUpChallenge | null
    onClose: (ticket: string | null) => void
}) {
    const supportsPasskey = typeof window !== "undefined" && browserSupportsWebAuthn()
    const [busy, setBusy] = useState(false)
    const [mode, setMode] = useState<Mode | null>(null)
    const codeForm = useForm<z.infer<typeof codeSchema>>({
        resolver: zodResolver(codeSchema),
        defaultValues: { code: "" },
    })
    const recoveryForm = useForm<z.infer<typeof recoverySchema>>({
        resolver: zodResolver(recoverySchema),
        defaultValues: { code: "" },
    })

    // Pick the default mode the first render after the challenge lands.
    // Use a derived value rather than useEffect so the initial render
    // already shows the right form.
    const effectiveMode: Mode | null =
        challenge == null
            ? null
            : mode ??
              (challenge.methods.includes("webauthn")
                  ? "passkey"
                  : challenge.methods.includes("totp")
                    ? "totp"
                    : challenge.methods.includes("recovery_code")
                      ? "recovery"
                      : null)

    async function submitTotp(values: z.infer<typeof codeSchema>) {
        if (!challenge) return
        setBusy(true)
        try {
            const { mfa_ticket } = await stepUpVerifyTotp(challenge.mfa_ticket, values.code.trim())
            onClose(mfa_ticket)
            resetForms()
        } catch (err) {
            toast.error(getDetail(err, "MFA verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function submitRecovery(values: z.infer<typeof recoverySchema>) {
        if (!challenge) return
        setBusy(true)
        try {
            const { mfa_ticket } = await stepUpVerifyRecoveryCode(challenge.mfa_ticket, values.code.trim())
            onClose(mfa_ticket)
            resetForms()
        } catch (err) {
            toast.error(getDetail(err, "Recovery code verification failed"))
        } finally {
            setBusy(false)
        }
    }

    async function submitPasskey() {
        if (!challenge) return
        if (!supportsPasskey) {
            toast.error("This browser doesn't support passkeys.")
            return
        }
        setBusy(true)
        try {
            const { options } = await stepUpWebauthnOptions(challenge.mfa_ticket)
            const credential = await startAuthentication({ optionsJSON: options })
            const { mfa_ticket } = await stepUpWebauthnVerify(challenge.mfa_ticket, credential)
            onClose(mfa_ticket)
            resetForms()
        } catch (err) {
            if (isWebAuthnUserCancel(err)) return
            toast.error(getDetail(err, "Passkey verification failed"))
        } finally {
            setBusy(false)
        }
    }

    function resetForms() {
        setMode(null)
        codeForm.reset({ code: "" })
        recoveryForm.reset({ code: "" })
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (!next && !busy) {
                    onClose(null)
                    resetForms()
                }
            }}
        >
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>
                {effectiveMode === "passkey" && (
                    <div className="space-y-3">
                        <Button
                            type="button"
                            className="w-full"
                            onClick={submitPasskey}
                            disabled={busy || !supportsPasskey}
                        >
                            {busy ? "Waiting for prompt..." : "Verify with passkey"}
                        </Button>
                        {challenge?.methods.includes("totp") && (
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => setMode("totp")}
                                disabled={busy}
                            >
                                Use authenticator code instead
                            </Button>
                        )}
                        {challenge?.methods.includes("recovery_code") && (
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                onClick={() => setMode("recovery")}
                                disabled={busy}
                            >
                                Use a recovery code instead
                            </Button>
                        )}
                    </div>
                )}
                {effectiveMode === "totp" && (
                    <Form {...codeForm}>
                        <form onSubmit={codeForm.handleSubmit(submitTotp)} className="space-y-3">
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
                                {busy ? "Verifying..." : "Confirm"}
                            </Button>
                            {challenge?.methods.includes("webauthn") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={() => setMode("passkey")}
                                    disabled={busy}
                                >
                                    Use passkey instead
                                </Button>
                            )}
                            {challenge?.methods.includes("recovery_code") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={() => setMode("recovery")}
                                    disabled={busy}
                                >
                                    Use a recovery code instead
                                </Button>
                            )}
                        </form>
                    </Form>
                )}
                {effectiveMode === "recovery" && (
                    <Form {...recoveryForm}>
                        <form onSubmit={recoveryForm.handleSubmit(submitRecovery)} className="space-y-3">
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
                                {busy ? "Verifying..." : "Confirm with recovery code"}
                            </Button>
                            {challenge?.methods.includes("totp") && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="w-full"
                                    onClick={() => setMode("totp")}
                                    disabled={busy}
                                >
                                    Use authenticator code instead
                                </Button>
                            )}
                        </form>
                    </Form>
                )}
                {effectiveMode === null && challenge && (
                    <p className="text-sm text-muted-foreground">
                        No MFA methods are registered for this account, so no
                        sensitive change can be confirmed.
                    </p>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    className="w-full text-muted-foreground"
                    onClick={() => {
                        onClose(null)
                        resetForms()
                    }}
                    disabled={busy}
                >
                    Cancel
                </Button>
            </DialogContent>
        </Dialog>
    )
}
