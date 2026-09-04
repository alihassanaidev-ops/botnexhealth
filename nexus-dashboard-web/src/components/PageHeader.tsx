import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { type PageArtName } from "@/assets/icons"
import { Art } from "@/components/Art"
import { cn } from "@/lib/utils"

// One consistent page heading for every page: an icon, the title, an optional
// description, and a right-aligned actions slot. Replaces the ad-hoc mix of
// h1/h2, text-2xl/3xl, and with/without-icon headings.
//
// Two icon styles are supported. `art` is an illustrated page icon and is the
// preferred one — a header gives it the ~44px it needs to read. `icon` is the
// original lucide glyph in a dark chip, kept for pages that have no artwork
// and as the fallback while artwork is rolled out page by page. Passing `art`
// wins if both are given.
export function PageHeader({
    icon: Icon,
    art,
    title,
    description,
    actions,
    className,
}: {
    icon?: LucideIcon
    art?: PageArtName
    title: ReactNode
    description?: ReactNode
    actions?: ReactNode
    className?: string
}) {
    return (
        <div className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
            <div className="flex items-start gap-3">
                {art ? (
                    <Art name={art} className="ui-artwork size-11 shrink-0" />
                ) : (
                    Icon && (
                        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-neutral-900 text-white ring-1 ring-black/5 dark:bg-neutral-800 dark:ring-white/10">
                            <Icon className="h-5 w-5" strokeWidth={1.75} />
                        </span>
                    )
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
