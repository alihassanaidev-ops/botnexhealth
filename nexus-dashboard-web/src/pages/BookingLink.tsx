import { useEffect, useMemo, useState } from "react"
import { useParams, useSearchParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    bookSlot,
    fetchSlots,
    type LinkAction,
    type SlotOption,
    type SlotsResponse,
} from "@/lib/booking-link-api"

/**
 * The page a patient lands on from a booking link in a text message.
 *
 * Not part of the dashboard: no sidebar, no nav, no login. The signed token in
 * the URL is the whole of the authentication, and the patient is on a phone,
 * mid-errand, deciding in seconds. Everything here follows from that — the
 * slots are on screen at first paint, choosing one and confirming is two taps,
 * and no state is worth a spinner the patient has to wait through twice.
 */

type Phase = "loading" | "choosing" | "booking" | "done" | "error"

/** Group slots by their local day so the list reads as a diary, not a dump. */
function byDay(slots: SlotOption[], timeZone: string | undefined) {
    const groups = new Map<string, SlotOption[]>()
    for (const slot of slots) {
        const day = new Date(slot.start).toLocaleDateString(undefined, {
            weekday: "long",
            month: "long",
            day: "numeric",
            timeZone,
        })
        groups.set(day, [...(groups.get(day) ?? []), slot])
    }
    return [...groups.entries()]
}

function timeLabel(iso: string, timeZone: string | undefined) {
    return new Date(iso).toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
        timeZone,
    })
}

