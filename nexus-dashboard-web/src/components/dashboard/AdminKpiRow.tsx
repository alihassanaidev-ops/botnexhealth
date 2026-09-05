import { Link } from "react-router-dom"

import type { PageArtName } from "@/assets/icons"
import { Art } from "@/components/Art"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

interface Kpi {
    label: string
    value: string
    /** What the number means, or what to do about it. */
    hint: string
    art: PageArtName
    /** The list this number came from. A count you cannot open is a dead end. */
    to?: string
    /** Draw attention only when the number is one somebody must act on. */
    attention?: boolean
}

/**
 * The numbers an admin can act on, above the charts.
 *
 * The page previously led with activity counts — calls today, calls this month —
 * which say what happened but never what to do. These are chosen the other way
 * round: each one is either a queue somebody has to clear or an outcome worth
 * defending, and each links to the list behind it.
 *
 * Every value here was already being served and simply not shown.
 */
export function AdminKpiRow({
    bookingRate,
    emergencyCalls,
    needsCallback,
    needsBooking,
    showNeedsBooking,
    loading = false,
}: {
    bookingRate: number | undefined
    emergencyCalls: number | undefined
    needsCallback: number | undefined
    needsBooking: number | undefined
    /** Only a practice booking by hand has a manual-booking queue to clear. */
    showNeedsBooking: boolean
    loading?: boolean
}) {
    const kpis: Kpi[] = [
        {
            label: "Booking rate",
            value: `${Math.round(bookingRate ?? 0)}%`,
            hint: "Calls this month that ended in a booking",
            art: "bookingRate",
        },
        {
            label: "Emergency calls",
            value: String(emergencyCalls ?? 0),
            hint: "Flagged urgent this month",
            art: "emergencyCalls",
            to: "/calls",
            attention: (emergencyCalls ?? 0) > 0,
        },
        {
            label: "Awaiting callback",
            value: String(needsCallback ?? 0),
            hint: "Callers who asked to be rung back",
            art: "awaitingCallback",
            to: "/callbacks",
            attention: (needsCallback ?? 0) > 0,
        },
    ]

    if (showNeedsBooking) {
        kpis.push({
            label: "To book manually",
            value: String(needsBooking ?? 0),
            hint: "Appointment requests still to enter",
            art: "manualBooking",
            to: "/calls",
            attention: (needsBooking ?? 0) > 0,
        })
    }

    if (loading) {
        return (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {kpis.map((k) => (
                    <Card key={k.label} className="border-border shadow-sm">
                        <CardContent className="p-4">
                            <Skeleton className="h-3 w-24" />
                            <Skeleton className="mt-3 h-7 w-16" />
                            <Skeleton className="mt-2 h-3 w-32" />
                        </CardContent>
                    </Card>
                ))}
            </div>
        )
    }

    return (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {kpis.map((kpi) => {
                const body = (
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-medium text-muted-foreground">
                                {kpi.label}
                            </span>
                            <Art
                                name={kpi.art}
                                className={cn(
                                    "ui-artwork size-10 shrink-0 xl:size-11 2xl:size-12",
                                    // Repoint only the accent: the drawing keeps its
                                    // ink and surface and picks up the warning hue
                                    // where it was coloured, rather than turning into
                                    // a flat amber silhouette.
                                    kpi.attention && "[--art-accent:hsl(var(--warning))]",
                                )}
                            />
                        </div>
                        <div className="mt-2 text-2xl font-semibold tabular-nums">{kpi.value}</div>
                        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{kpi.hint}</p>
                    </CardContent>
                )
                return kpi.to ? (
                    <Link key={kpi.label} to={kpi.to} className="block">
                        <Card className="border-border shadow-sm transition-colors hover:bg-muted/40">
                            {body}
                        </Card>
                    </Link>
                ) : (
                    <Card key={kpi.label} className="border-border shadow-sm">
                        {body}
                    </Card>
                )
            })}
        </div>
    )
}
