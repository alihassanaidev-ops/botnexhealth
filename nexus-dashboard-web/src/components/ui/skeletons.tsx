import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

// Composite loading skeletons that mirror the real layout of each surface, so
// loading states no longer flash a bare spinner. Prefer these over Loader2 for
// content/page/section loads; keep spinners only for in-flight button actions.

/** Rows of a table/list. Widths vary per column to feel organic. */
export function TableSkeleton({ rows = 6, cols = 4, className }: { rows?: number; cols?: number; className?: string }) {
    const widths = ["w-3/4", "w-1/2", "w-2/3", "w-1/3", "w-4/5", "w-1/2"]
    return (
        <div className={cn("w-full divide-y divide-border/60", className)} role="status" aria-label="Loading">
            {Array.from({ length: rows }).map((_, r) => (
                <div key={r} className="flex items-center gap-4 px-4 py-3.5">
                    {Array.from({ length: cols }).map((_, c) => (
                        <Skeleton key={c} className={cn("h-4", c === 0 ? "w-40 shrink-0" : `flex-1 ${widths[(r + c) % widths.length]}`)} />
                    ))}
                </div>
            ))}
        </div>
    )
}

/** A grid of cards (e.g. tenants, providers, templates). */
export function CardsSkeleton({ count = 6, className }: { count?: number; className?: string }) {
    return (
        <div className={cn("grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3", className)} role="status" aria-label="Loading">
            {Array.from({ length: count }).map((_, i) => (
                <div key={i} className="space-y-3 rounded-xl border bg-card p-5">
                    <div className="flex items-center gap-3">
                        <Skeleton className="h-9 w-9 rounded-full" />
                        <div className="flex-1 space-y-1.5">
                            <Skeleton className="h-4 w-2/3" />
                            <Skeleton className="h-3 w-1/3" />
                        </div>
                    </div>
                    <Skeleton className="h-3 w-full" />
                    <Skeleton className="h-3 w-4/5" />
                </div>
            ))}
        </div>
    )
}

/** Label + field pairs for settings/forms. */
export function FormSkeleton({ rows = 5, className }: { rows?: number; className?: string }) {
    return (
        <div className={cn("space-y-6", className)} role="status" aria-label="Loading">
            {Array.from({ length: rows }).map((_, i) => (
                <div key={i} className="space-y-2">
                    <Skeleton className="h-3.5 w-32" />
                    <Skeleton className="h-9 w-full max-w-md" />
                </div>
            ))}
        </div>
    )
}

/** Chart placeholder: a framed area with a faint bar silhouette. */
export function ChartSkeleton({ className, height = 260 }: { className?: string; height?: number }) {
    return (
        <div className={cn("flex w-full items-end gap-2 rounded-lg border bg-card p-4", className)} style={{ height }} role="status" aria-label="Loading chart">
            {[45, 70, 55, 85, 60, 75, 40, 65, 80, 50, 68, 58].map((h, i) => (
                <Skeleton key={i} className="flex-1 rounded-sm" style={{ height: `${h}%` }} />
            ))}
        </div>
    )
}

/** Generic centered page/section fallback (header + a few lines). */
export function PageSkeleton({ className }: { className?: string }) {
    return (
        <div className={cn("space-y-4 p-6", className)} role="status" aria-label="Loading">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-72" />
            <div className="space-y-3 pt-2">
                <Skeleton className="h-16 w-full rounded-xl" />
                <Skeleton className="h-16 w-full rounded-xl" />
                <Skeleton className="h-16 w-full rounded-xl" />
            </div>
        </div>
    )
}
