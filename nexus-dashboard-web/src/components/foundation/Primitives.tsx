import {
    Children,
    cloneElement,
    Fragment,
    forwardRef,
    isValidElement,
    type ButtonHTMLAttributes,
    type ComponentPropsWithoutRef,
    type HTMLAttributes,
    type InputHTMLAttributes,
    type ReactElement,
    type ReactNode,
    type TextareaHTMLAttributes,
} from "react"

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "./compat/select"

import "./primitives.css"
import "./select.css"

type UiButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
    asChild?: boolean
    variant?: "primary" | "secondary" | "quiet" | "danger"
    size?: "sm" | "md" | "icon"
}

export const UiButton = forwardRef<HTMLButtonElement, UiButtonProps>(function UiButton(
    { asChild = false, variant = "secondary", size = "md", className = "", children, ...props },
    ref,
) {
    const classes = `ui-button ui-button-${variant} ui-button-${size} ${className}`.trim()
    if (asChild && isValidElement(children)) {
        const child = Children.only(children) as ReactElement<{ className?: string }>
        return cloneElement(child, { ...props, className: `${classes} ${child.props.className ?? ""}`.trim() } as never)
    }
    return (
        <button
            ref={ref}
            className={classes}
            {...props}
        >{children}</button>
    )
})

export const UiInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
    function UiInput({ className = "", ...props }, ref) {
        return <input ref={ref} className={`ui-input ${className}`.trim()} {...props} />
    },
)

type UiSelectOption = { value: string; label: ReactNode; disabled?: boolean }

// Call sites keep writing plain <option> children; we lift them out of the
// tree and render our own popup, so no dropdown in the app falls back to the
// OS menu (which ignores every token in the design system).
function collectOptions(children: ReactNode, out: UiSelectOption[] = []): UiSelectOption[] {
    Children.toArray(children).forEach((child) => {
        if (!isValidElement(child)) return
        const props = child.props as { value?: string | number; children?: ReactNode; disabled?: boolean }
        if (child.type === Fragment) {
            collectOptions(props.children, out)
            return
        }
        if (child.type !== "option") return
        out.push({ value: String(props.value ?? ""), label: props.children, disabled: props.disabled })
    })
    return out
}

type UiSelectProps = Omit<
    ComponentPropsWithoutRef<typeof SelectTrigger>,
    "onChange" | "value" | "defaultValue" | "children"
> & {
    value?: string
    defaultValue?: string
    onChange?: (event: { target: { value: string } }) => void
    children?: ReactNode
    placeholder?: ReactNode
    /** Matches the UiButton scale so controls sharing a row line up. */
    uiSize?: "sm" | "md"
}

export function UiSelect({
    value,
    defaultValue,
    onChange,
    children,
    className = "",
    disabled,
    placeholder,
    uiSize = "md",
    ...triggerProps
}: UiSelectProps) {
    const options = collectOptions(children)
    // A blank <option> is a placeholder, not a choice — and Radix reserves "".
    const blank = options.find((option) => option.value === "")

    return (
        <Select
            value={value === "" ? undefined : value}
            defaultValue={defaultValue}
            onValueChange={(next) => onChange?.({ target: { value: next } })}
            disabled={disabled}
        >
            <SelectTrigger className={`ui-select-control-${uiSize} ${className}`.trim()} {...triggerProps}>
                <SelectValue placeholder={placeholder ?? blank?.label} />
            </SelectTrigger>
            <SelectContent>
                {options
                    .filter((option) => option.value !== "")
                    .map((option) => (
                        <SelectItem key={option.value} value={option.value} disabled={option.disabled}>
                            {option.label}
                        </SelectItem>
                    ))}
            </SelectContent>
        </Select>
    )
}

export const UiTextarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
    function UiTextarea({ className = "", ...props }, ref) {
        return <textarea ref={ref} className={`ui-textarea ${className}`.trim()} {...props} />
    },
)

export function UiBadge({
    tone = "neutral",
    className = "",
    ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: "neutral" | "primary" | "danger" | "success" }) {
    return <span className={`ui-badge ui-badge-${tone} ${className}`.trim()} {...props} />
}

export function UiSkeleton({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
    return <div className={`ui-skeleton ${className}`.trim()} aria-hidden="true" {...props} />
}

export function UiSurface({ className = "", ...props }: HTMLAttributes<HTMLElement>) {
    return <section className={`ui-surface ${className}`.trim()} {...props} />
}
