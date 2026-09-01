import { useEffect, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    campaignLinkGoneMessage,
    fetchIdentityContext,
    identifyPatient,
    type IdentityContext,
} from "@/lib/booking-link-api"

/**
 * The step in front of an action that discloses or destroys something.
 *
 * What the clinic knows when someone follows a campaign link is only how it
 * reached them — a number if it went by text, an address if by email. Who is
 * actually holding the phone is unproven, and a household shares a number.
 *
 * So the page asks them to say whether they are new or existing rather than
 * guessing, then asks an existing patient for the same factors the phone agent
 * asks for.
 *
 * Nothing is prefilled. We could populate the phone number from the contact the
 * campaign targeted and it would be friendlier, but then the only unproven
 * thing they supply is a date of birth and the check quietly becomes
 * single-factor. They know their own number; typing it is what makes it
 * evidence.
 *
 * There is no search-as-you-type either — a Next button, one search, one
 * answer. Every failure reads the same, because differences between "no such
 * patient", "several matched" and "wrong date of birth" are exactly what would
 * let someone test guesses.
 */
type Phase = "loading" | "choose" | "form" | "checking" | "locked" | "error"

const DOB_RE = /^\d{4}-\d{2}-\d{2}$/

export default function IdentifyPatient() {
    const [params] = useSearchParams()
    const navigate = useNavigate()
    const token = params.get("token") ?? ""
    const next = params.get("next") ?? ""

    const [phase, setPhase] = useState<Phase>(token ? "loading" : "error")
    const [ctx, setCtx] = useState<IdentityContext | null>(null)
    const [message, setMessage] = useState(
        token ? "" : "This link isn't valid. Please contact the clinic directly.",
    )

    const [fullName, setFullName] = useState("")
    const [dob, setDob] = useState("")
    const [phone, setPhone] = useState("")
    const [email, setEmail] = useState("")
    const [invalid, setInvalid] = useState("")

    useEffect(() => {
        if (!token) return
        let cancelled = false
        fetchIdentityContext(token)
            .then((c) => {
                if (cancelled) return
                setCtx(c)
                // Already through the gate on this run — don't ask twice.
                if (c.verified && next) navigate(`${next}?token=${token}`, { replace: true })
                else setPhase(c.verified ? "choose" : "choose")
            })
            .catch((err) => {
                if (cancelled) return
                setMessage(
                    campaignLinkGoneMessage(err)
                        ?? "This link isn't valid. Please contact the clinic directly.",
                )
                setPhase("error")
            })
        return () => {
            cancelled = true
        }
    }, [token, next, navigate])

    async function submit() {
        if (!fullName.trim()) {
            setInvalid("Please enter your full name.")
            return
        }
        if (!DOB_RE.test(dob)) {
            setInvalid("Please enter your date of birth.")
            return
        }
        if (!phone.trim() && !email.trim()) {
            setInvalid("Please enter the phone number or email address the clinic has for you.")
            return
        }
        setInvalid("")
        setPhase("checking")
        try {
            const outcome = await identifyPatient(token, {
                full_name: fullName.trim(),
                date_of_birth: dob,
                phone: phone.trim() || undefined,
                email: email.trim() || undefined,
            })
            if (outcome.status === "verified") {
                navigate(next ? `${next}?token=${token}` : `/book/book?token=${token}`, {
                    replace: true,
                })
                return
            }
            if (outcome.status === "locked") {
                setMessage(outcome.message ?? "")
                setPhase("locked")
                return
            }
            setInvalid(outcome.message ?? "")
            setPhase("form")
        } catch {
            setMessage("Sorry — something went wrong. Please contact the clinic.")
            setPhase("error")
        }
    }

    const clinic = ctx?.clinic_name || "the clinic"
    const busy = phase === "checking"

    return (
        <div className="min-h-screen bg-muted/30 px-4 py-8 flex justify-center">
            <div className="w-full max-w-md">
                <Card>
                    <CardHeader>
                        <CardTitle className="text-xl">
                            {phase === "locked" ? "Let's get you some help" : `Welcome to ${clinic}`}
                        </CardTitle>
                        {phase === "choose" && (
                            <CardDescription>
                                Have you been seen here before?
                            </CardDescription>
                        )}
                        {phase === "form" && (
                            <CardDescription>
                                Just to make sure we bring up the right record.
                            </CardDescription>
                        )}
                    </CardHeader>

                    <CardContent className="space-y-4">
                        {phase === "loading" && (
                            <div className="h-10 rounded-md bg-muted animate-pulse" aria-busy="true" />
                        )}

                        {(phase === "error" || phase === "locked") && (
                            <p className="text-sm text-muted-foreground">{message}</p>
                        )}

                        {phase === "choose" && (
                            <div className="space-y-2">
                                <Button
                                    className="w-full"
                                    onClick={() => setPhase("form")}
                                >
                                    I'm an existing patient
                                </Button>
                                <Button
                                    className="w-full"
                                    variant="outline"
                                    onClick={() =>
                                        navigate(`/book/register?token=${token}`)
                                    }
                                >
                                    I'm a new patient
                                </Button>
                            </div>
                        )}

                        {(phase === "form" || busy) && (
                            <div className="space-y-4">
                                <div className="space-y-1.5">
                                    <Label htmlFor="full-name">Full name</Label>
                                    <Input
                                        id="full-name"
                                        value={fullName}
                                        autoComplete="name"
                                        disabled={busy}
                                        onChange={(e) => setFullName(e.target.value)}
                                    />
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="dob">Date of birth</Label>
                                    <Input
                                        id="dob"
                                        type="date"
                                        value={dob}
                                        autoComplete="bday"
                                        disabled={busy}
                                        onChange={(e) => setDob(e.target.value)}
                                    />
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="phone">
                                        {ctx?.arrived_by === "email"
                                            ? "Phone number (if we have one for you)"
                                            : "Phone number"}
                                    </Label>
                                    <Input
                                        id="phone"
                                        type="tel"
                                        value={phone}
                                        autoComplete="tel"
                                        disabled={busy}
                                        onChange={(e) => setPhone(e.target.value)}
                                    />
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="email">Email (optional)</Label>
                                    <Input
                                        id="email"
                                        type="email"
                                        value={email}
                                        autoComplete="email"
                                        disabled={busy}
                                        onChange={(e) => setEmail(e.target.value)}
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Adding it helps us find the right record.
                                    </p>
                                </div>

                                {invalid && (
                                    <p className="text-sm text-destructive" role="alert">
                                        {invalid}
                                    </p>
                                )}

                                <Button className="w-full" onClick={submit} disabled={busy}>
                                    {busy ? "Checking…" : "Next"}
                                </Button>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}
