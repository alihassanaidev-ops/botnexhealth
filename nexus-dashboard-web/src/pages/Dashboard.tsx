import { useEffect, useState, useCallback } from "react"
import { Link } from "react-router-dom"
import {
    UserCog,
    CalendarCheck,
    Armchair,
    Clock,
    RefreshCcw,
    ArrowRight,
    CheckCircle2,
    Circle,
    MapPin,
    Zap,
} from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { toast } from "sonner"
import { useAuth } from "@/context/AuthContext"
import type { SetupOverview } from "@/types"
import { getSetupOverview, triggerSync } from "@/lib/tenant-api"

// ── Setup Steps Definition ──────────────────────────────────────────────

interface SetupStep {
    label: string
    description: string
    countKey: string
    link: string
    icon: React.ElementType
}

const SETUP_STEPS: SetupStep[] = [
    {
        label: "Providers",
        description: "Sync your dental providers from your PMS",
        countKey: "providers",
        link: "/setup/providers",
        icon: UserCog,
    },
    {
        label: "Appointment Types",
        description: "Configure the services your practice offers",
        countKey: "appointment_types",
        link: "/setup/appointment-types",
        icon: CalendarCheck,
    },
    {
        label: "Operatories",
        description: "Import rooms and chairs from your PMS",
        countKey: "operatories",
        link: "/setup/operatories",
        icon: Armchair,
    },
    {
        label: "Availabilities",
        description: "Link appointment types to provider schedules",
        countKey: "availabilities",
        link: "/setup/providers",
        icon: Clock,
    },
]

// ── Dashboard Page ──────────────────────────────────────────────────────

