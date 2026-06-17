import { useRouteError, useNavigate, isRouteErrorResponse } from "react-router-dom"
import { AlertTriangle, RefreshCcw, Home } from "lucide-react"
import { Button } from "@/components/ui/button"

/**
 * Friendly fallback shown by React Router's `errorElement` when a route (or any
 * descendant) throws during render. Replaces the raw "Unexpected Application
 * Error!" dev screen with something a user can act on.
 */
export default function RouteError() {
    const error = useRouteError()
    const navigate = useNavigate()

    const detail = isRouteErrorResponse(error)
        ? `${error.status} ${error.statusText}`
        : error instanceof Error
            ? error.message
            : "Unknown error"

    return (
        <div className="flex min-h-[70vh] flex-col items-center justify-center gap-5 p-8 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-destructive/10">
                <AlertTriangle className="h-8 w-8 text-destructive" />
            </div>
            <div className="space-y-1.5">
                <h2 className="text-2xl font-semibold tracking-tight">Something went wrong</h2>
                <p className="max-w-md text-sm text-muted-foreground">
                    This page hit an unexpected error. Reloading usually fixes it — if it
                    keeps happening, let the team know.
                </p>
            </div>

            {import.meta.env.DEV && (
                <pre className="max-w-lg overflow-auto rounded-md border border-border bg-muted px-3 py-2 text-left text-xs text-muted-foreground">
                    {detail}
                </pre>
            )}

            <div className="flex gap-2">
                <Button variant="outline" onClick={() => navigate(0)} className="gap-1.5">
                    <RefreshCcw className="h-4 w-4" /> Reload
                </Button>
                <Button onClick={() => navigate("/")} className="gap-1.5">
                    <Home className="h-4 w-4" /> Back to home
                </Button>
            </div>
        </div>
    )
}
