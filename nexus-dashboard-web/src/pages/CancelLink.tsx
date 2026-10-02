import { useEffect, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    campaignLinkGoneMessage,
    cancelAppointment,
    fetchCancellation,
    type CancellationDetails,
} from "@/lib/booking-link-api"

/**
 * Rendered from the wall clock the practice software returned, never re-zoned
 * into a configured timezone — the two disagree, and showing a patient the
 * wrong hour on the screen that asks them to give up their slot is the worst
 * place to be an hour out.
 */
function appointmentLabel(iso: string) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
    if (!m) return iso
    const [, y, mo, d, hh, mm] = m
    const day = new Date(`${y}-${mo}-${d}T12:00:00`).toLocaleDateString(undefined, {
        weekday: "long",
        day: "numeric",
        month: "long",
    })
    const hour = Number(hh)
    const suffix = hour < 12 ? "am" : "pm"
    const h12 = hour % 12 === 0 ? 12 : hour % 12
    return `${day} at ${h12}:${mm}${suffix}`
}

/**
 * Cancelling an appointment from a link.
 *
 * Two steps on purpose. Opening the link only asks the question; the
 * cancellation happens when the patient answers it. Messaging apps and mail
 * clients follow links to build previews, so a page that cancelled on load
 * would cancel appointments nobody ever tapped — and unlike a booking, the
 * patient cannot put it back.
 */
type Phase = "loading" | "asking" | "cancelling" | "done" | "error"

export default function CancelLink() {
    const [params] = useSearchParams()
    const navigate = useNavigate()
    const token = params.get("token") ?? ""

    const [phase, setPhase] = useState<Phase>(token ? "loading" : "error")
    const [clinic, setClinic] = useState("the clinic")
    const [appointment, setAppointment] =
        useState<CancellationDetails["appointment"]>(null)
    const [message, setMessage] = useState(
        token ? "" : "This link isn't valid. Please contact the clinic directly.",
    )

    useEffect(() => {
        if (!token) return
        let cancelled = false
        fetchCancellation(token)
            .then((d) => {
                if (cancelled) return
                if (d.clinic_name) setClinic(d.clinic_name)
                if (d.identity_required) {
                    // The appointment is deliberately withheld until they prove
                    // who they are. Send them through and come straight back.
                    navigate(
                        `/book/identify?token=${token}&next=/book/cancel`,
                        { replace: true },
                    )
                    return
                }
                setAppointment(d.appointment)
                setPhase(d.already_cancelled ? "done" : "asking")
            })
            .catch((err) => {
                if (cancelled) return
                const status = err?.response?.status
                const reason = err?.response?.data?.error
                setMessage(
                    campaignLinkGoneMessage(err)
                        ?? (reason === "no_appointment"
                          ? "There's no appointment on this link to cancel. Please contact the clinic."
                          : status === 503
                            ? "We can't reach the clinic's system right now. Please try again shortly."
                            : "This link isn't valid. Please contact the clinic directly."),
                )
                setPhase("error")
            })
        return () => {
            cancelled = true
        }
    }, [token, navigate])

    async function confirmCancel() {
        setPhase("cancelling")
        try {
            await cancelAppointment(token)
            setPhase("done")
        } catch {
            setMessage("Sorry — we couldn't cancel that just now. Please contact the clinic.")
            setPhase("error")
        }
    }

    return (
        <div className="min-h-screen bg-muted/30 px-4 py-8 flex justify-center">
            <div className="w-full max-w-md">
                <Card>
                    <CardHeader>
                        <CardTitle className="text-xl">
                            {phase === "done" ? "Appointment cancelled" : "Cancel your appointment"}
                        </CardTitle>
                        {phase === "asking" && (
                            <CardDescription>
                                This will cancel your appointment at {clinic}.
                            </CardDescription>
                        )}
                    </CardHeader>

                    <CardContent className="space-y-4">
                        {phase === "loading" && (
                            <div className="h-10 rounded-md bg-muted animate-pulse" aria-busy="true" />
                        )}

                        {phase === "error" && (
                            <p className="text-sm text-muted-foreground">{message}</p>
                        )}

                        {(phase === "asking" || phase === "cancelling") && (
                            <div className="space-y-3">
                                {appointment ? (
                                    <div className="rounded-md border bg-muted/40 p-4 space-y-1">
                                        <p className="font-medium">
                                            {appointmentLabel(appointment.start)}
                                        </p>
                                        {appointment.provider_name && (
                                            <p className="text-sm text-muted-foreground">
                                                with {appointment.provider_name}
                                            </p>
                                        )}
                                        {appointment.reason && (
                                            <p className="text-sm text-muted-foreground">
                                                {appointment.reason}
                                            </p>
                                        )}
                                        <p className="text-sm text-muted-foreground pt-1">
                                            {clinic}
                                        </p>
                                    </div>
                                ) : (
                                    // Never imply we know which visit this is when
                                    // we could not look it up.
                                    <p className="text-sm text-muted-foreground">
                                        We couldn't load the details of this appointment.
                                        If you're unsure which visit this is, contact{" "}
                                        {clinic} before cancelling.
                                    </p>
                                )}
                                <p className="text-sm text-muted-foreground">
                                    If you'd rather move it than cancel it, contact {clinic} and
                                    they'll find you another time.
                                </p>
                                <Button
                                    variant="destructive"
                                    className="w-full h-12"
                                    disabled={phase === "cancelling"}
                                    onClick={confirmCancel}
                                >
                                    {phase === "cancelling"
                                        ? "Cancelling…"
                                        : "Yes, cancel my appointment"}
                                </Button>
                            </div>
                        )}

                        {phase === "done" && (
                            <div className="space-y-3">
                                {appointment && (
                                    <p className="text-sm">
                                        {appointmentLabel(appointment.start)}
                                        {appointment.provider_name
                                            ? ` with ${appointment.provider_name}`
                                            : ""}
                                    </p>
                                )}
                                <p className="text-sm text-muted-foreground">
                                    Your appointment at {clinic} has been cancelled. Contact them
                                    any time to book again.
                                </p>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}
