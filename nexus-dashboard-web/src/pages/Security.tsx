/**
 * Security / MFA management page.
 *
 * Lists the user's enrolled factors and exposes the three destructive
 * operations: regenerate recovery codes, remove a passkey, disable
 * TOTP. Every destructive call is gated by a step-up MFA verification
 * via useStepUp().
 */

import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { toast } from "sonner"
import axios from "axios"
import { QRCodeSVG } from "qrcode.react"
import { startRegistration, browserSupportsWebAuthn } from "@simplewebauthn/browser"
import { KeyRound, Smartphone, ShieldCheck, Trash2, RefreshCw, Plus, Lock } from "lucide-react"

import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Input } from "@/components/ui/input"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form"
import { RecoveryCodesPanel } from "@/components/mfa-flow"
import { useStepUp } from "@/components/use-step-up"
import {
    getMfaStatus,
    listPasskeys,
    regenerateRecoveryCodes,
    removePasskey,
    disableTotp,
    addPasskeyOptions,
    addPasskeyVerify,
    addTotpOptions,
    addTotpVerify,
    type AddTotpOptions,
    type MfaStatusResponse,
    type WebAuthnCredentialSummary,
} from "@/lib/security-api"

const totpVerifySchema = z.object({
    code: z.string().min(6, { message: "Enter the 6-digit code" }),
})
const passkeyLabelSchema = z.object({
    device_label: z.string().max(64).optional(),
})

function detail(err: unknown, fallback: string): string {
    if (axios.isAxiosError(err)) {
        const d = err.response?.data?.detail
        if (typeof d === "string" && d.trim()) return d
    }
    if (err instanceof Error && err.message) return err.message
    return fallback
}

function isWebAuthnUserCancel(err: unknown): boolean {
    const e = err as { name?: string }
    return e?.name === "NotAllowedError" || e?.name === "AbortError"
}

