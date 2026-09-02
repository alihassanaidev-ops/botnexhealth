import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { completeOAuth } from "@/lib/form-integrations-api"

/**
 * Where Meta and Typeform send the practice back after they authorise.
 *
 * The page does nothing but hand the code and the signed state to the server,
 * which is what verifies them — the state travelled through a browser and is
 * attacker-controlled by construction, so nothing here reads it as truth.
 *
 * The exchange is guarded against running twice. React's development strict
 * mode mounts effects twice, and an authorisation code is single-use: a second
 * attempt fails at the provider and shows a connection error for a connection
 * that in fact succeeded.
 */
export default function FormIntegrationsCallback() {
    const [params] = useSearchParams()
    const navigate = useNavigate()
    const attempted = useRef(false)
    const [error, setError] = useState("")
    const [status, setStatus] = useState<"working" | "done" | "failed">("working")

    useEffect(() => {
        if (attempted.current) return
        attempted.current = true

        const code = params.get("code")
        const state = params.get("state")
        // The provider says why it refused; passing it through beats "failed".
        const denied = params.get("error_description") || params.get("error")

        if (denied) {
            setError(denied)
            setStatus("failed")
            return
        }
        if (!code || !state) {
            setError("That link is missing part of the authorisation.")
            setStatus("failed")
            return
        }

        completeOAuth(code, state)
            .then(() => {
                setStatus("done")
                navigate("/institution-admin/lead-forms", { replace: true })
            })
            .catch((err: unknown) => {
                const detail = (err as { response?: { data?: { detail?: unknown } } })
                    ?.response?.data?.detail
                setError(
                    typeof detail === "string" && detail
                        ? detail
                        : "We couldn't complete that connection.",
                )
                setStatus("failed")
            })
    }, [params, navigate])

    return (
        <div className="space-y-6">
            <PageHeader
                title="Connecting your account"
                description="Finishing the authorisation you just approved."
            />
            <Card>
                <CardContent className="space-y-3 pt-6">
                    {status === "working" && (
                        <p className="text-sm text-muted-foreground">Finishing up…</p>
                    )}
                    {status === "failed" && (
                        <>
                            <p className="text-sm text-destructive">{error}</p>
                            <Button
                                size="sm"
                                onClick={() => navigate("/institution-admin/lead-forms")}
                            >
                                Back to lead forms
                            </Button>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}
