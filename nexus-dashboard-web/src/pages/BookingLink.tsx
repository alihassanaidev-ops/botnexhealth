import { useEffect, useMemo, useState } from "react"
import { useParams, useSearchParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { AppointmentTypePicker } from "@/components/booking/AppointmentTypePicker"
import {
    bookSlot,
    fetchAppointmentTypes,
    fetchSlots,
    type AppointmentTypeOption,
    type LinkAction,
    type SlotOption,
    type SlotsResponse,
} from "@/lib/booking-link-api"

/** How far ahead to look. A week is where most people plan a visit from. */
const RANGE_OPTIONS = [
    { days: 7, label: "Next 7 days" },
    { days: 14, label: "Next 2 weeks" },
    { days: 30, label: "Next month" },
]

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

/**
 * Times are rendered from the wall clock the practice software returns, not by
 * converting into a configured timezone.
 *
 * The sandbox returns 08:00-07:00 for a location this database records as
 * America/Chicago — converting would display 10:00 for a slot the clinic calls
 * eight o'clock. The offset in the practice software's own response is the
 * authority on what the clinic means, so the wall-clock part is read directly
 * and never re-zoned.
 */
function wallClock(iso: string) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
    if (!m) return { date: iso.slice(0, 10), hour: 0, minute: 0 }
    return {
        date: `${m[1]}-${m[2]}-${m[3]}`,
        hour: Number(m[4]),
        minute: Number(m[5]),
    }
}

function timeLabel(iso: string) {
    const { hour, minute } = wallClock(iso)
    const suffix = hour < 12 ? "am" : "pm"
    const h12 = hour % 12 === 0 ? 12 : hour % 12
    return `${h12}:${String(minute).padStart(2, "0")}${suffix}`
}

function dayLabel(isoDate: string) {
    // Midday avoids the date shifting when the browser is behind UTC.
    return new Date(`${isoDate}T12:00:00`).toLocaleDateString(undefined, {
        weekday: "long",
        month: "long",
        day: "numeric",
    })
}

/** Group slots by the clinic's own day so the list reads as a diary. */
function byDay(slots: SlotOption[]) {
    const groups = new Map<string, SlotOption[]>()
    for (const slot of slots) {
        const { date } = wallClock(slot.start)
        groups.set(date, [...(groups.get(date) ?? []), slot])
    }
    return [...groups.entries()]
}

