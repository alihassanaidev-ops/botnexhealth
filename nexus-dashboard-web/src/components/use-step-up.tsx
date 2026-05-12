/**
 * `useStepUp` — the hook side of the step-up flow.
 *
 * Separated from <StepUpDialog /> in step-up-dialog.tsx because Fast
 * Refresh requires component files to export only components. The hook
 * owns the promise dance (resolve when the dialog closes) and returns
 * both the imperative `stepUp({...})` function and the `dialog`
 * element callers render once.
 *
 * Typical usage:
 *
 *     const { stepUp, dialog } = useStepUp()
 *     // ...
 *     async function handleRemovePasskey(p) {
 *         const ticket = await stepUp({ title, description })
 *         if (!ticket) return
 *         await removePasskey(p.id, ticket)
 *     }
 *     return <>{...}{dialog}</>
 */

import { useCallback, useState } from "react"
import { toast } from "sonner"
import axios from "axios"

import { StepUpDialog } from "@/components/step-up-dialog"
import { startStepUp, type StepUpChallenge } from "@/lib/security-api"

interface DialogState {
    open: boolean
    title: string
    description: string
    challenge: StepUpChallenge | null
    resolve: ((ticket: string | null) => void) | null
}

function getDetail(error: unknown, fallback: string): string {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (typeof detail === "string" && detail.trim()) return detail
    }
    if (error instanceof Error && error.message) return error.message
    return fallback
}

export function useStepUp() {
    const [state, setState] = useState<DialogState>({
        open: false,
        title: "",
        description: "",
        challenge: null,
        resolve: null,
    })

    const stepUp = useCallback(
        async ({
            title,
            description,
        }: { title: string; description: string }): Promise<string | null> => {
            try {
                const challenge = await startStepUp()
                return await new Promise<string | null>((resolve) => {
                    setState({ open: true, title, description, challenge, resolve })
                })
            } catch (err) {
                toast.error(getDetail(err, "Couldn't start MFA verification"))
                return null
            }
        },
        [],
    )

    const close = useCallback((ticket: string | null) => {
        setState((s) => {
            s.resolve?.(ticket)
            return { open: false, title: "", description: "", challenge: null, resolve: null }
        })
    }, [])

    return {
        stepUp,
        dialog: (
            <StepUpDialog
                open={state.open}
                title={state.title}
                description={state.description}
                challenge={state.challenge}
                onClose={close}
            />
        ),
    }
}
