import { createContext, useContext, useEffect, type ComponentProps, type ReactNode } from "react"
import { X } from "lucide-react"

import { UiButton } from "./Primitives"
import "./overlay.css"

type DialogContextValue = { open: boolean; setOpen: (open: boolean) => void }

const DialogContext = createContext<DialogContextValue | null>(null)

export function UiDialog({
    open,
    onOpenChange,
    children,
}: {
    open: boolean
    onOpenChange: (open: boolean) => void
    children: ReactNode
}) {
    useEffect(() => {
        if (!open) return
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") onOpenChange(false)
        }
        window.addEventListener("keydown", onKeyDown)
        return () => window.removeEventListener("keydown", onKeyDown)
    }, [open, onOpenChange])

    if (!open) return null
    return (
        <DialogContext.Provider value={{ open, setOpen: onOpenChange }}>
            {children}
        </DialogContext.Provider>
    )
}

export function UiDialogContent({ className = "", children, ...props }: ComponentProps<"div">) {
    const dialog = useContext(DialogContext)
    if (!dialog) return null
    return (
        <div className="ui-dialog-layer" role="presentation">
            <button
                type="button"
                className="ui-dialog-backdrop"
                aria-label="Close"
                onClick={() => dialog.setOpen(false)}
            />
            <div role="dialog" aria-modal="true" className={`ui-dialog-panel ${className}`.trim()} {...props}>
                <UiButton
                    type="button"
                    variant="quiet"
                    size="icon"
                    className="ui-dialog-close"
                    aria-label="Close"
                    onClick={() => dialog.setOpen(false)}
                >
                    <X />
                </UiButton>
                {children}
            </div>
        </div>
    )
}

export function UiDialogHeader({ className = "", ...props }: ComponentProps<"div">) {
    return <div className={`ui-dialog-header ${className}`.trim()} {...props} />
}

export function UiDialogTitle({ className = "", ...props }: ComponentProps<"h2">) {
    return <h2 className={`ui-dialog-title ${className}`.trim()} {...props} />
}

export function UiDialogDescription({ className = "", ...props }: ComponentProps<"p">) {
    return <p className={`ui-dialog-description ${className}`.trim()} {...props} />
}

export function UiDialogFooter({ className = "", ...props }: ComponentProps<"div">) {
    return <div className={`ui-dialog-footer ${className}`.trim()} {...props} />
}
