import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"

import { cn } from "@/lib/utils"
import "../overlay.css"
import "../compat.css"

export const Sheet = DialogPrimitive.Root
export const SheetTrigger = DialogPrimitive.Trigger
export const SheetClose = DialogPrimitive.Close
export const SheetPortal = DialogPrimitive.Portal
export const SheetOverlay = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Overlay>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>>(function SheetOverlay({ className, ...props }, ref) {
    return <DialogPrimitive.Overlay ref={ref} className={cn("ui-dialog-backdrop", className)} {...props} />
})
export const SheetContent = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Content>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { side?: "top" | "right" | "bottom" | "left" }>(function SheetContent({ side = "right", className, children, ...props }, ref) {
    return <SheetPortal><SheetOverlay /><DialogPrimitive.Content ref={ref} className={cn("compat-sheet", `compat-sheet-${side}`, className)} {...props}>{children}<DialogPrimitive.Close className="compat-sheet-close" aria-label="Close"><X /><span className="sr-only">Close</span></DialogPrimitive.Close></DialogPrimitive.Content></SheetPortal>
})
export function SheetHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) { return <div className={cn("compat-sheet-header", className)} {...props} /> }
export function SheetFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) { return <div className={cn("compat-sheet-footer", className)} {...props} /> }
export const SheetTitle = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Title>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(function SheetTitle({ className, ...props }, ref) { return <DialogPrimitive.Title ref={ref} className={cn("compat-sheet-title", className)} {...props} /> })
export const SheetDescription = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Description>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>>(function SheetDescription({ className, ...props }, ref) { return <DialogPrimitive.Description ref={ref} className={cn("compat-sheet-description", className)} {...props} /> })
