import {
    Children,
    cloneElement,
    createContext,
    forwardRef,
    isValidElement,
    useContext,
    useState,
    type ButtonHTMLAttributes,
    type ComponentProps,
    type HTMLAttributes,
    type InputHTMLAttributes,
    type LabelHTMLAttributes,
    type ReactElement,
    type ReactNode,
    type TextareaHTMLAttributes,
} from "react"
import { Check } from "lucide-react"

import { cn } from "@/lib/utils"
import { UiSkeleton } from "./Primitives"
import "./compat.css"

type ButtonVariant = "default" | "destructive" | "outline" | "secondary" | "ghost" | "link"
type ButtonSize = "default" | "sm" | "lg" | "icon" | "icon-sm"

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
    asChild?: boolean
    variant?: ButtonVariant
    size?: ButtonSize
}

export function buttonVariants({
    variant = "default",
    size = "default",
    className,
}: {
    variant?: ButtonVariant
    size?: ButtonSize
    className?: string
} = {}) {
    return cn("compat-button", `compat-button-${variant}`, `compat-button-${size}`, className)
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
    { asChild = false, variant = "default", size = "default", className, children, ...props },
    ref,
) {
    const classes = buttonVariants({ variant, size, className })
    if (asChild && isValidElement(children)) {
        const child = Children.only(children) as ReactElement<{ className?: string }>
        return cloneElement(child, { ...props, className: cn(classes, child.props.className) } as never)
    }
    return <button ref={ref} className={classes} {...props}>{children}</button>
})

export function Card({ className, ...props }: ComponentProps<"section">) {
    return <section className={cn("compat-card", className)} {...props} />
}
export function CardHeader({ className, ...props }: ComponentProps<"div">) {
    return <div className={cn("compat-card-header", className)} {...props} />
}
export function CardTitle({ className, ...props }: ComponentProps<"h3">) {
    return <h3 className={cn("compat-card-title", className)} {...props} />
}
export function CardDescription({ className, ...props }: ComponentProps<"p">) {
    return <p className={cn("compat-card-description", className)} {...props} />
}
export function CardContent({ className, ...props }: ComponentProps<"div">) {
    return <div className={cn("compat-card-content", className)} {...props} />
}
export function CardFooter({ className, ...props }: ComponentProps<"div">) {
    return <div className={cn("compat-card-footer", className)} {...props} />
}

export function Badge({
    variant = "default",
    className,
    ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: "default" | "secondary" | "destructive" | "outline" }) {
    return <span className={cn("compat-badge", `compat-badge-${variant}`, className)} {...props} />
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
    { className, ...props },
    ref,
) {
    return <input ref={ref} className={cn("compat-input", className)} {...props} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
    { className, ...props },
    ref,
) {
    return <textarea ref={ref} className={cn("compat-textarea", className)} {...props} />
})

export const Label = forwardRef<HTMLLabelElement, LabelHTMLAttributes<HTMLLabelElement>>(function Label(
    { className, ...props },
    ref,
) {
    return <label ref={ref} className={cn("compat-label", className)} {...props} />
})

export const Skeleton = UiSkeleton

export function Alert({ className, variant = "default", ...props }: ComponentProps<"div"> & { variant?: "default" | "destructive" }) {
    return <div role="alert" className={cn("compat-alert", variant === "destructive" && "compat-alert-destructive", className)} {...props} />
}
export function AlertTitle({ className, ...props }: ComponentProps<"h5">) {
    return <h5 className={cn("compat-alert-title", className)} {...props} />
}
export function AlertDescription({ className, ...props }: ComponentProps<"div">) {
    return <div className={cn("compat-alert-description", className)} {...props} />
}

export const Checkbox = forwardRef<HTMLButtonElement, Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> & {
    checked?: boolean | "indeterminate"
    onCheckedChange?: (checked: boolean) => void
}>(function Checkbox({ checked = false, onCheckedChange, className, disabled, ...props }, ref) {
    const active = checked === true
    return (
        <button
            ref={ref}
            type="button"
            role="checkbox"
            aria-checked={checked === "indeterminate" ? "mixed" : checked}
            disabled={disabled}
            className={cn("compat-checkbox", active && "is-checked", className)}
            onClick={() => onCheckedChange?.(!active)}
            {...props}
        >
            {active && <Check aria-hidden="true" />}
        </button>
    )
})

