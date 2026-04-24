import { useCallback, useEffect, useState, type ElementType } from "react";
import { Link } from "react-router-dom";
import {
    Armchair,
    CalendarCheck2,
    CheckCircle2,
    ClipboardList,
    RefreshCcw,
    Stethoscope,
    TimerReset,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getSetupOverview, listLocations, triggerSync } from "@/lib/tenant-api";
import type { LocationInfo, SetupOverview as SetupOverviewData } from "@/types";
import { useAuth } from "@/context/AuthContext";

type StepDefinition = {
    key: string;
    title: string;
    description: string;
    href?: string;
    ctaLabel: string;
    countLabel: string;
    count: number;
    complete: boolean;
    icon: ElementType;
};

const EMPTY_COUNTS = {
    providers: 0,
    appointment_types: 0,
    operatories: 0,
    descriptors: 0,
};

export default function SetupOverview() {
    const { user } = useAuth();
    const canSync = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN";
    const [locations, setLocations] = useState<LocationInfo[]>([]);
    const [selectedLocationId, setSelectedLocationId] = useState<string>("");
    const [overview, setOverview] = useState<SetupOverviewData | null>(null);
    const [loadingLocations, setLoadingLocations] = useState(true);
    const [loadingOverview, setLoadingOverview] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const loadOverview = useCallback(async (locationId: string) => {
        setLoadingOverview(true);
        try {
            const data = await getSetupOverview(locationId);
            setOverview(data);
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load setup overview";
            toast.error(message);
        } finally {
            setLoadingOverview(false);
        }
    }, []);

    useEffect(() => {
        let active = true;

        const loadLocations = async () => {
            setLoadingLocations(true);
            try {
                const data = await listLocations();
                if (!active) {
                    return;
                }

                setLocations(data);
                if (data.length === 0) {
                    setSelectedLocationId("");
                    setOverview(null);
                    return;
                }

                setSelectedLocationId((current) => current || data[0].id);
            } catch (error: unknown) {
                if (!active) {
                    return;
                }
                const message = error instanceof Error ? error.message : "Failed to load locations";
                toast.error(message);
            } finally {
                if (active) {
                    setLoadingLocations(false);
                }
            }
        };

        void loadLocations();

        return () => {
            active = false;
        };
    }, []);

    useEffect(() => {
        if (!selectedLocationId) {
            return;
        }
        void loadOverview(selectedLocationId);
    }, [loadOverview, selectedLocationId]);

    const handleSync = useCallback(async () => {
        if (!canSync || !selectedLocationId) {
            return;
        }

        setSyncing(true);
        try {
            const result = await triggerSync(selectedLocationId);
            if (!result.success) {
                toast.error(`Sync completed with errors: ${result.errors.join(", ")}`);
                return;
            }

            toast.success(
                `Sync complete: ${result.providers_synced} providers, ${result.appointment_types_synced} appointment types, ${result.operatories_synced} operatories`,
            );
            await loadOverview(selectedLocationId);
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Sync failed";
            toast.error(message);
        } finally {
            setSyncing(false);
        }
    }, [canSync, loadOverview, selectedLocationId]);

    const counts = {
        ...EMPTY_COUNTS,
        ...(overview?.counts ?? {}),
    };
    const totalSyncedRecords = Object.values(counts).reduce((total, value) => total + value, 0);
    const setupSteps: StepDefinition[] = [
        {
            key: "sync",
            title: "Run your first PMS sync",
            description: "Pull providers, appointment types, operatories, and descriptors into the setup cache.",
            ctaLabel: canSync ? "Sync now" : "Await sync",
            countLabel: "records cached",
            count: totalSyncedRecords,
            complete: totalSyncedRecords > 0,
            icon: TimerReset,
        },
        {
            key: "appointment-types",
            title: "Review appointment types",
            description: "Confirm durations and descriptor mappings before opening scheduling to staff.",
            href: "/setup/appointment-types",
            ctaLabel: "Open appointment types",
            countLabel: "appointment types",
            count: counts.appointment_types,
            complete: counts.appointment_types > 0,
            icon: CalendarCheck2,
        },
        {
            key: "providers",
            title: "Configure providers",
            description: "Check provider buffers, same-day cutoffs, and availability links.",
            href: "/setup/providers",
            ctaLabel: "Open providers",
            countLabel: "providers",
            count: counts.providers,
            complete: counts.providers > 0,
            icon: Stethoscope,
        },
        {
            key: "operatories",
            title: "Verify operatories",
            description: "Make sure chairs and rooms are present before linking availability windows.",
            href: "/setup/operatories",
            ctaLabel: "Open operatories",
            countLabel: "operatories",
            count: counts.operatories,
            complete: counts.operatories > 0,
            icon: Armchair,
        },
    ];

    const completedSteps = setupSteps.filter((step) => step.complete).length;
    const completionPercent = Math.round((completedSteps / setupSteps.length) * 100);
    const canCreateAppointmentTypes = overview?.can_create_appointment_types ?? false;
    const canLinkAvailability = overview?.can_link_availability ?? false;

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 pointer-events-none overflow-hidden">
                <div className="absolute -top-32 right-[-6rem] h-[420px] w-[420px] rounded-full bg-transparent blur-[100px] dark:bg-violet-700/20" />
            </div>

            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="space-y-3">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <ClipboardList className="h-4 w-4" />
                        Practice setup
                    </div>
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">First-clinic setup overview</h1>
                        <p className="max-w-2xl text-muted-foreground">
                            Use this page to track what has already synced and move through the setup steps in order.
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary" className="border border-border bg-background">
                            {completedSteps}/{setupSteps.length} steps complete
                        </Badge>
                        <Badge
                            variant="secondary"
                            className={canCreateAppointmentTypes ? "border border-emerald-200 bg-emerald-50 text-emerald-700" : "border border-border bg-background"}
                        >
                            {canCreateAppointmentTypes ? "PMS supports appointment type creation" : "Appointment types are PMS-managed"}
                        </Badge>
                        <Badge
                            variant="secondary"
                            className={canLinkAvailability ? "border border-sky-200 bg-sky-50 text-sky-700" : "border border-border bg-background"}
                        >
                            {canLinkAvailability ? "Availability linking enabled" : "Availability linking limited"}
                        </Badge>
                    </div>
                </div>

                <div className="flex flex-col gap-3 rounded-xl border border-border bg-background/80 p-4 shadow-sm lg:min-w-[320px]">
                    <div className="space-y-2">
                        <div className="text-sm font-medium">Working location</div>
                        <Select
                            value={selectedLocationId}
                            onValueChange={setSelectedLocationId}
                            disabled={loadingLocations || locations.length === 0}
                        >
                            <SelectTrigger>
                                <SelectValue placeholder="Select a location" />
                            </SelectTrigger>
                            <SelectContent>
                                {locations.map((location) => (
                                    <SelectItem key={location.id} value={location.id}>
                                        {location.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <Button variant="outline" onClick={() => void handleSync()} disabled={!canSync || syncing || !selectedLocationId}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                        {syncing ? "Syncing..." : "Run sync"}
                    </Button>
                </div>
            </div>

            <Card className="border-border/80 bg-background/80 shadow-sm">
                <CardHeader>
                    <CardTitle>Completion progress</CardTitle>
                    <CardDescription>
                        {overview?.location.name ?? "No location selected"} is {completionPercent}% through the setup checklist.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <Progress value={completionPercent} className="h-3" />
                    <div className="grid gap-4 md:grid-cols-4">
                        <div className="rounded-lg border border-border bg-muted/30 p-4">
                            <div className="text-sm text-muted-foreground">Providers</div>
                            <div className="mt-2 text-2xl font-semibold">{counts.providers}</div>
                        </div>
                        <div className="rounded-lg border border-border bg-muted/30 p-4">
                            <div className="text-sm text-muted-foreground">Appointment types</div>
                            <div className="mt-2 text-2xl font-semibold">{counts.appointment_types}</div>
                        </div>
                        <div className="rounded-lg border border-border bg-muted/30 p-4">
                            <div className="text-sm text-muted-foreground">Operatories</div>
                            <div className="mt-2 text-2xl font-semibold">{counts.operatories}</div>
                        </div>
                        <div className="rounded-lg border border-border bg-muted/30 p-4">
                            <div className="text-sm text-muted-foreground">Descriptors</div>
                            <div className="mt-2 text-2xl font-semibold">{counts.descriptors}</div>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
                <div className="grid gap-4 md:grid-cols-2">
                    {setupSteps.map((step) => {
                        const Icon = step.icon;

                        return (
                            <Card key={step.key} className="border-border/80 bg-background/80 shadow-sm">
                                <CardHeader className="space-y-3">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="flex items-center gap-3">
                                            <div className="rounded-lg border border-border bg-muted/40 p-2">
                                                <Icon className="h-5 w-5" />
                                            </div>
                                            <div>
                                                <CardTitle className="text-lg">{step.title}</CardTitle>
                                                <CardDescription>{step.description}</CardDescription>
                                            </div>
                                        </div>
                                        {step.complete && <CheckCircle2 className="h-5 w-5 text-emerald-600" />}
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-4">
                                    <div className="flex items-baseline justify-between rounded-lg border border-border bg-muted/20 px-4 py-3">
                                        <div className="text-sm text-muted-foreground">{step.countLabel}</div>
                                        <div className="text-2xl font-semibold">{step.count}</div>
                                    </div>

                                    {step.href ? (
                                        <Button asChild className="w-full">
                                            <Link to={step.href}>{step.ctaLabel}</Link>
                                        </Button>
                                    ) : (
                                        <Button className="w-full" onClick={() => void handleSync()} disabled={!canSync || syncing || !selectedLocationId}>
                                            <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                                            {step.ctaLabel}
                                        </Button>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>

                <div className="space-y-4">
                    <Card className="border-border/80 bg-background/80 shadow-sm">
                        <CardHeader>
                            <CardTitle>Next actions</CardTitle>
                            <CardDescription>
                                Finish the core sync-backed setup first, then review these final configuration areas.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <Button asChild variant="outline" className="w-full justify-between">
                                <Link to="/setup/insurance-plans">Review insurance plans</Link>
                            </Button>
                            <Button asChild variant="outline" className="w-full justify-between">
                                <Link to="/setup/audit-logs">Review audit logs</Link>
                            </Button>
                        </CardContent>
                    </Card>

                    <Card className="border-border/80 bg-background/80 shadow-sm">
                        <CardHeader>
                            <CardTitle>Status notes</CardTitle>
                            <CardDescription>
                                This page reads from cached setup data so you can see readiness without hitting PMS APIs on every screen.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3 text-sm text-muted-foreground">
                            <p>
                                {loadingOverview
                                    ? "Refreshing setup counts..."
                                    : overview
                                        ? `The current view is scoped to ${overview.location.name}.`
                                        : "Select a location to begin."}
                            </p>
                            <p>
                                Run a fresh sync after major PMS-side changes so provider, appointment type, and operatory counts stay current.
                            </p>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
