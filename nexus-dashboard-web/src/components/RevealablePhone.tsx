import { useState, type MouseEvent } from "react"
import { Phone } from "lucide-react"
import { toast } from "sonner"

import { revealPhone } from "@/lib/calls-api"
import { cn } from "@/lib/utils"

interface RevealablePhoneProps {
    /** ID passed to the reveal function — a call_id by default, or a contact_id. */
    callId: string
    masked: string | null
    available: boolean
    className?: string
    /**
     * Audited reveal call. Defaults to the calls endpoint; the Patients page
     * passes the contacts endpoint. Must return the full phone number.
     */
    revealFn?: (id: string) => Promise<{ phone: string | null }>
}

/**
 * Callback number, masked by default (last 4 digits). Clicking "Reveal" calls
 * the audited reveal endpoint and shows the full number. Stops row-click
 * propagation so it works inside clickable table rows.
 */
export function RevealablePhone({ callId, masked, available, className, revealFn }: RevealablePhoneProps) {
    const [full, setFull] = useState<string | null>(null)
    const [loading, setLoading] = useState(false)

    if (!available || !masked) {
        return <span className={cn("text-muted-foreground", className)}>—</span>
    }

    async function handleReveal(e: MouseEvent) {
        e.stopPropagation()
        if (full || loading) return
        setLoading(true)
        try {
            const res = await (revealFn ?? revealPhone)(callId)
            setFull(res.phone)
        } catch {
            toast.error("Couldn't reveal the phone number")
        } finally {
            setLoading(false)
        }
    }

    return (
        <span className={cn("inline-flex items-center gap-1.5", className)}>
            <Phone className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="font-mono tabular-nums">{full ?? masked}</span>
            {!full && (
                <button
                    type="button"
                    onClick={handleReveal}
                    disabled={loading}
                    className="text-xs font-medium text-primary transition-colors hover:underline disabled:opacity-50"
                >
                    {loading ? "…" : "Reveal"}
                </button>
            )}
        </span>
    )
}