export default function Security() {
    const [status, setStatus] = useState<MfaStatusResponse | null>(null)
    const [passkeys, setPasskeys] = useState<WebAuthnCredentialSummary[]>([])
    const [loading, setLoading] = useState(true)
    const [newRecoveryCodes, setNewRecoveryCodes] = useState<string[] | null>(null)
    const [addPasskeyOpen, setAddPasskeyOpen] = useState(false)
    const [addPasskeyBusy, setAddPasskeyBusy] = useState(false)
    const [addTotpDialog, setAddTotpDialog] = useState<AddTotpOptions | null>(null)
    const [addTotpBusy, setAddTotpBusy] = useState(false)
    const { stepUp, dialog } = useStepUp()
    const supportsPasskey = typeof window !== "undefined" && browserSupportsWebAuthn()

    const passkeyLabelForm = useForm<z.infer<typeof passkeyLabelSchema>>({
        resolver: zodResolver(passkeyLabelSchema),
        defaultValues: { device_label: "" },
    })
    const totpVerifyForm = useForm<z.infer<typeof totpVerifySchema>>({
        resolver: zodResolver(totpVerifySchema),
        defaultValues: { code: "" },
    })

    const refresh = useCallback(async () => {
        setLoading(true)
        try {
            const [statusData, passkeyData] = await Promise.all([
                getMfaStatus(),
                listPasskeys(),
            ])
            setStatus(statusData)
            setPasskeys(passkeyData)
        } catch (err) {
            const e = err as { message?: string }
            toast.error(e?.message || "Failed to load security settings")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        void refresh()
    }, [refresh])

    async function handleRegenerateRecoveryCodes() {
        const ticket = await stepUp({
            title: "Regenerate recovery codes",
            description:
                "Verify your MFA factor to regenerate codes. Your existing codes will stop working after this.",
        })
        if (!ticket) return
        try {
            const codes = await regenerateRecoveryCodes(ticket)
            setNewRecoveryCodes(codes)
            toast.success("New recovery codes generated")
            await refresh()
        } catch (err) {
            const e = err as { message?: string }
            toast.error(e?.message || "Failed to regenerate recovery codes")
        }
    }

    async function handleRemovePasskey(passkey: WebAuthnCredentialSummary) {
        const ticket = await stepUp({
            title: `Remove "${passkey.device_label ?? "passkey"}"`,
            description:
                "Verify your MFA factor to remove this passkey. If this is your last factor, you'll be prompted to enrol a new one on next sign-in.",
        })
        if (!ticket) return
        try {
            await removePasskey(passkey.id, ticket)
            toast.success("Passkey removed")
            await refresh()
        } catch (err) {
            const e = err as { message?: string }
            toast.error(e?.message || "Failed to remove passkey")
        }
    }

    async function handleDisableTotp() {
        const ticket = await stepUp({
            title: "Disable authenticator app",
            description:
                "Verify your MFA factor to disable TOTP. You'll need to re-enrol an authenticator on next sign-in if no other factor is registered.",
        })
        if (!ticket) return
        try {
            const msg = await disableTotp(ticket)
            toast.success(msg)
            await refresh()
        } catch (err) {
            const e = err as { message?: string }
            toast.error(e?.message || "Failed to disable authenticator")
        }
    }

    // Add-passkey flow is intentionally split across two user
    // gestures: step-up first, then a label-entry dialog the user
    // submits to actually trigger the browser's authenticator prompt.
    // Without the split the device label input was disabled the
    // moment the dialog opened (because startRegistration() was
    // already in flight), so labels in practice were always empty.
    // We stash the still-pending elevated ticket on a ref-like
    // state so the submit handler can complete the flow.
    const [addPasskeyTicket, setAddPasskeyTicket] = useState<string | null>(null)

    async function handleAddPasskey() {
        if (!supportsPasskey) {
            toast.error("This browser doesn't support passkeys.")
            return
        }
        const elevated = await stepUp({
            title: "Add a passkey",
            description:
                "Verify your existing MFA factor first. You'll be able to name the new passkey before your browser prompts you to register it.",
        })
        if (!elevated) return
        passkeyLabelForm.reset({ device_label: "" })
        setAddPasskeyTicket(elevated)
        setAddPasskeyOpen(true)
    }

    async function submitAddPasskey(values: z.infer<typeof passkeyLabelSchema>) {
        const elevated = addPasskeyTicket
        if (!elevated) return
        setAddPasskeyBusy(true)
        try {
            const opts = await addPasskeyOptions(elevated)
            // Browser asks the authenticator. Returns a serialised
            // credential the verify endpoint will sign-counter-check.
            const credential = await startRegistration({ optionsJSON: opts.options })
            const label = values.device_label?.trim() || undefined
            await addPasskeyVerify(opts.enrollment_ticket, credential, label)
            toast.success("Passkey added")
            await refresh()
            setAddPasskeyOpen(false)
            setAddPasskeyTicket(null)
        } catch (err) {
            if (isWebAuthnUserCancel(err)) {
                toast.message("Passkey prompt was cancelled.")
            } else {
                toast.error(detail(err, "Failed to register passkey"))
            }
            setAddPasskeyOpen(false)
            setAddPasskeyTicket(null)
        } finally {
            setAddPasskeyBusy(false)
        }
    }

    async function handleAddTotp() {
        const elevated = await stepUp({
            title: "Add authenticator app",
            description:
                "Verify your existing MFA factor first; you'll then be shown a QR code to scan into your authenticator app.",
        })
        if (!elevated) return
        try {
            const opts = await addTotpOptions(elevated)
            setAddTotpDialog(opts)
            totpVerifyForm.reset({ code: "" })
        } catch (err) {
            toast.error(detail(err, "Failed to start authenticator enrolment"))
        }
    }

    async function handleAddTotpVerify(values: z.infer<typeof totpVerifySchema>) {
        if (!addTotpDialog) return
        setAddTotpBusy(true)
        try {
            await addTotpVerify(addTotpDialog.enrollment_ticket, values.code.trim())
            toast.success("Authenticator app enabled")
            setAddTotpDialog(null)
            await refresh()
        } catch (err) {
            toast.error(detail(err, "Failed to verify authenticator"))
        } finally {
            setAddTotpBusy(false)
        }
    }

    return (
        <div className="space-y-6 p-6">
            <PageHeader
                icon={Lock}
                title="Security"
                description="Manage the multi-factor methods on your account."
            />

            {loading && <p className="text-sm text-muted-foreground">Loading…</p>}

            {!loading && status && (
                <>
                    {/* Passkeys */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <KeyRound className="h-5 w-5" /> Passkeys
                            </CardTitle>
                            <CardDescription>
                                Hardware-backed credentials (Touch ID, Face ID, Windows
                                Hello, security keys). Recommended over TOTP — phishing
                                resistant and bound to your device.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {passkeys.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    No passkeys registered yet.
                                </p>
                            ) : (
                                <ul className="space-y-2">
                                    {passkeys.map((p) => (
                                        <li
                                            key={p.id}
                                            data-testid="passkey-row"
                                            className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2"
                                        >
                                            <div className="min-w-0">
                                                <div className="font-medium truncate">
                                                    {p.device_label ?? "Unnamed passkey"}
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                    Added {new Date(p.created_at).toLocaleDateString()}
                                                    {p.last_used_at &&
                                                        ` · last used ${new Date(p.last_used_at).toLocaleDateString()}`}
                                                    {p.credential_backed_up && (
                                                        <Badge variant="secondary" className="ml-2">
                                                            Synced
                                                        </Badge>
                                                    )}
                                                </div>
                                            </div>
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => handleRemovePasskey(p)}
                                                aria-label={`Remove ${p.device_label ?? "passkey"}`}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                            <Button
                                variant="outline"
                                size="sm"
                                className="gap-2"
                                onClick={handleAddPasskey}
                                disabled={!supportsPasskey}
                            >
                                <Plus className="h-4 w-4" />
                                Add passkey
                            </Button>
                        </CardContent>
                    </Card>

                    {/* TOTP */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Smartphone className="h-5 w-5" /> Authenticator app (TOTP)
                            </CardTitle>
                            <CardDescription>
                                Time-based one-time passwords from Google
                                Authenticator, 1Password, Authy, etc.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="flex items-center justify-between gap-3">
                            <div className="text-sm">
                                {status.totp_enabled ? (
                                    <Badge variant="default" className="bg-emerald-500/15 text-emerald-500">
                                        Enabled
                                    </Badge>
                                ) : (
                                    <Badge variant="secondary">Not enabled</Badge>
                                )}
                            </div>
                            {status.totp_enabled ? (
                                <Button variant="outline" size="sm" onClick={handleDisableTotp}>
                                    Disable
                                </Button>
                            ) : (
                                <Button variant="outline" size="sm" className="gap-2" onClick={handleAddTotp}>
                                    <Plus className="h-4 w-4" /> Set up authenticator
                                </Button>
                            )}
                        </CardContent>
                    </Card>

                    {/* Recovery codes */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <ShieldCheck className="h-5 w-5" /> Recovery codes
                            </CardTitle>
                            <CardDescription>
                                One-time codes that work even when your authenticator
                                isn&apos;t available. Keep them somewhere safe.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <div className="text-sm text-muted-foreground">
                                {status.recovery_codes_remaining} unused
                                {status.recovery_codes_remaining === 1 ? " code" : " codes"} remaining
                            </div>
                            {newRecoveryCodes ? (
                                <>
                                    <p className="text-sm text-muted-foreground">
                                        New codes shown once below — save them, the
                                        previous set is now invalid.
                                    </p>
                                    <RecoveryCodesPanel
                                        codes={newRecoveryCodes}
                                        onContinue={() => setNewRecoveryCodes(null)}
                                    />
                                </>
                            ) : (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={handleRegenerateRecoveryCodes}
                                    className="gap-2"
                                >
                                    <RefreshCw className="h-4 w-4" />
                                    Regenerate codes
                                </Button>
                            )}
                        </CardContent>
                    </Card>

                    <Separator />
                    <p className="text-xs text-muted-foreground">
                        Every change here requires a fresh MFA verification — a
                        stolen session alone cannot remove or disable factors.
                    </p>
                </>
            )}

            {/* Add-passkey waiting dialog: opens after step-up while the
                browser prompts for biometrics / security key. Provides
                a place to type a device label and a clear progress
                state — the underlying flow itself is driven by
                handleAddPasskey above. */}
            {/* Label-entry dialog that gates the browser's
                passkey-registration prompt. The user types a name
                (e.g. "MacBook Pro"), clicks Continue, then the
                authenticator prompt opens — so labels actually make
                it onto the saved credential row. */}
            <Dialog
                open={addPasskeyOpen}
                onOpenChange={(open) => {
                    if (!open && !addPasskeyBusy) {
                        setAddPasskeyOpen(false)
                        setAddPasskeyTicket(null)
                    }
                }}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add a passkey</DialogTitle>
                        <DialogDescription>
                            Give this passkey a name so you can tell it apart
                            later. Clicking Continue will open your browser&apos;s
                            authenticator prompt.
                        </DialogDescription>
                    </DialogHeader>
                    <Form {...passkeyLabelForm}>
                        <form onSubmit={passkeyLabelForm.handleSubmit(submitAddPasskey)} className="space-y-3">
                            <FormField
                                control={passkeyLabelForm.control}
                                name="device_label"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Device name (optional)</FormLabel>
                                        <FormControl>
                                            <Input
                                                placeholder="e.g. MacBook Pro"
                                                autoComplete="off"
                                                disabled={addPasskeyBusy}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <DialogFooter>
                                <Button type="submit" disabled={addPasskeyBusy}>
                                    {addPasskeyBusy ? "Waiting for browser…" : "Continue"}
                                </Button>
                            </DialogFooter>
                        </form>
                    </Form>
                </DialogContent>
            </Dialog>

            {/* Add-TOTP dialog: QR + manual secret + verify code. Step-up
                already happened before this opens, so the user only needs
                to scan and confirm. */}
            <Dialog
                open={addTotpDialog !== null}
                onOpenChange={(open) => !open && !addTotpBusy && setAddTotpDialog(null)}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add authenticator app</DialogTitle>
                        <DialogDescription>
                            Scan the QR with Google Authenticator, 1Password, Authy
                            etc. and enter the 6-digit code to confirm.
                        </DialogDescription>
                    </DialogHeader>
                    {addTotpDialog && (
                        <div className="space-y-4">
                            <div className="flex justify-center rounded-lg bg-white p-4">
                                <QRCodeSVG value={addTotpDialog.provisioning_uri} size={192} includeMargin={false} />
                            </div>
                            <div className="rounded-md border border-border bg-muted/40 p-3 text-xs">
                                <div className="text-muted-foreground mb-1">
                                    Can&apos;t scan? Enter this secret manually:
                                </div>
                                <div className="break-all font-mono text-foreground">
                                    {addTotpDialog.secret}
                                </div>
                            </div>
                            <Form {...totpVerifyForm}>
                                <form
                                    onSubmit={totpVerifyForm.handleSubmit(handleAddTotpVerify)}
                                    className="space-y-3"
                                >
                                    <FormField
                                        control={totpVerifyForm.control}
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
                                    <Button type="submit" className="w-full" disabled={addTotpBusy}>
                                        {addTotpBusy ? "Verifying…" : "Verify and enable"}
                                    </Button>
                                </form>
                            </Form>
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            {dialog}
        </div>
    )
}
