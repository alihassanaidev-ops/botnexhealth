import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { Controller, FormProvider, useFormContext, type ControllerProps, type FieldPath, type FieldValues } from "react-hook-form"

import { cn } from "@/lib/utils"
import { Label } from "./label"
import "../compat.css"

export const Form = FormProvider
type FieldContextValue<TValues extends FieldValues = FieldValues, TName extends FieldPath<TValues> = FieldPath<TValues>> = { name: TName }
const FieldContext = React.createContext<FieldContextValue | null>(null)
const ItemContext = React.createContext<{ id: string } | null>(null)
export function FormField<TValues extends FieldValues = FieldValues, TName extends FieldPath<TValues> = FieldPath<TValues>>(props: ControllerProps<TValues, TName>) { return <FieldContext.Provider value={{ name: props.name }}><Controller {...props} /></FieldContext.Provider> }
export function useFormField() {
    const field = React.useContext(FieldContext)
    const item = React.useContext(ItemContext)
    const { getFieldState, formState } = useFormContext()
    if (!field || !item) throw new Error("Form controls must be used inside FormField and FormItem")
    const state = getFieldState(field.name, formState)
    return { id: item.id, name: field.name, formItemId: `${item.id}-form-item`, formDescriptionId: `${item.id}-form-item-description`, formMessageId: `${item.id}-form-item-message`, ...state }
}
export const FormItem = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(function FormItem({ className, ...props }, ref) { const id = React.useId(); return <ItemContext.Provider value={{ id }}><div ref={ref} className={cn("compat-form-item", className)} {...props} /></ItemContext.Provider> })
export const FormLabel = React.forwardRef<HTMLLabelElement, React.ComponentPropsWithoutRef<typeof Label>>(function FormLabel({ className, ...props }, ref) { const { error, formItemId } = useFormField(); return <Label ref={ref} htmlFor={formItemId} className={cn(error && "text-destructive", className)} {...props} /> })
export const FormControl = React.forwardRef<React.ElementRef<typeof Slot>, React.ComponentPropsWithoutRef<typeof Slot>>(function FormControl(props, ref) { const { error, formItemId, formDescriptionId, formMessageId } = useFormField(); return <Slot ref={ref} id={formItemId} aria-describedby={error ? `${formDescriptionId} ${formMessageId}` : formDescriptionId} aria-invalid={!!error} {...props} /> })
export const FormDescription = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(function FormDescription({ className, ...props }, ref) { const { formDescriptionId } = useFormField(); return <p ref={ref} id={formDescriptionId} className={cn("compat-form-description", className)} {...props} /> })
export const FormMessage = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(function FormMessage({ className, children, ...props }, ref) { const { error, formMessageId } = useFormField(); const body = error ? String(error.message ?? "") : children; if (!body) return null; return <p ref={ref} id={formMessageId} className={cn("compat-form-message", className)} {...props}>{body}</p> })