export default function BookingLink() {
    const { action } = useParams<{ action: string }>()
    const [params] = useSearchParams()
    const token = params.get("token") ?? ""

    const [phase, setPhase] = useState<Phase>("loading")
    const [data, setData] = useState<SlotsResponse | null>(null)
    const [chosen, setChosen] = useState<SlotOption | null>(null)
    const [types, setTypes] = useState<AppointmentTypeOption[]>([])
    const [typeId, setTypeId] = useState<string | null>(null)
    const [rangeDays, setRangeDays] = useState(7)
    const [refreshing, setRefreshing] = useState(false)
    const [outcome, setOutcome] = useState<"booked" | "pending" | null>(null)
    // What was actually booked, held so the confirmation can name it back.
    const [confirmedSlot, setConfirmedSlot] = useState<SlotOption | null>(null)
    const [message, setMessage] = useState("")

    const linkAction = (action === "reschedule" ? "reschedule" : "book") as LinkAction
    const isReschedule = linkAction === "reschedule"

    // Types are a property of the clinic, not of the filters — fetched once.
    useEffect(() => {
        if (!token) return
        let cancelled = false
        fetchAppointmentTypes(linkAction, token)
            .then((t) => !cancelled && setTypes(t))
            .catch(() => {
                /* The picker simply stays on "any"; it is not worth an error
                   screen when the patient can still book. */
            })
        return () => {
            cancelled = true
        }
    }, [linkAction, token])

    useEffect(() => {
        let cancelled = false
        if (!token) {
            setMessage("This link isn't valid. Please contact the clinic directly.")
            setPhase("error")
            return
        }
        // Re-running for a filter change must not blank the page — the patient
        // keeps the times they are looking at until the new ones arrive.
        setRefreshing(true)
        fetchSlots(linkAction, token, {
            appointmentTypeId: typeId ?? undefined,
            days: rangeDays,
        })
            .then((result) => {
                if (cancelled) return
                setData(result)
                setChosen(null)
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
            .finally(() => !cancelled && setRefreshing(false))
        return () => {
            cancelled = true
        }
    }, [linkAction, token, typeId, rangeDays])

    const days = useMemo(
        () => byDay(data?.slots ?? []),
        [data],
    )

    async function confirm() {
        if (!chosen) return
        setPhase("booking")
        try {
            const result = await bookSlot(linkAction, token, chosen.start, typeId ?? undefined)
            setConfirmedSlot(chosen)
            setOutcome(result.status === "pending" ? "pending" : "booked")
            setPhase("done")
        } catch (err: unknown) {
            const response = (err as {
                response?: { status?: number; data?: { error?: string; slots?: SlotOption[] } }
            })?.response
            const status = response?.status
            const reason = response?.data?.error

            if (status === 409 && reason === "handoff") {
                // Not a race — there is no record in the practice software to
                // book against. Telling them the time was taken would have them
                // trying slot after slot forever. The clinic has been given a
                // handoff, so a person will pick this up.
                setOutcome("pending")
                setPhase("done")
                return
            }

            if (status === 409) {
                // Someone took it while they were deciding — most likely the
                // clinic booking it over the phone, which goes through the same
                // path. Re-offer rather than dead-end: the whole point is that
                // they finish. The refusal carries the refreshed list, so this
                // costs no second request and no second wait.
                const fresh = response?.data?.slots
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
    const chosenTypeName = types.find((t) => t.id === typeId)?.name ?? null

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

                        {phase === "booking" && (
                            <div className="py-6 text-center space-y-3" aria-busy="true">
                                <div className="h-8 w-8 mx-auto rounded-full border-2 border-muted border-t-primary animate-spin" />
                                <p className="text-sm text-muted-foreground" role="status">
                                    Booking {chosen ? timeLabel(chosen.start) : "your appointment"}…
                                </p>
                                <p className="text-xs text-muted-foreground">
                                    This can take a few seconds. Please don't close this page.
                                </p>
                            </div>
                        )}

                        {phase === "choosing" && (
                            <>
                                {/* Filters first: the type changes how long the
                                    appointment needs to be, so it changes which
                                    times are even offerable. */}
                                <div className="space-y-2">
                                    <AppointmentTypePicker
                                        types={types}
                                        value={typeId}
                                        onChange={setTypeId}
                                    />
                                    <div className="flex gap-2">
                                        {RANGE_OPTIONS.map((opt) => (
                                            <Button
                                                key={opt.days}
                                                type="button"
                                                size="sm"
                                                variant={rangeDays === opt.days ? "default" : "outline"}
                                                aria-pressed={rangeDays === opt.days}
                                                className="flex-1 h-9"
                                                onClick={() => setRangeDays(opt.days)}
                                            >
                                                {opt.label}
                                            </Button>
                                        ))}
                                    </div>
                                </div>

                                {message && (
                                    <p className="text-sm text-destructive" role="status">
                                        {message}
                                    </p>
                                )}

                                {refreshing && (
                                    <p className="text-sm text-muted-foreground" role="status">
                                        Finding times…
                                    </p>
                                )}

                                {!refreshing && days.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">
                                        No times available for that choice. Try a longer date range
                                        or a different appointment type — or contact {clinic} and
                                        they'll find one for you.
                                    </p>
                                ) : (
                                    days.map(([day, slots]) => (
                                        <div key={dayLabel(day)} className="space-y-2">
                                            <h2 className="text-sm font-medium text-muted-foreground">
                                                {dayLabel(day)}
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
                                                            {timeLabel(slot.start)}
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
                            <div className="space-y-4">
                                {confirmedSlot && (
                                    <div className="rounded-md border bg-muted/40 p-4 space-y-1">
                                        <p className="font-medium">
                                            {dayLabel(wallClock(confirmedSlot.start).date)}
                                        </p>
                                        <p className="text-2xl font-semibold tracking-tight">
                                            {timeLabel(confirmedSlot.start)}
                                        </p>
                                        {confirmedSlot.provider_name && (
                                            <p className="text-sm text-muted-foreground">
                                                with {confirmedSlot.provider_name}
                                            </p>
                                        )}
                                        {chosenTypeName && (
                                            <p className="text-sm text-muted-foreground">
                                                {chosenTypeName}
                                            </p>
                                        )}
                                        <p className="text-sm text-muted-foreground pt-1">{clinic}</p>
                                    </div>
                                )}
                                <p className="text-sm text-muted-foreground">
                                    {outcome === "pending"
                                        ? `We're confirming this with ${clinic} and will let you know shortly.`
                                        : "Your appointment is confirmed. See you then."}
                                </p>
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* The confirm step is pinned so it is reachable without scrolling
                    back up a long list of times on a phone. */}
                {phase === "choosing" && chosen && (
                    <div className="sticky bottom-4 mt-4">
                        <Button className="w-full h-12 shadow-lg" onClick={confirm}>
                            Confirm {timeLabel(chosen.start)}
                            {chosen.provider_name ? ` with ${chosen.provider_name}` : ""}
                        </Button>
                    </div>
                )}
            </div>
        </div>
    )
}
