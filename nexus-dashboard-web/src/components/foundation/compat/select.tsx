import * as React from "react"
import * as SelectPrimitive from "@radix-ui/react-select"
import { Check, ChevronDown, ChevronUp } from "lucide-react"

import { cn } from "@/lib/utils"
import "../compat.css"
import "../select.css"

export const Select = SelectPrimitive.Root
export const SelectGroup = SelectPrimitive.Group
export const SelectValue = SelectPrimitive.Value
export const SelectTrigger = React.forwardRef<React.ElementRef<typeof SelectPrimitive.Trigger>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>>(function SelectTrigger({ className, children, ...props }, ref) {
    return <SelectPrimitive.Trigger ref={ref} className={cn("ui-select-control ui-select-trigger", className)} {...props}>{children}<SelectPrimitive.Icon asChild><ChevronDown /></SelectPrimitive.Icon></SelectPrimitive.Trigger>
})
export const SelectScrollUpButton = React.forwardRef<React.ElementRef<typeof SelectPrimitive.ScrollUpButton>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollUpButton>>(function SelectScrollUpButton({ className, ...props }, ref) { return <SelectPrimitive.ScrollUpButton ref={ref} className={cn("compat-select-scroll", className)} {...props}><ChevronUp /></SelectPrimitive.ScrollUpButton> })
export const SelectScrollDownButton = React.forwardRef<React.ElementRef<typeof SelectPrimitive.ScrollDownButton>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollDownButton>>(function SelectScrollDownButton({ className, ...props }, ref) { return <SelectPrimitive.ScrollDownButton ref={ref} className={cn("compat-select-scroll", className)} {...props}><ChevronDown /></SelectPrimitive.ScrollDownButton> })
export const SelectContent = React.forwardRef<React.ElementRef<typeof SelectPrimitive.Content>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>>(function SelectContent({ className, children, position = "popper", ...props }, ref) {
    return <SelectPrimitive.Portal><SelectPrimitive.Content ref={ref} className={cn("compat-select-content", className)} position={position} {...props}><SelectScrollUpButton /><SelectPrimitive.Viewport className="compat-select-viewport">{children}</SelectPrimitive.Viewport><SelectScrollDownButton /></SelectPrimitive.Content></SelectPrimitive.Portal>
})
export const SelectLabel = React.forwardRef<React.ElementRef<typeof SelectPrimitive.Label>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.Label>>(function SelectLabel({ className, ...props }, ref) { return <SelectPrimitive.Label ref={ref} className={cn("compat-select-label", className)} {...props} /> })
export const SelectItem = React.forwardRef<React.ElementRef<typeof SelectPrimitive.Item>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>>(function SelectItem({ className, children, ...props }, ref) {
    return <SelectPrimitive.Item ref={ref} className={cn("compat-select-item", className)} {...props}><SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText><SelectPrimitive.ItemIndicator><Check /></SelectPrimitive.ItemIndicator></SelectPrimitive.Item>
})
export const SelectSeparator = React.forwardRef<React.ElementRef<typeof SelectPrimitive.Separator>, React.ComponentPropsWithoutRef<typeof SelectPrimitive.Separator>>(function SelectSeparator({ className, ...props }, ref) { return <SelectPrimitive.Separator ref={ref} className={cn("compat-select-separator", className)} {...props} /> })