export default function BookingLink() {
    const { action } = useParams<{ action: string }>()
    const [params] = useSearchParams()
    const token = params.get("token") ?? ""

    const [phase, setPhase] = useState<Phase>("loading")
    const [data, setData] = useState<SlotsResponse | null>(null)
    const [chosen, setChosen] = useState<SlotOption | null>(null)
    const [outcome, setOutcome] = useState<"booked" | "pending" | null>(null)
    const [message, setMessage] = useState("")

    const linkAction = (action === "reschedule" ? "reschedule" : "book") as LinkAction
    const isReschedule = linkAction === "reschedule"

    useEffect(() => {
        let cancelled = false
        if (!token) {
            setMessage("This link isn't valid. Please contact the clinic directly.")
            setPhase("error")
            return
        }
        fetchSlots(linkAction, token)
            .then((result) => {
                if (cancelled) return
                setData(result)
                if (result.already_booked) {
                    setOutcome("booked")
                    setPhase("done")
                } else {
                    setPhase("choosing")
                }
            })
            .catch((err) => {
                if (cancelled) return
                const status = err?.response?.status
                // Three different things, and the patient can act on each
                // differently. Telling someone their link is broken when the
                // clinic's system is simply unreachable sends them chasing a
                // problem they cannot fix.
                setMessage(
                    status === 410
                        ? "This link has expired. Please contact the clinic and they'll help you."
                        : status === 503
                          ? "We can't load available times right now. Please try again shortly, or contact the clinic."
                          : "This link isn't valid. Please contact the clinic directly.",
                )
                setPhase("error")
            })
        return () => {
            cancelled = true
        }
    }, [linkAction, token])

    const days = useMemo(
        () => byDay(data?.slots ?? [], data?.timezone ?? undefined),
        [data],
    )

    async function confirm() {
        if (!chosen) return
        setPhase("booking")
        try {
            const result = await bookSlot(linkAction, token, chosen.start)
            setOutcome(result.status === "pending" ? "pending" : "booked")
            setPhase("done")
        } catch (err: unknown) {
            const status = (err as { response?: { status?: number } })?.response?.status
            if (status === 409) {
                // Someone took it while they were deciding — most likely the
                // clinic booking it over the phone, which goes through the same
                // path. Re-offer rather than dead-end: the whole point is that
                // they finish. The refusal carries the refreshed list, so this
                // costs no second request and no second wait.
                const fresh = (err as { response?: { data?: { slots?: SlotOption[] } } })
                    ?.response?.data?.slots
                setMessage("Sorry — that time has just been taken. Here are the latest times.")
                setChosen(null)
                if (fresh && data) {
                    setData({ ...data, slots: fresh })
                } else {
                    const refreshed = await fetchSlots(linkAction, token).catch(() => null)
                    if (refreshed) setData(refreshed)
                }
                setPhase("choosing")
                return
            }
            setMessage("Sorry — we couldn't book that just now. Please contact the clinic.")
            setPhase("error")
        }
    }

    const clinic = data?.clinic_name || "the clinic"

    return (
        <div className="min-h-screen bg-muted/30 px-4 py-8 flex justify-center">
            <div className="w-full max-w-md">
                <Card>
                    <CardHeader>
                        <CardTitle className="text-xl">
                            {phase === "done"
                                ? outcome === "pending"
                                    ? "Almost done"
                                    : "You're booked"
                                : isReschedule
                                  ? "Choose a new time"
                                  : "Book your appointment"}
                        </CardTitle>
                        {phase !== "done" && phase !== "error" && (
                            <CardDescription>
                                Pick a time that suits you at {clinic}.
                            </CardDescription>
                        )}
                    </CardHeader>

                    <CardContent className="space-y-4">
                        {phase === "loading" && (
                            <div className="space-y-2" aria-busy="true" aria-label="Loading times">
                                {[0, 1, 2].map((i) => (
                                    <div key={i} className="h-10 rounded-md bg-muted animate-pulse" />
                                ))}
                            </div>
                        )}

                        {phase === "error" && (
                            <p className="text-sm text-muted-foreground">{message}</p>
                        )}

                        {(phase === "choosing" || phase === "booking") && (
                            <>
                                {message && (
                                    <p className="text-sm text-destructive" role="status">
                                        {message}
                                    </p>
                                )}

                                {days.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">
                                        There are no times available online at the moment. Please
                                        contact {clinic} and they'll find one for you.
                                    </p>
                                ) : (
                                    days.map(([day, slots]) => (
                                        <div key={day} className="space-y-2">
                                            <h2 className="text-sm font-medium text-muted-foreground">
                                                {day}
                                            </h2>
                                            <div className="grid grid-cols-3 gap-2">
                                                {slots.map((slot) => {
                                                    const active = chosen?.start === slot.start
                                                    return (
                                                        <Button
                                                            key={slot.start}
                                                            type="button"
                                                            variant={active ? "default" : "outline"}
                                                            aria-pressed={active}
                                                            // 44px: a finger, not a cursor.
                                                            className="h-11"
                                                            onClick={() => {
                                                                setMessage("")
                                                                setChosen(slot)
                                                            }}
                                                        >
                                                            {timeLabel(slot.start, data?.timezone ?? undefined)}
                                                        </Button>
                                                    )
                                                })}
                                            </div>
                                        </div>
                                    ))
                                )}
                            </>
                        )}

                        {phase === "done" && (
                            <p className="text-sm text-muted-foreground">
                                {outcome === "pending"
                                    ? `We're confirming this with ${clinic} and will let you know shortly.`
                                    : `Your appointment at ${clinic} is confirmed. See you then.`}
                            </p>
                        )}
                    </CardContent>
                </Card>

                {/* The confirm step is pinned so it is reachable without scrolling
                    back up a long list of times on a phone. */}
                {(phase === "choosing" || phase === "booking") && chosen && (
                    <div className="sticky bottom-4 mt-4">
                        <Button
                            className="w-full h-12 shadow-lg"
                            disabled={phase === "booking"}
                            onClick={confirm}
                        >
                            {phase === "booking"
                                ? "Booking…"
                                : `Confirm ${timeLabel(chosen.start, data?.timezone ?? undefined)}`}
                        </Button>
                    </div>
                )}
            </div>
        </div>
    )
}
