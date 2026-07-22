import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

// One consistent page heading for every page: an icon (matching the sidebar
// nav), the title, an optional description, and a right-aligned actions slot.
// Replaces the ad-hoc mix of h1/h2, text-2xl/3xl, and with/without-icon headings.
export function PageHeader({
    icon: Icon,
    title,
    description,
    actions,
    className,
}: {
    icon?: LucideIcon
    title: ReactNode
    description?: ReactNode
    actions?: ReactNode
    className?: string
}) {
    return (
        <div className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
            <div className="flex items-start gap-3">
                {Icon && (
                    <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-neutral-900 text-white ring-1 ring-black/5 dark:bg-neutral-800 dark:ring-white/10">
                        <Icon className="h-5 w-5" strokeWidth={1.75} />
                    </span>
                )}
                <div className="space-y-1">
                    <h1 className="text-2xl font-bold tracking-tight text-balance sm:text-3xl">{title}</h1>
                    {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
                </div>
            </div>
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
    )
}
