import { useCallback, useEffect, useState, type ElementType } from "react";
import { Link } from "react-router-dom";
import {
    Armchair,
    CalendarCheck2,
    CheckCircle2,
    ClipboardList,
    Info,
    RefreshCcw,
    Stethoscope,
    TimerReset,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { getSetupOverview, triggerSync } from "@/lib/tenant-api";
import type { SetupOverview as SetupOverviewData } from "@/types";
import { useAuth } from "@/context/AuthContext";
import { useLocationContext } from "@/context/LocationContext";

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
    const {
        locations,
        selectedLocationId: ctxLocationId,
        setSelectedLocationId,
        isLoading: loadingLocations,
    } = useLocationContext();
    const canSync = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN";
    const selectedLocationId = ctxLocationId ?? "";
    const [overview, setOverview] = useState<SetupOverviewData | null>(null);
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
        if (!selectedLocationId) {
            setOverview(null);
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

    const locationName = overview?.location.name ?? null;

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            {/* Header */}
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <ClipboardList className="h-4 w-4" />
                        Practice setup
                    </div>
                    <h1 className="mt-1 text-2xl font-bold tracking-tight">Setup overview</h1>
                    <p className="text-sm text-muted-foreground">Track what's synced and finish setup in order.</p>
                </div>
                <div className="flex items-center gap-2">
                    <Select
                        value={selectedLocationId}
                        onValueChange={setSelectedLocationId}
                        disabled={loadingLocations || locations.length === 0}
                    >
                        <SelectTrigger className="h-9 w-[200px]">
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
                    <Button onClick={() => void handleSync()} disabled={!canSync || syncing || !selectedLocationId}>
                        <RefreshCcw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
                        {syncing ? "Syncing..." : "Run sync"}
                    </Button>
                </div>
            </div>

            {/* Progress — single bar; capability + status notes tucked behind the info icon */}
            <Card>
                <CardContent className="p-5">
                    <div className="flex items-center justify-between gap-4">
                        <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                                <span className="text-sm font-medium">
                                    {locationName ? `${locationName} readiness` : "Setup readiness"}
                                </span>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <button type="button" className="text-muted-foreground/60 transition-colors hover:text-muted-foreground" aria-label="Setup details">
                                            <Info className="h-3.5 w-3.5" />
                                        </button>
                                    </TooltipTrigger>
                                    <TooltipContent className="max-w-xs space-y-1.5 text-xs">
                                        <p>{canCreateAppointmentTypes ? "Your PMS supports creating appointment types here." : "Appointment types are managed in your PMS."}</p>
                                        <p>{canLinkAvailability ? "Availability linking is enabled." : "Availability linking is limited for this PMS."}</p>
                                        <p className="text-muted-foreground">Reads cached setup data — run a sync after major PMS-side changes.</p>
                                    </TooltipContent>
                                </Tooltip>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                {completedSteps} of {setupSteps.length} steps complete
                            </p>
                        </div>
                        <span className="text-2xl font-bold tabular-nums">{completionPercent}%</span>
                    </div>
                    <Progress value={completionPercent} className="mt-3 h-2" />
                </CardContent>
            </Card>

            {/* Checklist */}
            <Card className={loadingOverview ? "opacity-60 transition-opacity" : "transition-opacity"}>
                <CardContent className="divide-y divide-border p-0">
                    {setupSteps.map((step) => {
                        const Icon = step.icon;
                        return (
                            <div key={step.key} className="flex items-center gap-4 px-5 py-4">
                                <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-foreground shadow-sm">
                                    <Icon className="size-5 text-background" />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-1.5">
                                        <span className="font-medium">{step.title}</span>
                                        {step.complete && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />}
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <button type="button" className="text-muted-foreground/60 transition-colors hover:text-muted-foreground" aria-label={`About ${step.title}`}>
                                                    <Info className="h-3.5 w-3.5" />
                                                </button>
                                            </TooltipTrigger>
                                            <TooltipContent className="max-w-xs text-xs">{step.description}</TooltipContent>
                                        </Tooltip>
                                    </div>
                                    <p className="text-xs tabular-nums text-muted-foreground">
                                        {step.count} {step.countLabel}
                                    </p>
                                </div>
                                {step.href ? (
                                    <Button asChild size="sm" variant={step.complete ? "outline" : "default"}>
                                        <Link to={step.href}>{step.ctaLabel}</Link>
                                    </Button>
                                ) : (
                                    <Button
                                        size="sm"
                                        variant={step.complete ? "outline" : "default"}
                                        onClick={() => void handleSync()}
                                        disabled={!canSync || syncing || !selectedLocationId}
                                    >
                                        <RefreshCcw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
                                        {step.ctaLabel}
                                    </Button>
                                )}
                            </div>
                        );
                    })}
                </CardContent>
            </Card>

            {/* Secondary config */}
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">More configuration</span>
                <Button asChild variant="outline" size="sm">
                    <Link to="/setup/insurance-plans">Insurance plans</Link>
                </Button>
                <Button asChild variant="outline" size="sm">
                    <Link to="/setup/audit-logs">Audit logs</Link>
                </Button>
            </div>
        </div>
    );
}
