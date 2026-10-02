import * as React from "react"
import { Eye, EyeOff } from "lucide-react"

import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/**
 * A password / secret field with a show-hide toggle.
 *
 * Every prop (including the id and aria attributes FormControl injects) goes to
 * the real <input>, so labels, validation messages and react-hook-form's ref
 * keep working exactly as they do on <Input type="password">. The toggle is a
 * plain button (type="button", so it never submits the form) and only reveals
 * what the user typed: stored secrets are never sent to the browser.
 */
const PasswordInput = React.forwardRef<
    HTMLInputElement,
    Omit<React.ComponentProps<"input">, "type">
>(({ className, disabled, ...props }, ref) => {
    const [visible, setVisible] = React.useState(false)
    const Icon = visible ? EyeOff : Eye
    const label = visible ? "Hide password" : "Show password"

    return (
        <div className="relative">
            <Input
                ref={ref}
                type={visible ? "text" : "password"}
                disabled={disabled}
                className={cn("pr-10", className)}
                {...props}
            />
            <button
                type="button"
                onClick={() => setVisible((v) => !v)}
                disabled={disabled}
                aria-label={label}
                aria-pressed={visible}
                title={label}
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-md text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
                <Icon className="h-4 w-4" aria-hidden="true" />
            </button>
        </div>
    )
})
PasswordInput.displayName = "PasswordInput"

export { PasswordInput }
