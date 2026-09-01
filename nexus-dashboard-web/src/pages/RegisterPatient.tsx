import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    campaignLinkGoneMessage,
    GENDERS,
    fetchRegistrationDetails,
    registerPatient,
    type Gender,
} from "@/lib/booking-link-api"

/**
 * Registering a lead as a patient, from a link in a campaign message.
 *
 * The clinic already knows the name, email and phone it was campaigning to, so
 * those are prefilled and editable — a number typed into a web form beats one a
 * voice agent transcribed, and it is the address the clinic will use from then
 * on. Only date of birth and gender are genuinely missing, because the practice
 * software demands them and nobody but the patient can supply them.
 *
 * Kept to one screen on purpose. This sits between a patient wanting an
 * appointment and being able to book one, so every extra field is somewhere to
 * give up.
 */
type Phase = "loading" | "form" | "saving" | "done" | "error"

const DOB_RE = /^\d{4}-\d{2}-\d{2}$/

export default function RegisterPatient() {
    const [params] = useSearchParams()
    const token = params.get("token") ?? ""

    const [phase, setPhase] = useState<Phase>(token ? "loading" : "error")
    const [clinic, setClinic] = useState("the clinic")
    const [message, setMessage] = useState(
        token ? "" : "This link isn't valid. Please contact the clinic directly.",
    )

    const [firstName, setFirstName] = useState("")
    const [lastName, setLastName] = useState("")
    const [email, setEmail] = useState("")
    const [phone, setPhone] = useState("")
    const [dob, setDob] = useState("")
    const [gender, setGender] = useState<Gender | "">("")
    const [invalid, setInvalid] = useState("")

    useEffect(() => {
        if (!token) return
        let cancelled = false
        fetchRegistrationDetails(token)
            .then((d) => {
                if (cancelled) return
                if (d.clinic_name) setClinic(d.clinic_name)
                setFirstName(d.first_name)
                setLastName(d.last_name)
                setEmail(d.email)
                setPhone(d.phone)
                // Already on file: say so rather than showing a form whose
                // submission would be refused.
                setPhase(d.already_registered ? "done" : "form")
            })
            .catch((err) => {
                if (cancelled) return
                const status = err?.response?.status
                setMessage(
                    campaignLinkGoneMessage(err)
                        ?? (status === 503
                          ? "We can't reach the clinic's system right now. Please try again shortly."
                          : "This link isn't valid. Please contact the clinic directly."),
                )
                setPhase("error")
            })
        return () => {
            cancelled = true
        }
    }, [token])

    async function submit() {
        if (!DOB_RE.test(dob)) {
            setInvalid("Please enter your date of birth.")
            return
        }
        if (!gender) {
            setInvalid("Please choose an option.")
            return
        }
        if (!firstName.trim() || !lastName.trim() || !email.trim() || !phone.trim()) {
            setInvalid("Please fill in your name, email and phone number.")
            return
        }
        setInvalid("")
        setPhase("saving")
        try {
            await registerPatient(token, {
                date_of_birth: dob,
                gender,
                first_name: firstName.trim(),
                last_name: lastName.trim(),
                email: email.trim(),
                phone: phone.trim(),
            })
            setPhase("done")
        } catch (err) {
            const reason = (err as { response?: { data?: { error?: string } } })?.response
                ?.data?.error
            // Never surfaces why the practice software refused: those messages
            // repeat the submitted details back, and this page is public.
            setMessage(
                reason === "could_not_register"
                    ? "We couldn't complete your registration. The clinic will be in touch."
                    : "Sorry — something went wrong. Please contact the clinic.",
            )
            setPhase("error")
        }
    }

    const busy = phase === "saving"

    return (
        <div className="min-h-screen bg-muted/30 px-4 py-8 flex justify-center">
            <div className="w-full max-w-md">
                <Card>
                    <CardHeader>
                        <CardTitle className="text-xl">
                            {phase === "done" ? "You're all set" : "A few details"}
                        </CardTitle>
                        {phase === "form" && (
                            <CardDescription>
                                {clinic} needs these before we can book you in.
                            </CardDescription>
                        )}
                    </CardHeader>

                    <CardContent className="space-y-4">
                        {phase === "loading" && (
                            <div
                                className="h-10 rounded-md bg-muted animate-pulse"
                                aria-busy="true"
                            />
                        )}

                        {phase === "error" && (
                            <p className="text-sm text-muted-foreground">{message}</p>
                        )}

                        {phase === "done" && (
                            <p className="text-sm text-muted-foreground">
                                Thanks — you're registered with {clinic}. You can book an
                                appointment from the link in your message.
                            </p>
                        )}

                        {(phase === "form" || busy) && (
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1.5">
                                        <Label htmlFor="first-name">First name</Label>
                                        <Input
                                            id="first-name"
                                            value={firstName}
                                            autoComplete="given-name"
                                            onChange={(e) => setFirstName(e.target.value)}
                                            disabled={busy}
                                        />
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor="last-name">Last name</Label>
                                        <Input
                                            id="last-name"
                                            value={lastName}
                                            autoComplete="family-name"
                                            onChange={(e) => setLastName(e.target.value)}
                                            disabled={busy}
                                        />
                                    </div>
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="dob">Date of birth</Label>
                                    <Input
                                        id="dob"
                                        type="date"
                                        value={dob}
                                        autoComplete="bday"
                                        onChange={(e) => setDob(e.target.value)}
                                        disabled={busy}
                                    />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>Gender</Label>
                                    <div className="flex gap-2">
                                        {GENDERS.map((g) => (
                                            <Button
                                                key={g}
                                                type="button"
                                                variant={gender === g ? "default" : "outline"}
                                                className="flex-1"
                                                onClick={() => setGender(g)}
                                                disabled={busy}
                                                aria-pressed={gender === g}
                                            >
                                                {g}
                                            </Button>
                                        ))}
                                    </div>
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="email">Email</Label>
                                    <Input
                                        id="email"
                                        type="email"
                                        value={email}
                                        autoComplete="email"
                                        onChange={(e) => setEmail(e.target.value)}
                                        disabled={busy}
                                    />
                                </div>

                                <div className="space-y-1.5">
                                    <Label htmlFor="phone">Phone</Label>
                                    <Input
                                        id="phone"
                                        type="tel"
                                        value={phone}
                                        autoComplete="tel"
                                        onChange={(e) => setPhone(e.target.value)}
                                        disabled={busy}
                                    />
                                </div>

                                {invalid && (
                                    <p className="text-sm text-destructive" role="alert">
                                        {invalid}
                                    </p>
                                )}

                                <Button className="w-full" onClick={submit} disabled={busy}>
                                    {busy ? "Saving…" : "Continue"}
                                </Button>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}