export default function Dashboard() {
    const { user } = useAuth()
    const [overview, setOverview] = useState<SetupOverview | null>(null)
    const [loading, setLoading] = useState(true)
    const [syncing, setSyncing] = useState(false)

    const fetchOverview = useCallback(async () => {
        try {
            const data = await getSetupOverview()
            setOverview(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load overview"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchOverview()
    }, [fetchOverview])

    const handleSync = async () => {
        setSyncing(true)
        try {
            const result = await triggerSync()
            if (result.success) {
                toast.success(
                    `Synced: ${result.providers_synced ?? 0} providers, ${result.appointment_types_synced ?? 0} appt types, ${result.operatories_synced ?? 0} operatories`
                )
                await fetchOverview()
            } else {
                toast.error(`Sync had errors: ${result.errors?.join(", ") ?? "Unknown error"}`)
            }
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Sync failed"
            toast.error(message)
        } finally {
            setSyncing(false)
        }
    }

    const completedSteps = overview
        ? SETUP_STEPS.filter((s) => (overview.counts[s.countKey] ?? 0) > 0).length
        : 0
    const progressPercent = (completedSteps / SETUP_STEPS.length) * 100

    // Greeting based on time
    const hour = new Date().getHours()
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"

    return (
        <div className="flex-1 space-y-6 p-8 pt-6">
            {/* Page Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">
                        {greeting}{user?.email ? `, ${user.email.split("@")[0]}` : ""}
                    </h2>
                    <p className="text-muted-foreground">
                        Here's an overview of your practice setup.
                    </p>
                </div>
                <Button
                    variant="outline"
                    onClick={handleSync}
                    disabled={syncing || loading}
                    className="gap-2"
                >
                    <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                    {syncing ? "Syncing…" : "Sync from PMS"}
                </Button>
            </div>

            {/* Location + PMS Info Bar */}
            {loading ? (
                <Skeleton className="h-12 w-full rounded-lg" />
            ) : overview ? (
                <div className="flex items-center gap-4 rounded-lg border bg-card px-5 py-3">
                    <div className="flex items-center gap-2 text-sm">
                        <MapPin className="h-4 w-4 text-muted-foreground" />
                        <span className="font-medium">{overview.location.name}</span>
                        <span className="text-muted-foreground font-mono text-xs">
                            ({overview.location.slug})
                        </span>
                    </div>
                    <Separator orientation="vertical" className="h-5" />
                    {overview.pms_source && (
                        <Badge variant="secondary" className="gap-1.5">
                            <Zap className="h-3 w-3" />
                            {overview.pms_source}
                        </Badge>
                    )}
                    {overview.can_create_appointment_types && (
                        <Badge variant="outline" className="text-xs">Create Appt Types</Badge>
                    )}
                    {overview.can_link_availability && (
                        <Badge variant="outline" className="text-xs">Link Availabilities</Badge>
                    )}
                </div>
            ) : null}

            {/* Stats Cards Grid */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                {SETUP_STEPS.map((step) => {
                    const StepIcon = step.icon
                    const count = overview?.counts[step.countKey] ?? 0
                    const isComplete = count > 0
                    return (
                        <Card key={step.countKey} className="relative overflow-hidden">
                            {loading ? (
                                <CardContent className="p-6 space-y-3">
                                    <Skeleton className="h-4 w-24" />
                                    <Skeleton className="h-8 w-16" />
                                    <Skeleton className="h-3 w-32" />
                                </CardContent>
                            ) : (
                                <>
                                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                        <CardTitle className="text-sm font-medium">
                                            {step.label}
                                        </CardTitle>
                                        <div className={`rounded-md p-2 ${isComplete ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}>
                                            <StepIcon className="h-4 w-4" />
                                        </div>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="text-3xl font-bold tabular-nums">
                                            {count}
                                        </div>
                                        <p className="text-xs text-muted-foreground mt-1">
                                            {isComplete
                                                ? `${count} ${step.label.toLowerCase()} configured`
                                                : "Not configured yet"}
                                        </p>
                                        {/* Subtle status bar at bottom */}
                                        <div className={`absolute bottom-0 left-0 right-0 h-0.5 ${isComplete ? "bg-primary" : "bg-muted"}`} />
                                    </CardContent>
                                </>
                            )}
                        </Card>
                    )
                })}
            </div>

            {/* Bottom Row: Setup Checklist + Quick Actions */}
            <div className="grid gap-6 lg:grid-cols-7">
                {/* Setup Progress — takes up 4 cols */}
                <Card className="lg:col-span-4">
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle>Setup Progress</CardTitle>
                                <CardDescription>
                                    Complete all steps to enable online booking.
                                </CardDescription>
                            </div>
                            {!loading && (
                                <span className="text-2xl font-bold tabular-nums text-primary">
                                    {completedSteps}/{SETUP_STEPS.length}
                                </span>
                            )}
                        </div>
                        {loading ? (
                            <Skeleton className="h-2 w-full mt-2" />
                        ) : (
                            <Progress value={progressPercent} className="mt-2" />
                        )}
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-4">
                            {SETUP_STEPS.map((step) => {
                                const count = overview?.counts[step.countKey] ?? 0
                                const done = count > 0
                                return (
                                    <div
                                        key={step.countKey}
                                        className="flex items-center gap-3"
                                    >
                                        {loading ? (
                                            <>
                                                <Skeleton className="h-5 w-5 rounded-full" />
                                                <div className="flex-1 space-y-1">
                                                    <Skeleton className="h-4 w-32" />
                                                    <Skeleton className="h-3 w-48" />
                                                </div>
                                            </>
                                        ) : (
                                            <>
                                                {done ? (
                                                    <CheckCircle2 className="h-5 w-5 text-primary shrink-0" />
                                                ) : (
                                                    <Circle className="h-5 w-5 text-muted-foreground/40 shrink-0" />
                                                )}
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center gap-2">
                                                        <span className={`text-sm font-medium ${done ? "" : "text-muted-foreground"}`}>
                                                            {step.label}
                                                        </span>
                                                        {done && (
                                                            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                                                                {count}
                                                            </Badge>
                                                        )}
                                                    </div>
                                                    <p className="text-xs text-muted-foreground truncate">
                                                        {step.description}
                                                    </p>
                                                </div>
                                                <Link to={step.link}>
                                                    <Button
                                                        variant={done ? "ghost" : "outline"}
                                                        size="sm"
                                                        className="shrink-0 gap-1"
                                                    >
                                                        {done ? "View" : "Set up"}
                                                        <ArrowRight className="h-3 w-3" />
                                                    </Button>
                                                </Link>
                                            </>
                                        )}
                                    </div>
                                )
                            })}
                        </div>
                    </CardContent>
                </Card>

                {/* Quick Actions — takes up 3 cols */}
                <Card className="lg:col-span-3">
                    <CardHeader>
                        <CardTitle>Quick Actions</CardTitle>
                        <CardDescription>
                            Jump to common tasks.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <Link to="/setup/appointment-types" className="block">
                            <div className="group flex items-center gap-3 rounded-lg border border-border/60 p-4 transition-all hover:border-border hover:bg-muted/50 hover:shadow-sm">
                                <div className="rounded-md bg-primary/10 p-2.5 text-primary group-hover:bg-primary/15 transition-colors">
                                    <CalendarCheck className="h-5 w-5" />
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">Manage Appointment Types</p>
                                    <p className="text-xs text-muted-foreground">
                                        Create, edit, or remove appointment types
                                    </p>
                                </div>
                                <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                            </div>
                        </Link>

                        <Link to="/setup/providers" className="block">
                            <div className="group flex items-center gap-3 rounded-lg border border-border/60 p-4 transition-all hover:border-border hover:bg-muted/50 hover:shadow-sm">
                                <div className="rounded-md bg-primary/10 p-2.5 text-primary group-hover:bg-primary/15 transition-colors">
                                    <UserCog className="h-5 w-5" />
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">Provider Scheduling</p>
                                    <p className="text-xs text-muted-foreground">
                                        Link appointment types to provider availabilities
                                    </p>
                                </div>
                                <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                            </div>
                        </Link>

                        <Link to="/setup/operatories" className="block">
                            <div className="group flex items-center gap-3 rounded-lg border border-border/60 p-4 transition-all hover:border-border hover:bg-muted/50 hover:shadow-sm">
                                <div className="rounded-md bg-primary/10 p-2.5 text-primary group-hover:bg-primary/15 transition-colors">
                                    <Armchair className="h-5 w-5" />
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">View Operatories</p>
                                    <p className="text-xs text-muted-foreground">
                                        Rooms and chairs synced from your PMS
                                    </p>
                                </div>
                                <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                            </div>
                        </Link>
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}