export const Switch = forwardRef<HTMLButtonElement, Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> & {
    checked?: boolean
    onCheckedChange?: (checked: boolean) => void
}>(function Switch({ checked = false, onCheckedChange, className, disabled, ...props }, ref) {
    return (
        <button
            ref={ref}
            type="button"
            role="switch"
            aria-checked={checked}
            disabled={disabled}
            className={cn("compat-switch", checked && "is-checked", className)}
            onClick={() => onCheckedChange?.(!checked)}
            {...props}
        >
            <span />
        </button>
    )
})

export function Progress({ value = 0, className, ...props }: HTMLAttributes<HTMLDivElement> & { value?: number }) {
    return (
        <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} className={cn("compat-progress", className)} {...props}>
            <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
        </div>
    )
}

export function Separator({ orientation = "horizontal", decorative = true, className, ...props }: HTMLAttributes<HTMLDivElement> & {
    orientation?: "horizontal" | "vertical"
    decorative?: boolean
}) {
    return <div role={decorative ? "none" : "separator"} aria-orientation={orientation} className={cn("compat-separator", `compat-separator-${orientation}`, className)} {...props} />
}

export function ScrollArea({ className, ...props }: ComponentProps<"div">) {
    return <div className={cn("compat-scroll-area", className)} {...props} />
}

type TabsContextValue = { value: string; setValue: (value: string) => void }
const TabsContext = createContext<TabsContextValue | null>(null)

export function Tabs({ value, defaultValue = "", onValueChange, className, children, ...props }: ComponentProps<"div"> & {
    value?: string
    defaultValue?: string
    onValueChange?: (value: string) => void
}) {
    const [internal, setInternal] = useState(defaultValue)
    const current = value ?? internal
    const setValue = (next: string) => {
        if (value === undefined) setInternal(next)
        onValueChange?.(next)
    }
    return <TabsContext.Provider value={{ value: current, setValue }}><div className={cn("compat-tabs", className)} {...props}>{children}</div></TabsContext.Provider>
}
export function TabsList({ className, ...props }: ComponentProps<"div">) {
    return <div role="tablist" className={cn("compat-tabs-list", className)} {...props} />
}
export function TabsTrigger({ value, className, ...props }: ComponentProps<"button"> & { value: string }) {
    const tabs = useContext(TabsContext)
    const active = tabs?.value === value
    return <button type="button" role="tab" aria-selected={active} className={cn("compat-tabs-trigger", active && "is-active", className)} onClick={() => tabs?.setValue(value)} {...props} />
}
export function TabsContent({ value, className, ...props }: ComponentProps<"div"> & { value: string }) {
    const tabs = useContext(TabsContext)
    if (tabs?.value !== value) return null
    return <div role="tabpanel" className={cn("compat-tabs-content", className)} {...props} />
}

type TooltipContextValue = { open: boolean; setOpen: (open: boolean) => void }
const TooltipContext = createContext<TooltipContextValue | null>(null)
export function TooltipProvider({ children }: { children: ReactNode; delayDuration?: number }) { return <>{children}</> }
export function Tooltip({ children }: { children: ReactNode }) {
    const [open, setOpen] = useState(false)
    return <TooltipContext.Provider value={{ open, setOpen }}><span className="compat-tooltip">{children}</span></TooltipContext.Provider>
}
export function TooltipTrigger({ asChild = false, children, ...props }: ComponentProps<"button"> & { asChild?: boolean }) {
    const tip = useContext(TooltipContext)
    const handlers = { onMouseEnter: () => tip?.setOpen(true), onMouseLeave: () => tip?.setOpen(false), onFocus: () => tip?.setOpen(true), onBlur: () => tip?.setOpen(false) }
    if (asChild && isValidElement(children)) return cloneElement(Children.only(children) as ReactElement, { ...props, ...handlers } as never)
    return <button type="button" {...props} {...handlers}>{children}</button>
}
export function TooltipContent({ className, sideOffset: _sideOffset, side: _side, align: _align, ...props }: ComponentProps<"span"> & { sideOffset?: number; side?: "top" | "right" | "bottom" | "left"; align?: "start" | "center" | "end" }) {
    const tip = useContext(TooltipContext)
    if (!tip?.open) return null
    return <span role="tooltip" className={cn("compat-tooltip-content", className)} {...props} />
}
